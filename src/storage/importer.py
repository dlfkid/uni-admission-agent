import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

import pandas as pd

from src.agents.cleaner_agent import LLMCleanerAgent, ParsedProgramData
from src.core.parser import DataCleaner
from src.storage.db_manager import DatabaseManager
from src.utils.pdf_processor import PDFProcessor
from src.utils.text import generate_program_group_code

logger = logging.getLogger(__name__)

class ExcelImporter:
    """
    Handles importing university admission data from XLSX files.
    Supports smart header detection and NaN handling.
    """
    
    # Columns that identify a header row
    HEADER_MARKERS = {"专业中文名", "专业英文名", "English Name", "Tuition Fee", "学费"}
    
    # Mapping Excel headers to DB columns
    COLUMN_MAP = {
        "专业中文名": "name_zh",
        "专业英文名": "name_en",
        "English Name": "name_en",
        "学费": "_tuition_raw",
        "Tuition Fee": "_tuition_raw",
        "学习时长": "_duration_raw",
        "Duration": "_duration_raw",
        "非本地人申请截止日期": "_deadline_raw",
        "Deadline": "_deadline_raw"
    }

    def __init__(self, file_path: str, use_llm: bool = False):
        self.file_path = Path(file_path)
        self.db_manager = DatabaseManager()
        self.use_llm = use_llm
        self.llm_agent = LLMCleanerAgent() if use_llm else None

    def import_data(self, univ_slug: str, year: int) -> None:
        """
        Main entry point to process a file for a specific university and year.
        Supports both Excel (.xlsx/.xls) and PDF (.pdf) files.
        """
        if not self.file_path.exists():
            logger.error(f"File not found: {self.file_path}")
            return

        # Route based on file type
        suffix = self.file_path.suffix.lower()
        if suffix == ".pdf":
            self._import_pdf(univ_slug, year)
            return

        # Default: Excel import
        xls = pd.ExcelFile(self.file_path)
        logger.info(f"Found {len(xls.sheet_names)} sheets in {self.file_path.name}")
        
        # Track stats
        total_sheets = len(xls.sheet_names)
        total_inserted = 0
        total_updated = 0
        
        for sheet_name in xls.sheet_names:
            logger.info(f"Processing sheet: {sheet_name}")
            try:
                inserted, updated = self._process_sheet(
                    xls, sheet_name, univ_slug, year
                )
                total_inserted += inserted
                total_updated += updated
            except Exception as e:
                logger.error(f"Error processing sheet '{sheet_name}': {e}")
        
        # Final Summary
        logger.info("\n" + "="*40)
        logger.info(f"Import Complete: {univ_slug.upper()} {year}")
        logger.info(f"Sheets Processed: {total_sheets}")
        logger.info(f"New Records:      {total_inserted}")
        logger.info(f"Updated Records:  {total_updated}")
        logger.info("="*40)

    def _import_pdf(self, univ_slug: str, year: int) -> None:
        if not self.llm_agent:
            logger.error("PDF import requires --llm flag. Aborting.")
            return

        processor = PDFProcessor()
        try:
            result = processor.convert_to_markdown(self.file_path)
        except Exception as e:
            logger.error(f"PDF conversion failed: {e}")
            return

        logger.info(
            f"PDF converted: {result.page_count} pages, "
            f"{result.char_count:,} chars"
        )

        raw_row: Dict[str, str] = {
            "source_file": str(self.file_path),
            "raw_content": result.markdown[:8000],
        }

        try:
            parsed: Optional[ParsedProgramData] = self.llm_agent.clean_row(raw_row)
            if parsed is None:
                logger.warning("No structured data extracted from PDF")
                return

            program_data: Dict[str, Any] = {"academic_year": year}

            if parsed.tuition:
                program_data["tuition_amount"] = parsed.tuition.amount
                program_data["currency"] = parsed.tuition.currency
            if parsed.study_options:
                program_data["study_options"] = [
                    opt.model_dump(mode="json") for opt in parsed.study_options
                ]
            if parsed.deadlines:
                sorted_deadlines = sorted(
                    parsed.deadlines, 
                    key=lambda x: x.cutoff_date or datetime.max
                )
                program_data["deadlines"] = []
                for i, d in enumerate(sorted_deadlines, 1):
                    d_dict = d.model_dump(mode="json")
                    d_dict["round"] = i
                    program_data["deadlines"].append(d_dict)

            program_data["extra_metadata"] = {
                "source_file": str(self.file_path),
                "pdf_pages": result.page_count,
            }

            _, created = self.db_manager.upsert_program(program_data, univ_slug)
            action = "Inserted" if created else "Updated"
            logger.info(f"{action} program from PDF: {self.file_path.name}")

        except Exception as e:
            logger.error(f"Failed to parse PDF content: {e}")

    def _process_sheet(
        self, xls: pd.ExcelFile, sheet_name: str, univ_slug: str, year: int,
    ) -> Tuple[int, int]:
        """
        Synchronous wrapper for async processing.
        Returns (inserted_count, updated_count)
        """
        return asyncio.run(self._process_sheet_async(
            xls, sheet_name, univ_slug, year
        ))

    async def _process_sheet_async(
        self, xls: pd.ExcelFile, sheet_name: str, univ_slug: str, year: int,
    ) -> Tuple[int, int]:
        # 1. Read roughly to find header
        try:
            df_preview = pd.read_excel(xls, sheet_name=sheet_name, header=None, nrows=20)
            header_idx = self._locate_header_index(df_preview)

            if header_idx is None:
                logger.warning(f"Skipping sheet '{sheet_name}': No recognized header found.")
                return 0, 0

            # 2. Read actual data
            df = pd.read_excel(xls, sheet_name=sheet_name, header=header_idx)

            # 3. Initial Parse & Collect Rows
            all_programs: List[Dict[str, Any]] = []
            rows_needing_llm: List[Dict[str, Any]] = []

            for _idx, row in df.iterrows():
                program_data = self._parse_row(row)
                if not program_data:
                    continue

                # Inject academic year and faculty
                program_data["academic_year"] = year
                program_data["faculty"] = sheet_name

                # --- Deterministic program_group_code (local, 0-latency) ---
                name_en = program_data.get("name_en", "")
                code = generate_program_group_code(univ_slug, str(name_en))
                program_data["program_group_code"] = code
                logger.info(
                    "[New Program] 检测到新学科: %s，已分配 ID: %s",
                    name_en, code,
                )

                # Check if LLM is needed (tuition / deadlines parsing)
                needs_llm = self._check_needs_llm(program_data)

                all_programs.append(program_data)
                if needs_llm:
                    rows_needing_llm.append(program_data)

            logger.info(
                f"Sheet '{sheet_name}': {len(all_programs)} valid rows. "
                f"{len(rows_needing_llm)} require LLM cleaning."
            )

            # 4. Batch Process Rows Needing LLM (field extraction only)
            if rows_needing_llm:
                await self._batch_process_llm(rows_needing_llm)

            # 5. Upsert All (strip processing-only keys before DB write)
            inserted_count = 0
            updated_count = 0
            for program_data in all_programs:
                # Remove intermediate raw keys that are not DB columns
                db_data = {
                    k: v for k, v in program_data.items()
                    if not k.startswith("_")
                }
                try:
                    _, created = self.db_manager.upsert_program(db_data, univ_slug)
                    if created:
                        inserted_count += 1
                    else:
                        updated_count += 1
                except Exception as e:
                    logger.error(f"DB Error upserting {program_data.get('name_en')}: {e}")

            return inserted_count, updated_count

        except Exception as e:
            logger.error(f"Error processing sheet '{sheet_name}': {e}")
            return 0, 0

    def _locate_header_index(self, df: pd.DataFrame) -> Optional[int]:
        for idx, row in df.iterrows():
            row_values = {str(v).strip() for v in row.values if pd.notna(v)}
            if len(row_values.intersection(self.HEADER_MARKERS)) > 0:
                if isinstance(idx, int):
                    return idx
        return None

    def _get_mapped_field(self, col_name: str) -> Optional[str]:
        col_clean = col_name.replace('\n', '')
        if col_clean in self.COLUMN_MAP:
            return self.COLUMN_MAP[col_clean]
        for key, field in self.COLUMN_MAP.items():
            if key in col_clean:
                return field
        return None

    def _parse_row(self, row: pd.Series) -> Optional[Dict[str, Any]]:
        data: Dict[str, Any] = {
            "extra_metadata": {}
        }
        for col, val in row.items():
            col_name = str(col).strip()
            if pd.isna(val) or val == "":
                continue
            field_key = self._get_mapped_field(col_name)
            if field_key:
                val_str = str(val).strip()
                data[field_key] = val_str
            else:
                data["extra_metadata"][col_name] = val
        
        if "name_en" not in data:
             return None

        if "name_zh" not in data:
            data["name_zh"] = ""
            
        if "_tuition_raw" in data:
            amount, currency = DataCleaner.parse_tuition(data["_tuition_raw"])
            if amount:
                data["tuition_amount"] = amount
                data["currency"] = currency

        if "_duration_raw" in data:
            options = DataCleaner.parse_study_options(data["_duration_raw"])
            if options:
                data["study_options"] = options
                
        if "_deadline_raw" in data:
            deadlines = DataCleaner.parse_deadlines(data["_deadline_raw"])
            if deadlines:
                data["deadlines"] = deadlines
                
        return data

    def _check_needs_llm(self, data: Dict[str, Any]) -> bool:
        """Check if row needs LLM cleaning."""
        if not self.use_llm:
            return False
            
        if data.get("_tuition_raw") and "tuition_amount" not in data:
            return True
        if data.get("_duration_raw") and "study_options" not in data:
            return True
        if data.get("_deadline_raw") and "deadlines" not in data:
            return True
        return False

    async def _batch_process_llm(self, rows: List[Dict[str, Any]]):
        """
        Process rows in batches of 5 with concurrency limit of 3.
        Updates the dictionaries in-place.
        """
        BATCH_SIZE = 5
        CONCURRENCY = 3
        
        sem = asyncio.Semaphore(CONCURRENCY)
        
        # Create chunks
        chunks = [rows[i:i + BATCH_SIZE] for i in range(0, len(rows), BATCH_SIZE)]
        
        async def process_chunk(chunk: List[Dict[str, Any]]):
            if not self.llm_agent:
                return

            async with sem:
                try:
                    # Prepare input for LLM (minimize tokens)
                    llm_inputs = []
                    for data in chunk:
                        inp: Dict[str, Any] = {
                            "Course Name": data.get("name_en"),
                            "Tuition Fee": data.get("_tuition_raw"),
                            "Duration": data.get("_duration_raw"),
                            "Deadlines": data.get("_deadline_raw"),
                        }
                        llm_inputs.append({k: v for k, v in inp.items() if v})

                    if not llm_inputs:
                        return

                    logger.info(f"Processing batch of {len(llm_inputs)} rows...")

                    loop = asyncio.get_event_loop()
                    results = await loop.run_in_executor(
                        None,
                        self.llm_agent.clean_batch,
                        llm_inputs,
                    )

                    # Merge results back (field extraction only)
                    if results and len(results) == len(chunk):
                        for data, res in zip(chunk, results):
                            if "tuition_amount" not in data and res.tuition:
                                data["tuition_amount"] = res.tuition.amount
                                data["currency"] = res.tuition.currency

                            if "study_options" not in data and res.study_options:
                                data["study_options"] = [
                                    opt.model_dump(mode="json") for opt in res.study_options
                                ]

                            if "deadlines" not in data and res.deadlines:
                                sorted_deadlines = sorted(
                                    res.deadlines,
                                    key=lambda x: x.cutoff_date or datetime.max,
                                )
                                data["deadlines"] = []
                                for i, d in enumerate(sorted_deadlines, 1):
                                    d_dict = d.model_dump(mode="json")
                                    d_dict["round"] = i
                                    data["deadlines"].append(d_dict)

                except Exception as e:
                    logger.error(f"Batch processing failed: {e}")

        # Run all chunks
        await asyncio.gather(*(process_chunk(chunk) for chunk in chunks))
