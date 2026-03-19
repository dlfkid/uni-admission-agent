---
name: data-quality
description: Data validation rules, field standards, and deduplication guidelines
---

# Data Quality

## Required Fields
Every program record must have:
- `program_name`: Official English name of the program
- `univ_slug`: University identifier (e.g., "hku", "nus", "cuhk")
- `year`: Academic year (e.g., "2025-2026")
- `source_url`: The detail page URL where data was extracted

## Validation Rules
- `program_name` must not be empty or generic (e.g., "Program", "Course").
- `year` must match the pattern `YYYY` or `YYYY-YYYY`.
- `source_url` must be a valid HTTP/HTTPS URL.
- Tuition fees should include currency code when available.

## Deduplication
Programs are deduplicated by `(univ_slug, program_group_code, year)`.
- `program_group_code` is auto-generated from `univ_slug + normalized_name`.
- If a duplicate is found, the existing record is updated rather than creating a new one.

## Review Patches
Use `review_patch_skill` to correct individual fields after import:
- Provide the program ID and a dict of field corrections.
- Patches are logged for audit trail.
