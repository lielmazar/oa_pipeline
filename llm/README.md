# LLM classification and QA (parked)

These modules come from the previous web application and are kept to be ported as later
pipeline stages (stage 4: LLM classification, stage 5: inter-rater QA). They are not wired
into the new pipeline yet.

| File | What it does |
|------|--------------|
| `llm_batch.py` | OpenAI Batch API lifecycle: estimate cost, prepare JSONL, submit, poll, parse results into SQLite |
| `llm_pricing.py` | Model pricing table for cost estimates |
| `qa.py` | QA sampling, ratings, IRR computation (expects the old `meta.db` tables) |
| `qa_stats.py` | Krippendorff's alpha and helpers |

`llm_batch.py` reads `openalex_id`, `title`, `abstract_text` from a SQLite table named
`filtered_records`. Stage 3 already writes exactly that table to
`runs/<run_id>/stage3/filtered_records.sqlite`, so the port mostly means replacing the web
app's job bookkeeping with a CLI. Install the extra dependencies with `pip install -e ".[llm]"`
and set `OPENAI_API_KEY`.
