# Schema-Based Extraction System Design

## Goal

Eliminate per-page LLM calls for detail page data extraction. LLM analyzes the first detail page to learn the HTML structure (CSS selectors), then all subsequent pages from the same university/index use pure code extraction. LLM serves as fallback for missing fields.

## Priority

**Data completeness & accuracy > Speed > Token savings**

The system must run unattended (e.g., overnight) and produce complete, correct data for all pages in an index — whether 10 pages (Edinburgh) or 400 pages (UCL).

---

## Architecture

```
Page 1 (Learning Phase):
  HTML ──→ LLMCleanerAgent (existing) ──→ ParsedProgramData
                                              │
  HTML + ParsedProgramData ──→ LLM ──→ SelectorSchema JSON
                                              │
                                         Save to disk + baseline score

Page 2-N (Reuse Phase):
  HTML ──→ CSS Selector extraction ──→ Result
                                         │
                                Missing fields ≤ 3?
                               ╱              ╲
                             yes               no
                              │                 │
                    Field-level LLM       Full-page LLM
                      fallback              fallback
                              │                 │
                              └──→ Merge ──→ Score & Persist
```

---

## Components

| Component | Responsibility |
|-----------|---------------|
| `SelectorSchema` | Data structure: field name → CSS selector mapping + baseline score |
| `SchemaLearner` | Uses LLM to infer CSS selectors from HTML + extracted data |
| `SelectorExtractor` | Applies CSS selectors to HTML to extract field values |
| `SchemaManager` | Manages JSON file read/write, score validation, deprecation/rebuild |

All components live in a single new file: `src/scrapers/schema_extractor.py`

---

## SelectorSchema JSON Format

Storage location: `.adm-agent/schemas/{univ_slug}_{page_pattern}.json`

```json
{
  "version": 1,
  "univ_slug": "edinburgh",
  "page_pattern": "postgraduate-taught",
  "created_at": "2026-04-05T10:00:00Z",
  "source_url": "https://study.ed.ac.uk/programmes/postgraduate-taught/108-cognitive-science",
  "baseline_score": 0.85,
  "total_fields": 6,
  "fields": {
    "faculty": {
      "selector": "div.field--name-field-school a",
      "attribute": "text",
      "sample_value": "Edinburgh Medical School"
    },
    "tuition_amount": {
      "selector": "table.tuition-fees td.fee-amount",
      "attribute": "text",
      "post_process": "extract_decimal"
    },
    "study_options": {
      "selector": "div.study-options-list div.study-option",
      "attribute": "text",
      "is_list": true,
      "post_process": "parse_study_option"
    },
    "deadlines": {
      "selector": "table.deadline-table tr td",
      "attribute": "text",
      "is_list": true,
      "post_process": "parse_deadline"
    },
    "requirements": {
      "selector": "div.entry-requirements-content",
      "attribute": "text",
      "post_process": "parse_requirements"
    },
    "name_en": {
      "selector": "h1.page-title",
      "attribute": "text"
    }
  }
}
```

### Field Spec Properties

| Property | Type | Description |
|----------|------|-------------|
| `selector` | string | CSS selector to locate the element(s) |
| `attribute` | string | What to extract: `"text"` (textContent), `"href"`, or any HTML attribute |
| `sample_value` | string? | Example value from the learning page (for debugging) |
| `is_list` | bool? | If true, select all matching elements as a list. Default: false |
| `post_process` | string? | Named post-processor to apply: `extract_decimal`, `parse_study_option`, `parse_deadline`, `parse_requirements` |

### page_pattern Derivation

Derived from the index URL path:
- `https://study.ed.ac.uk/programmes/postgraduate-taught?page=5` → `postgraduate-taught`
- `https://www.ucl.ac.uk/prospective-students/graduate/taught-degrees` → `graduate_taught-degrees`
- Final filename: `edinburgh_postgraduate-taught.json`

---

## SchemaLearner — LLM Selector Inference

### Input/Output

- **Input:** raw HTML of first detail page + ParsedProgramData from LLMCleanerAgent
- **Output:** SelectorSchema JSON with CSS selectors for each field

### Two-Step Process

1. **Step 1 (existing):** `LLMCleanerAgent.clean_markdown()` extracts structured data from the page (already implemented, no changes needed)
2. **Step 2 (new):** Send HTML + extracted data to LLM, ask it to reverse-engineer CSS selectors for each field value

### LLM Prompt (Step 2)

```
You are an HTML structure analysis expert.

I will give you:
1. A university course page's HTML
2. Structured data already extracted from that page

For each extracted field value, find where it appears in the HTML and return
a CSS selector that can reliably locate that value.

Requirements:
- Use class/id-based selectors, avoid fragile positional selectors (div > div > span)
- If a value appears in multiple places, prefer the most semantically clear location
  (e.g., a labeled table cell > a paragraph in running text)
- If a field's value cannot be found in the HTML, return null for that selector
- Return JSON matching this schema:
  {
    "fields": {
      "<field_name>": {
        "selector": "<css_selector>",
        "attribute": "text" | "href" | "<attr_name>",
        "is_list": true | false
      }
    }
  }

Extracted data:
{extracted_data_json}

HTML:
{html_content}
```

### Large HTML Handling

HTML may exceed 60K characters. Strategy:
1. Apply `_strip_html_boilerplate()` (existing function, removes nav/footer, 30-50% reduction)
2. If still > 30K, keep only HTML regions containing extracted values (string search, ±2000 chars context per field)
3. Merged fragments typically < 20K

### Immediate Validation

After LLM returns selectors, immediately validate on the same page:
- For each field, run the selector on the HTML
- Compare extracted value with known LLMCleanerAgent result
- Match → field scores 1 point
- No match or null selector → field scores 0
- `baseline_score = scored_fields / total_fields`

This ensures no "looks-correct-but-doesn't-work" selectors are persisted.

---

## SelectorExtractor — Code-Based Extraction

Uses `BeautifulSoup` (already a project dependency) to execute CSS selectors.

### Core Logic

```python
def extract(html: str, schema: SelectorSchema) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    results = {}
    for field_name, field_spec in schema.fields.items():
        elements = soup.select(field_spec.selector)
        if not elements:
            results[field_name] = None
            continue
        if field_spec.is_list:
            results[field_name] = [extract_value(el, field_spec) for el in elements]
        else:
            results[field_name] = extract_value(elements[0], field_spec)
    return results
```

### Post-Processors

Named functions mapped by the `post_process` field:
- `extract_decimal`: Regex to pull numeric value from text (e.g., "£12,500" → 12500.00)
- `parse_study_option`: Parse "Full-time | 1 year" → `{mode: "FullTime", duration_months: 12}`
- `parse_deadline`: Parse "31 January 2026" → ISO 8601 datetime
- `parse_requirements`: Split requirement text into structured categories

---

## Fallback Logic

### Decision Flow

```
After CSS selector extraction completes:

1. Count missing fields (value is None)

2. missing = 0
   → Use selector results as-is ✓

3. missing ≤ 3
   → Field-level LLM fallback
   → Send only missing field names + HTML to LLM
   → Prompt: "Extract only these fields from this HTML: {missing_fields}"
   → Merge: selector results + LLM supplement

4. missing > 3
   → Full-page LLM fallback
   → Run existing LLMCleanerAgent pipeline on entire page
   → Use LLM result entirely (replaces selector results)
```

### Field-Level Fallback Prompt

```
Extract ONLY the following fields from this university programme page HTML.
Return JSON with only these keys. If a field is not found, use null.

Fields to extract: {missing_field_names}

Field definitions:
- faculty: The school, faculty, or department offering the programme
- tuition_amount: Annual tuition fee as a number
- deadlines: Application deadline dates
...

HTML:
{stripped_html}
```

---

## Scoring & Schema Lifecycle

### Baseline Score

Calculated during learning (Step 2 validation):
- `baseline_score = fields_with_working_selectors / total_fields`
- Stored in the JSON file

### Runtime Score

Each detail page extraction produces a page score:
- `page_score = fields_with_values / total_fields`

### Rolling Average & Deprecation

During a single run, track cumulative scores:
- Maintain `sum_scores` and `page_count`
- **Deprecation trigger:** `(sum_scores / page_count) < (baseline_score × 0.8)` AND `page_count >= 3`
- When triggered:
  1. Rename current schema to `{name}.deprecated.json`
  2. Use the next detail page as the new "page 1" — run full LLM extraction + SchemaLearner
  3. Save new schema, continue with remaining pages

### Cross-Run Reuse

On subsequent runs:
- Load existing schema from disk
- First page acts as validation: extract with selectors, compute score
- If score < `baseline_score × 0.8` → deprecate and rebuild immediately
- If score OK → continue using schema for all pages

---

## Integration Point

### Modified `_auto_fetch_and_extract()` in `common.py`

Current flow:
```
fetch all pages → LLM extract all pages (5 parallel workers)
```

New flow:
```
fetch all pages →
  Check for existing schema (SchemaManager.load)
    ├─ Schema exists and valid:
    │    page 1: SelectorExtractor → validate score → if OK, continue
    │    page 2-N: SelectorExtractor + fallback (parallel)
    │
    └─ No schema or invalid:
         page 1: LLM extract (existing) → SchemaLearner → save schema
         page 2-N: SelectorExtractor + fallback (parallel)
```

Page 1 is sequential (learning or validation). Pages 2-N can run in parallel.

### Performance Impact

For 10 pages:
- **Old:** 10 × LLM calls ≈ 5-10 minutes
- **New:** 1-2 × LLM calls (extract + learn) + 9 × selector (<1s each) + occasional fallback ≈ 2-3 minutes

For 400 pages (UCL):
- **Old:** 400 × LLM calls ≈ 3-6 hours
- **New:** 1-2 × LLM calls + 399 × selector + ~40 fallback calls ≈ 20-30 minutes

---

## File Structure

```
.adm-agent/
  schemas/
    edinburgh_postgraduate-taught.json
    edinburgh_postgraduate-taught.deprecated.json  (if rebuilt)
    ucl_graduate_taught-degrees.json
    manchester_taught_postgraduate-courses.json

src/scrapers/
  schema_extractor.py    (NEW — all 4 components)
```

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| SchemaLearner LLM call fails | Skip schema creation, fall through to full LLM extraction for all pages |
| CSS selector raises exception | Treat field as missing, increment missing count for fallback decision |
| `.adm-agent/schemas/` dir doesn't exist | Create it on first schema save |
| Schema JSON is corrupted/unparseable | Delete and rebuild |
| All selectors return None on first reuse | Immediate deprecation, rebuild from that page |
| BeautifulSoup not installed | Already a project dependency (used in link_parser.py) |

---

## Out of Scope

- Schema sharing across different universities (each university gets its own)
- Schema versioning beyond deprecation (no migration between schema versions)
- UI for viewing/managing schemas (file system is the interface)
- Automatic detection of university CMS changes (relies on score degradation)
