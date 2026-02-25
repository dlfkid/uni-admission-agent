
import json
import logging
from typing import Optional, List, IO, Union
import pandas as pd
from sqlmodel import select
from src.storage.db_manager import DatabaseManager
from src.models.admission import University, Program

logger = logging.getLogger(__name__)


def _format_deadlines(deadlines: list) -> str:
    """Convert deadline JSONB list into a human-readable string."""
    if not deadlines:
        return ""
    parts = []
    for d in deadlines:
        desc = d.get("description", "")
        date = d.get("cutoff_date", "")
        rnd = d.get("round")
        label = f"R{rnd}" if rnd else ""
        parts.append(" ".join(filter(None, [label, desc, date])))
    return " | ".join(parts)


def _format_study_options(options: list) -> str:
    """Convert study_options JSONB list into a human-readable string."""
    if not options:
        return ""
    parts = []
    for opt in options:
        mode = opt.get("mode", "")
        dur = opt.get("duration_months")
        s = mode
        if dur:
            s += f" ({dur}mo)"
        parts.append(s)
    return ", ".join(parts)


class ExcelExporter:
    def __init__(self, output_path: Optional[str] = None, output_stream: Optional[IO[bytes]] = None):
        """
        Args:
            output_path: File path to write the Excel file.
            output_stream: In-memory BytesIO buffer (for API streaming).
            Exactly one of output_path or output_stream should be provided.
        """
        self.output_path = output_path
        self.output_stream = output_stream
        self.db_manager = DatabaseManager()

    def export_data(self, univ_slug: str, year: Optional[int] = None) -> int:
        """
        Export data for a university to Excel.

        Returns:
            Number of programs exported.
        """
        with self.db_manager.get_session() as session:
            # 1. Find University
            univ = session.exec(select(University).where(University.slug == univ_slug)).first()
            if not univ:
                logger.error(f"University not found: {univ_slug}")
                return 0

            # 2. Build Query
            query = select(Program).where(Program.university_id == univ.id)
            if year:
                query = query.where(Program.academic_year == year)
            
            # 3. Fetch Data
            programs = session.exec(query).all()
            if not programs:
                logger.warning(f"No programs found for {univ_slug} " + (f"({year})" if year else "(All years)"))
                return 0

            logger.info(f"Found {len(programs)} programs. Exporting...")

            # 4. Transform to DataFrame
            data_rows = []
            for p in programs:
                row = {
                    "University": univ.name,
                    "Academic Year": p.academic_year,
                    "Program Name (EN)": p.name_en,
                    "Program Name (ZH)": p.name_zh or "",
                    "Group Code": p.program_group_code or "",
                    "Faculty": p.faculty or "",
                    "Tuition": float(p.tuition_amount) if p.tuition_amount else "",
                    "Currency": p.currency.value if p.currency else "",
                    "Study Options": _format_study_options(p.study_options),
                    "Deadlines": _format_deadlines(p.deadlines),
                    "Active": "Yes" if p.is_active else "No",
                    "Discontinued": "Yes" if p.is_discontinued else "No",
                    "Updated At": p.updated_at.strftime("%Y-%m-%d %H:%M") if p.updated_at else "",
                }

                # Flatten extra_metadata keys as additional columns
                if p.extra_metadata:
                    for k, v in p.extra_metadata.items():
                        col_name = f"Extra: {k}"
                        row[col_name] = str(v) if v is not None else ""

                data_rows.append(row)

            df = pd.DataFrame(data_rows)
            
            # 5. Write to Excel
            target = self.output_stream or self.output_path
            if not target:
                logger.error("No output target specified for ExcelExporter")
                return 0

            try:
                df.to_excel(target, index=False, engine="openpyxl")
                if self.output_path:
                    logger.info(f"Successfully exported to {self.output_path}")
                return len(data_rows)
            except Exception as e:
                logger.error(f"Failed to write Excel: {e}")
                return 0
