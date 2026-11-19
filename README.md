# amzpipe — Amazon products → training-ready CSV datasets

Scrape Amazon product pages, organise them into a clean, validated,
RAG-verified dataset, and export it as CSV for model training. A Streamlit
frontend lets you run scrapes, browse the data (including where every value
came from), change the data structure, and download the CSVs.

```
ASINs / product URLs (.txt / .csv / pasted)
        │
        ▼
 ① fetch     ── polite delays, retries, CAPTCHA detection, disk cache;
                 sets a delivery ZIP in the marketplace's country (otherwise Amazon hides prices);
                 plain HTTP first, headless browser (Playwright) when blocked
 ② extract   ── schema-driven: CSS / spec tables / JSON-LD / regex   (config/schema.yaml)
 ③ RAG       ── chunk the main-product text → hybrid index (TF-IDF + optional embeddings)
                 • fill missing fields: spec lookup → LLM (grounded, optional) → regex
                 • verify scraped values against the page (drops carousel / comparison-card prices)
                 • map each product onto your label set (config/taxonomy.yaml)
 ④ derive    ── discount_pct, … from the finished record
 ⑤ validate  ── types, constraints, required fields, quality score, de-duplication
 ⑥ export    ── products.csv + train/validation/test.csv + reviews.csv + DATASET_CARD.md
```

## Setup (Windows, already done in this folder)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium     # headless-browser fallback
# optional, semantic retrieval (~1 GB, PyTorch): .venv\Scripts\pip install sentence-transformers
```

## Frontend

Double-click **`run_app.bat`**, or run:

```powershell
.venv\Scripts\python -m streamlit run app.py
```

| Page | What you do there |
|---|---|
| **Scrape** | Paste ASINs or product links, or upload a .txt / .csv. Choose *add to the existing dataset* or *start a new dataset*, then start. Shows progress and the log; stops cleanly (exporting what it has). Also has **Rebuild from cache** and **Load sample data** (offline demo). |
| **Browse data** | Filterable table. Select a product to see every field with its **source, confidence and evidence** (what RAG did), the training text and its reviews. Also reviews, rejected pages, charts, and the dataset card. |
| **Data structure** | Edit `schema.yaml` visually: rename, retype, reorder or delete fields; add fields (spec row, CSS selector, RAG-only, derived); training text template with live preview, label, targets, splits; reviews; the label taxonomy; raw YAML with validation and backups. |
| **Export CSV** | Choose the CSV columns, their order and headers. Preview, save (re-exports instantly), and download each CSV or everything as a .zip. |
| **Settings** | Marketplace, delivery ZIP, fetching, delays, proxies, RAG and LLM (an API key can be entered for the session only), quality threshold. |

## Command line

```powershell
.venv\Scripts\python -m amzpipe run --asins B00006JSUA https://www.amazon.com/dp/B09B8V1LZ3
.venv\Scripts\python -m amzpipe run -i products.csv              # any .txt/.csv with ASINs or links
.venv\Scripts\python -m amzpipe run -i more.txt --append         # add to the current dataset
.venv\Scripts\python -m amzpipe run --html-dir saved_pages\      # pages saved from your browser (offline)
.venv\Scripts\python -m amzpipe run -i asins.txt --llm openai --llm-model gpt-4o-mini
.venv\Scripts\python -m amzpipe rebuild     # after changing fields: re-extract from cached pages, no network
.venv\Scripts\python -m amzpipe export      # after changing training/export settings: rewrite the CSVs
.venv\Scripts\python -m amzpipe schema --check
```

Output goes to `data/output/`. Raw pages are cached in `data/cache/`, so
re-runs, rebuilds and appends never download a page twice.

## Changing the data structure

See **`SCHEMA.md`**: every key, the output record, the sources and transforms,
how RAG fills and verifies values, recipes, and troubleshooting.

## Tests

```powershell
.venv\Scripts\python tests\make_fixtures.py
.venv\Scripts\python -m pytest -q tests
```

These run offline against Amazon-style fixture pages, including one modelled on
the live 2026 layout. They cover:
- carousel and comparison-card price rejection, and split visible prices
- review signals and review extraction
- the hallucinated-LLM-answer guard, and taxonomy labels
- CSV column selection and renaming, offline rebuild, stop-and-export
- schema validation, the browser fallback, and the delivery-location flow

## Practical notes

- **Delivery location.** From outside the marketplace's country, Amazon says "cannot be shipped to your
  selected delivery location" and hides prices. `fetch.delivery_zip: auto` sets a local ZIP (10001 for
  amazon.com) through the site's own "Deliver to" calls. If a product still has the `delivery:` flag,
  use a proxy in that country.
- **Reviews.** amazon.com shows individual reviews only to signed-in users. Logged-out pages give
  the star histogram, Amazon's "Customers say" summary and positive / negative aspects. These are
  product columns. `reviews.csv` fills only from pages that contain reviews (e.g. saved while signed in).
- **Blocking.** Amazon blocks scrapers. Keep the default 2–5 s delays and scale up slowly. Use proxies
  if a run ends with status *blocked*, then continue with **Add to the existing dataset**.
- **Terms.** Amazon's Conditions of Use restrict automated access. Use this for research or personal
  datasets at low volume, and don't republish the scraped content. For commercial or large-scale use,
  the Product Advertising API is the sanctioned route.
- **Markup changes.** Amazon's markup changes over time. Watch the fill rates in `DATASET_CARD.md`.
  When one drops, add a new selector to that field's `sources` and **Rebuild**.
