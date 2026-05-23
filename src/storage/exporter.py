import logging
from typing import Optional, IO
import pandas as pd
from sqlmodel import select, col, desc
from src.storage.db_manager import DatabaseManager
from src.models.admission import University, Program
from src.models.requirement import (
    ProgramStudyOption,
    ProgramDeadline,
    ProgramRequirement,
    SubjectDim,
    ExamDim,
    FrameworkDim,
    RequirementEvidence,
    RequirementVersion,
)

logger = logging.getLogger(__name__)


def _format_deadlines(deadlines: list) -> str:
    """Convert deadline JSON list into a human-readable string."""
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
    """Convert study_options JSON list into a human-readable string."""
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


def _format_requirements(requirements: list) -> str:
    if not requirements:
        return ""
    parts = []
    for req in requirements:
        subject = req.get("subject_name") or req.get("exam_name") or req.get("framework") or "Requirement"
        min_val = req.get("minimum_value") or ""
        unit = req.get("unit") or ""
        text = req.get("requirement_text") or ""
        scope = req.get("applicant_scope") or ""
        headline = " ".join(s for s in [subject, min_val, unit] if s).strip()
        tail = " | ".join(s for s in [text, scope] if s)
        parts.append(" - ".join(s for s in [headline, tail] if s))
    return " || ".join(parts)


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
            query = (
                select(Program)
                .where(Program.university_id == univ.id)
                .order_by(col(Program.academic_year).desc(), col(Program.name_en))
            )
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
                option_rows = session.exec(
                    select(ProgramStudyOption)
                    .where(ProgramStudyOption.program_id == p.id)
                    .order_by(col(ProgramStudyOption.id))
                ).all()
                deadline_rows = session.exec(
                    select(ProgramDeadline)
                    .where(ProgramDeadline.program_id == p.id)
                    .order_by(col(ProgramDeadline.cutoff_date), col(ProgramDeadline.id))
                ).all()
                latest_requirement_version = session.exec(
                    select(RequirementVersion)
                    .where(RequirementVersion.program_id == p.id)
                    .order_by(desc(col(RequirementVersion.version_no)))
                ).first()
                requirement_stmt = (
                    select(
                        ProgramRequirement,
                        SubjectDim,
                        ExamDim,
                        FrameworkDim,
                        RequirementEvidence,
                    )
                    .join(SubjectDim, SubjectDim.id == ProgramRequirement.subject_dim_id, isouter=True)
                    .join(ExamDim, ExamDim.id == ProgramRequirement.exam_dim_id, isouter=True)
                    .join(FrameworkDim, FrameworkDim.id == ProgramRequirement.framework_dim_id, isouter=True)
                    .join(RequirementEvidence, RequirementEvidence.id == ProgramRequirement.evidence_id, isouter=True)
                    .order_by(col(ProgramRequirement.sort_order), col(ProgramRequirement.id))
                )
                if latest_requirement_version and latest_requirement_version.id is not None:
                    requirement_stmt = requirement_stmt.where(
                        ProgramRequirement.version_id == latest_requirement_version.id
                    )
                else:
                    requirement_stmt = requirement_stmt.where(ProgramRequirement.program_id == p.id)
                requirement_rows = session.exec(requirement_stmt).all()

                study_options = (
                    [
                        {
                            "mode": opt.mode.value if opt.mode else "Unknown",
                            "duration_months": opt.duration_months,
                            "notes": opt.notes,
                        }
                        for opt in option_rows
                    ]
                    if option_rows
                    else (p.study_options or [])
                )

                deadlines = (
                    [
                        {
                            "round": d.round,
                            "description": d.description,
                            "cutoff_date": d.cutoff_date.isoformat() if d.cutoff_date else None,
                        }
                        for d in deadline_rows
                    ]
                    if deadline_rows
                    else (p.deadlines or [])
                )

                requirements = []
                for req, subject_dim, exam_dim, framework_dim, evidence in requirement_rows:
                    requirements.append(
                        {
                            "category": req.category.value if req.category else "other",
                            "subject_name": (
                                subject_dim.canonical_name
                                if subject_dim and subject_dim.canonical_name
                                else req.subject_name
                            ),
                            "framework": (
                                framework_dim.display_name
                                if framework_dim and framework_dim.display_name
                                else req.framework
                            ),
                            "exam_name": exam_dim.display_name if exam_dim else None,
                            "minimum_value": req.minimum_value,
                            "unit": req.unit,
                            "applicant_scope": req.applicant_scope,
                            "requirement_text": req.requirement_text,
                            "evidence_url": (
                                evidence.source_url
                                if evidence and evidence.source_url
                                else req.evidence_url
                            ),
                        }
                    )

                row = {
                    "University": univ.name,
                    "Academic Year": p.academic_year,
                    "Program Name (EN)": p.name_en,
                    "Program Name (ZH)": p.name_zh or "",
                    "Group Code": p.program_group_code or "",
                    "Faculty": p.faculty or "",
                    "Tuition": float(p.tuition_amount) if p.tuition_amount else "",
                    "Currency": p.currency.value if p.currency else "",
                    "Study Options": _format_study_options(study_options),
                    "Deadlines": _format_deadlines(deadlines),
                    "Subject Requirements": _format_requirements(requirements),
                    "Requirement Version": (
                        latest_requirement_version.version_no
                        if latest_requirement_version
                        else ""
                    ),
                    "Requirement Effective At": (
                        latest_requirement_version.effective_at.isoformat()
                        if latest_requirement_version and latest_requirement_version.effective_at
                        else ""
                    ),
                    "Requirement Valid From": (
                        latest_requirement_version.valid_from.isoformat()
                        if latest_requirement_version and latest_requirement_version.valid_from
                        else ""
                    ),
                    "Requirement Valid To": (
                        latest_requirement_version.valid_to.isoformat()
                        if latest_requirement_version and latest_requirement_version.valid_to
                        else ""
                    ),
                    "Requirement Change Summary": (
                        latest_requirement_version.change_summary
                        if latest_requirement_version and latest_requirement_version.change_summary
                        else ""
                    ),
                    "Source URL": p.source_url or (p.extra_metadata or {}).get("source_url", ""),
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
