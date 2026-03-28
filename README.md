# OpenAlex Sustainable Fuels Filter Pipeline

Filter the entire [OpenAlex](https://openalex.org/) works dataset (~470M records) down to papers that discuss **sustainable fuels** alongside **environmental or performance impacts**. Outputs a single SQLite database with full metadata and keyword provenance.

## How it works

Every record in OpenAlex is a single JSON line inside a gzipped partition file. The pipeline reads each file and applies a filter cascade:

```
Raw line  ──→  is open access?  ──→  is English?  ──→  parse JSON
    │               │                    │
    │              skip                 skip
    ▼
has DOI?  ──→  has abstract?  ──→  contains fuel + impact term?  ──→  save
    │               │                         │
   skip            skip                      skip
```

A record is saved only if it contains **at least one fuel term** AND **at least one impact term** in the title or abstract. The matched terms are stored alongside the record so every inclusion is auditable.

### Fuel terms (59)

`sustainable fuel`, `biofuel`, `biodiesel`, `HVO`, `SAF`, `FAME`, `hydrogen`, `methanol`, `ethanol`, `diesel`, `jet fuel`, `ammonia`, `fuel cell`, `e-fuel`, and variants including hyphenated forms and standard codes (`ASTM D7566`, `EN 590`, `Def Stan 91-091`, …).

### Impact terms (48)

`emissions`, `CO2`, `GHG`, `NOx`, `PM2.5`, `thermal efficiency`, `cetane number`, `ignition delay`, `LCA`, `lifecycle`, `well-to-wheel`, `energy density`, `viscosity`, and variants.

### Optimisations

- **Raw-line pre-screen** — substring checks for `"is_oa": true` and `"language": "en"` before JSON parsing, skipping ~86% of records without deserialization.
- **orjson** — 3–5× faster JSON parsing for the records that pass pre-screen.
- **Title-first matching** — if the title alone has both a fuel and impact term, the expensive abstract inversion is skipped.
- **Inverted-index key pre-screen** — stems the abstract's dictionary keys as a cheap set-intersection test before full inversion.
- **Multiprocessing** — configurable parallel workers, each processing a separate file.

## Project structure

```
your-project-folder/
├── Proccess_dataset.py          # Core filtering logic (terms, stemming, matching, DB schema)
├── process_all_works.py         # Orchestrator (parallelism, progress TUI, state management)
├── pipeline_state.json          # Auto-generated: tracks which files have been processed
├── pipeline.log                 # Auto-generated: detailed log of every file + errors
└── openalex_works_filtered.sqlite  # Auto-generated: output database
```

Both scripts must be in the **same folder**. The orchestrator imports from `Proccess_dataset.py`.

## Setup

### Requirements

- Python 3.11+ (tested on 3.12 and 3.14)
- `orjson` (strongly recommended, falls back to stdlib `json`)
- `boto3` (only needed for S3 mode — not required if using `--local-dir`)

```bash
pip install orjson boto3
```

### Get the data

**Option A — Local snapshot (recommended for speed):**

Follow [OpenAlex: Download to your machine](https://docs.openalex.org/download-all-data/download-to-your-machine) to sync the works dataset. The result is a directory tree like:

```
~/data/openalex/works/
├── updated_date=2016-06-24/
│   └── part_0000.gz
├── updated_date=2025-10-10/
│   ├── part_0000.gz
│   ├── part_0001.gz
│   └── ...
└── updated_date=2026-02-25/
    └── ...
```

**Option B — Stream from S3 (no setup, slower):**

The pipeline can stream directly from the public `openalex` S3 bucket. No credentials needed, but downloads ~550 GB over the network.

## Usage

### Run against a local snapshot (fastest)

```bash
cd your-project-folder
python process_all_works.py --local-dir ~/data/openalex/works
```

This is the recommended way. With 8 workers on a server-class machine, expect ~15–30 minutes for the full dataset.

### Run from S3

```bash
cd your-project-folder
python process_all_works.py
```

Each worker downloads one file at a time, processes it, then deletes the temp file. ETA depends on bandwidth — typically 5–10 hours with 4 workers.

### Common options

```bash
# Use 8 parallel workers
python process_all_works.py --local-dir ./works --workers 8

# Smoke test — process only 10 files
python process_all_works.py --local-dir ./works --max-files 10

# Custom output database path
python process_all_works.py --local-dir ./works --output-db ~/results/fuels.sqlite

# Disable colored output (for piping to a file)
python process_all_works.py --local-dir ./works --no-color

# Abort if disk falls below 5 GB free
python process_all_works.py --local-dir ./works --min-free-gb 5
```

### Process a single file (standalone)

```bash
python Proccess_dataset.py --input path/to/part_0000.gz --output-db test.sqlite
```

### Resume after interruption

State is saved to `pipeline_state.json` after every file. Simply re-run the same command — it picks up where it left off. To start fresh, delete `pipeline_state.json`.

## Output database

SQLite file with one table: `filtered_records` (26 columns).

### Schema

| Column | Type | Description |
|--------|------|-------------|
| `openalex_id` | TEXT (PK) | OpenAlex work ID |
| `doi` | TEXT | DOI |
| `title` | TEXT | Paper title |
| `abstract_text` | TEXT | Reconstructed abstract |
| `publication_year` | INTEGER | Year |
| `publication_date` | TEXT | ISO date |
| `fwci` | REAL | Field-Weighted Citation Impact |
| `language` | TEXT | Always `en` |
| `type` | TEXT | Work type (`article`, `review`, …) |
| `is_oa` | INTEGER | Always `1` |
| `open_access_oa_status` | TEXT | `gold`, `green`, `hybrid`, … |
| `open_access_oa_url` | TEXT | OA URL |
| `primary_location_source_display_name` | TEXT | Journal/source name |
| `authors_institutions` | TEXT | JSON list of institution names |
| `authors_countries` | TEXT | JSON list of country codes |
| `matched_fuel_terms` | TEXT | JSON list — original fuel terms found |
| `matched_impact_terms` | TEXT | JSON list — original impact terms found |
| `matched_fuel_stems` | TEXT | JSON list — stemmed tokens that triggered |
| `matched_impact_stems` | TEXT | JSON list — stemmed tokens that triggered |

*(plus `open_access_any_repository_has_fulltext`, `primary_location_is_oa`, `primary_location_landing_page_url`, `primary_location_license`, `primary_location_source_host_organization_name`, `primary_location_source_type`)*

### Example queries

```sql
-- All matched records
SELECT title, doi, publication_year, matched_fuel_terms, matched_impact_terms
FROM filtered_records
ORDER BY publication_year DESC;

-- Papers about biodiesel emissions
SELECT title, doi
FROM filtered_records
WHERE matched_fuel_terms LIKE '%biodiesel%'
  AND matched_impact_terms LIKE '%emission%';

-- Most common fuel terms across all matches
SELECT value, COUNT(*) as n
FROM filtered_records, json_each(filtered_records.matched_fuel_terms)
GROUP BY value
ORDER BY n DESC;

-- Most common impact terms
SELECT value, COUNT(*) as n
FROM filtered_records, json_each(filtered_records.matched_impact_terms)
GROUP BY value
ORDER BY n DESC;

-- Papers by country
SELECT value as country, COUNT(*) as n
FROM filtered_records, json_each(filtered_records.authors_countries)
GROUP BY value
ORDER BY n DESC;
```

## Terminal UI

The pipeline displays a live dashboard that refreshes in-place:

```
 OpenAlex (local) → SQLite                    elapsed 12m 38s

 █████████████████░░░░░░░░░░░░░░░░░░░░  1,074 / 2,236  (48.0%)
 73.2K rec/s  ·  85.0 files/min  ·  ETA ~13m 41s

  ⚙  w0  2025-11-06/part_0412  filtering…  296 MB
  ⚙  w1  2025-11-06/part_0413  filtering…  301 MB
  ⚙  w2  2025-11-06/part_0414  filtering…  298 MB
  ⚙  w3  2025-11-06/part_0415  filtering…  295 MB

 Records   55.6M seen  →  1,218 matched  (0.0022%)
 Skipped   not open access 65%  ·  not English 22%  ·  no keywords 10%  ·  no DOI 3%
 System    412.8 GB free  ·  DB 4.1 MB  ·  0 errors
 Last hit  Methanol fuel cell degradation under variable CO₂ concentr…
```

In S3 mode, workers show download progress bars with speed.

Errors (JSON parse failures, corrupt files, etc.) appear in a dedicated section at the bottom and are logged to `pipeline.log` with full details.

## Customising the keywords

Edit the `FUEL_TERMS` and `IMPACT_TERMS` lists at the top of `Proccess_dataset.py`. The stemmer is lightweight (strips `-ing`, `-ed`, `-es`, `-s`, `-ies→y`), so include variant forms explicitly if needed. Hyphenated forms (`bio-jet fuel`) and standard codes (`ASTM D7566`) are handled.

After changing terms, delete `pipeline_state.json` and the output database to reprocess from scratch.
