"""Migration script to update all program_group_code values to new format.

New format: {univ_slug}#{normalize_program_name(name_en)}
Example: hku#mscfinance
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from sqlmodel import select

from src.models.admission import Program, University
from src.storage.db_manager import DatabaseManager
from src.utils.text import generate_program_group_code

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def migrate_slugs() -> None:
    db_manager = DatabaseManager()
    db_manager.init_db()

    logger.info("Starting program_group_code migration (new # format)...")

    with db_manager.get_session() as session:
        # 1. Fetch all universities to get their slugs
        universities = session.exec(select(University)).all()
        univ_map = {u.id: u.slug for u in universities}

        # 2. Fetch all programs
        programs = session.exec(select(Program)).all()
        logger.info(f"Found {len(programs)} programs to process.")

        updated_count = 0
        skipped_count = 0

        for program in programs:
            if not program.university_id or program.university_id not in univ_map:
                logger.warning(
                    f"Program {program.id} has no valid university_id. Skipping."
                )
                skipped_count += 1
                continue

            univ_slug = univ_map[program.university_id]
            name_en = program.name_en

            if not name_en:
                logger.warning(f"Program {program.id} has no name_en. Skipping.")
                skipped_count += 1
                continue

            # Generate new deterministic code
            new_code = generate_program_group_code(univ_slug, name_en)

            # Check if update is needed
            if program.program_group_code != new_code:
                old_code = program.program_group_code
                program.program_group_code = new_code
                session.add(program)
                updated_count += 1
                if updated_count <= 5 or updated_count % 50 == 0:
                    logger.info(
                        f"  [{updated_count}] {name_en}: {old_code} -> {new_code}"
                    )
            else:
                skipped_count += 1

        # 3. Commit changes
        logger.info(f"Committing changes for {updated_count} programs...")
        session.commit()

    logger.info(
        f"Migration complete. Updated: {updated_count}, Skipped: {skipped_count}."
    )


if __name__ == "__main__":
    asyncio.run(migrate_slugs())
