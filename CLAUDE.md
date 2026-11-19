# CLAUDE.md: context for future sessions

Amazon product scraper → RAG-verified, schema-driven dataset → CSV for model
training, plus a Streamlit frontend. Built on 2026-11-19, extending the
`amazon_dataset_pipeline.zip` (amzpipe v1.0) the user downloaded.

## User's decisions (from the clarifying questions; don't re-ask)
- Extend the existing `amzpipe` code (not a rewrite). The YAML-driven schema is the core idea.
- Input: **ASINs / product URLs only** (pasted or .txt/.csv). Search (`-q`) still exists in the CLI but is not in the UI.
- Marketplace default **amazon.com**.
- Fetching: **requests first, Playwright headless-browser fallback** when blocked (`fetch.backend: auto`).
- RAG LLM: **optional and pluggable** (none by default; openai / anthropic / ollama). Every LLM answer must be grounded in a retrieved chunk.
- Use cases: **category classification** (`label` = `category_normalized`) **and price / rating prediction** (`training.targets: [price, rating]`, kept out of `text`).
- Reviews: the user wanted "top reviews on the product page". Live check (Nov 2026): amazon.com shows individual reviews **only to signed-in users**, so logged-out pages have none. The review extractor stays (works on saved signed-in pages via `--html-dir`); review *signals* were added as product fields instead (`rating_5_pct`…`rating_1_pct`, `review_summary`, `aspects_positive/negative`).
- Frontend: **Streamlit** (Node.js is not installed on this machine). Exports must be **CSV**.

## Environment
- Windows 11, PowerShell 5.1, Python 3.12. No git, no node.
- Project venv: `.venv\Scripts\python.exe` (all deps incl. streamlit 1.67, playwright + Chromium, ruamel.yaml, pandas 3.x). The global Python has no packages.
- The machine's IP is in Pakistan. Amazon.com then hides prices unless a US delivery ZIP is set (the fetcher does this automatically).

## Commands
```powershell
.venv\Scripts\python tests\make_fixtures.py; .venv\Scripts\python -m pytest -q tests -p no:cacheprovider
.venv\Scripts\python -m streamlit run app.py          # or run_app.bat
.venv\Scripts\python -m amzpipe run --asins B00006JSUA [--append]
.venv\Scripts\python -m amzpipe rebuild | export | schema --check
```

## Layout
- `amzpipe/`: the pipeline (library + CLI)
  - `pipeline.py`: `Pipeline.run / rebuild / re_export`. Jobs are stored in `data/output/run_inputs.json` (enables append / rebuild). Progress callback; stop-file for a clean stop.
  - `fetcher.py`: cache (`data/cache/ASIN_hash.html`), delays, CAPTCHA detection, auto browser fallback, currency/language cookies, **delivery-ZIP flow** (`_set_location`: glow-validation-token → get-rendered-address-selections CSRF → POST address-change). Errors: `FetchError` > `BlockedError` (stop run), `NotFoundError`, `CacheMissError`.
  - `extractor.py`: `Page` (main-product `sections()` = RAG knowledge base; merged `specs`), source types, `extract_reviews`, `apply_derived`.
  - `rag/enricher.py`: fill (spec_keys → LLM → patterns), verify (`grounded`, `on_unverified: keep|drop`), taxonomy (+ LLM tie-break among retrieved candidates only).
  - `schema.py`: `parse_schema(raw) -> (schema|None, errors, warnings)`. All validation lives here (the frontend relies on it).
  - `export.py`: `build_rows`, `assign_splits`, `table()` (CSV shape from `export:`), `re_export`. CSVs are `utf-8-sig`.
  - `transforms.py`: `TRANSFORMS` and `DERIVED` registries. `inputs.py`: ASIN / URL parsing.
- `config/schema.yaml` (data structure), `settings.yaml` (runtime), `taxonomy.yaml` (labels). Documented in `SCHEMA.md`, which the user asked for. **Keep SCHEMA.md in sync when changing schema features.**
- `app.py` + `ui/`: Streamlit (`st.navigation`). `ui/common.py` holds the ruamel round-trip YAML, saves with backups to `config/backups/`, and the background job runner (subprocess `python -m amzpipe … --progress-file --stop-file --log-file` in `data/run/`).

## Conventions and gotchas
- Config edits from the UI must go through ruamel round-trip (`load_rt` / `dump_rt`) and only touch changed keys. A no-op save must leave the file byte-identical (this was tested). Single-item `sources` in `schema.yaml` are block lists (`sources:\n  - {css: ...}`), because ruamel rewrites `[{a: b}]` as `[a: b]`.
- Amazon markup facts (verified live, Nov 2026): `.priceToPay .a-offscreen` is often **empty**, and the visible price is split spans (`$ 228 . 36`). The generic `.a-price .a-offscreen` hits comparison cards ("$4499"), so it's the last-resort source and `price` has `on_unverified: drop`. Histogram 0% rows are `<span aria-label>`, not `<a>`. Apple/Amazon device pages have no breadcrumbs. "Customers say" = `[data-testid='overall-summary']`; aspects = raw-HTML ids `cr-product-insights-detail-page_button_N_{POSITIVE|MIXED|NEGATIVE}_{aspect}`.
- Quality score: a new optional field lowers every product's score unless given a low `weight`.
- `Page.__init__`: nested script-in-noscript tags must be skipped when decomposing (real pages crashed before).
- Don't put targets (price, rating) or rating-leaking fields in `training.text_template`.
- Frontend verification: Streamlit `AppTest` scripts (render every page, click the save buttons, diff the config files, restore). Don't leave test data in `data/output`.
- Scraping etiquette: keep live test runs to a few products at default delays.
