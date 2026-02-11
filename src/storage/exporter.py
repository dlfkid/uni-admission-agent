
import logging
from typing import Optional, List
import pandas as pd
from sqlmodel import select
from src.storage.db_manager import DatabaseManager
from src.models.admission import University, Program

logger = logging.getLogger(__name__)

class ExcelExporter:
    def __init__(self, output_path: str):
        self.output_path = output_path
        self.db_manager = DatabaseManager()

    def export_data(self, univ_slug: str, year: Optional[int] = None) -> None:
        """
        Export data for a university to Excel.
        If year is specified, export only that year.
        If year is None, export all history with 'Academic Year' column.
        """
        with self.db_manager.get_session() as session:
            # 1. Find University
            univ = session.exec(select(University).where(University.slug == univ_slug)).first()
            if not univ:
                logger.error(f"University not found: {univ_slug}")
                return

            # 2. Build Query
            query = select(Program).where(Program.university_id == univ.id)
            if year:
                query = query.where(Program.academic_year == year)
            
            # 3. Fetch Data
            programs = session.exec(query).all()
            if not programs:
                logger.warning(f"No programs found for {univ_slug} " + (f"({year})" if year else "(All years)"))
                return

            logger.info(f"Found {len(programs)} programs. Exporting...")

            # 4. Transform to DataFrame
            data_rows = []
            for p in programs:
                row = {
                    "University": univ.name,
                    "Academic Year": p.academic_year,
                    "Program Name (EN)": p.name_en,
                    "Program Name (ZH)": p.name_zh,
                    "Tuition": p.tuition_amount,
                    "Currency": p.currency,
                    "Tuition (Raw)": p.tuition_fee_raw,
                    "Duration (Raw)": p.duration_raw,
                    "Deadline (Raw)": p.deadline_raw,
                    "Updated At": p.updated_at
                }
                # Add extra metadata flattened? Or separate? 
                # For basic export, let's keep it clean. User might want details.
                # Let's add full JSON dumps for complex fields for now, easier to debug.
                row["Study Options (JSON)"] = str(p.study_options)
                row["Deadlines (JSON)"] = str(p.deadlines)
                
                data_rows.append(row)

            df = pd.DataFrame(data_rows)
            
            # 5. Write to Excel
            try:
                df.to_excel(self.output_path, index=False)
                logger.info(f"Successfully exported to {self.output_path}")
            except Exception as e:
                logger.error(f"Failed to write Excel: {e}")
