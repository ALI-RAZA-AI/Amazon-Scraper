# Data Structure Guide

Everything about **what** the dataset contains is configuration, not code. The
pipeline reads these files at runtime, so you change the data structure by
editing YAML (or using the frontend), never Python.

| File | Controls | Frontend page |
|---|---|---|
| `config/schema.yaml` | Fields, their types and sources, validation, RAG, training text / label / splits, CSV columns, reviews | **Data structure**, **Export CSV** |
| `config/taxonomy.yaml` | The label set for `category_normalized` | **Data structure → Labels** |
| `config/settings.yaml` | Runtime only: marketplace, delivery location, delays, proxies, embedder, LLM, output folder | **Settings** |

`python -m amzpipe schema --check` lists the active fields and reports errors and warnings.

Every save from the frontend is validated first (nothing invalid is written),
keeps the comments in the YAML, and puts a copy of the previous version in
`config/backups/`. You can restore old versions from the **YAML** tab.

### After a change: what to run

| You changed… | Run | Network? |
|---|---|---|
| CSV columns, order, headers, list separator (`export:`) | Saving on **Export CSV** re-exports automatically · CLI: `python -m amzpipe export` | no |
| Training text, label, targets, splits (`training:`) | **Re-export CSVs** · `python -m amzpipe export` | no |
| Fields, sources, transforms, RAG, reviews, taxonomy | **Rebuild dataset from cache** · `python -m amzpipe rebuild` | no (pages are cached) |
| Which products are in the dataset | **Scrape** page · `python -m amzpipe run …` | yes, for new pages only |

---

## 1. Output files (`data/output/`)

| File | Contents |
|---|---|
| `products.csv` | Every accepted product, one row each. **The training data.** |
| `train.csv`, `validation.csv`, `test.csv` | The same rows, split (stratified by label). No `split` column. |
| `reviews.csv` | Individual reviews, linked by `asin`, in the same split as their product (see §3). |
| `rejected.csv` | Pages that failed: `url, asin, title, reason, quality, errors`. |
| `DATASET_CARD.md` | Counts, label distribution, target statistics, fill rate and source of every field. |
| `products.jsonl`, `reviews.jsonl` | Internal store: every field plus `_meta` provenance. The frontend and `amzpipe export` rebuild the CSVs from these. |
| `run_inputs.json` | Every page in the dataset, so it can be rebuilt or appended to. |

CSV details: UTF-8 **with BOM** (Excel opens it correctly), list fields joined
with ` | ` (configurable), dict fields such as `specifications` as JSON text,
whole numbers without `.0`, empty cells for missing values.
Optional extras (`settings.yaml → output.formats`): `jsonl` (split `.jsonl`
files) and `parquet`.

---

## 2. The product record (default schema)

A real record (amazon.com, Nov 2026), shortened:

```jsonc
{
  "asin": "B00006JSUA",                          // required, primary key (de-duplication)
  "title": "Lodge Pre-Seasoned Cast Iron Skillet, PFAS-Free, 10.25 Inches",
  "brand": "Lodge",
  "price": 23.38,                                // float; verified against the main product (RAG)
  "currency": "USD",
  "list_price": 24.9,                            // "List Price" / "Typical price", if shown
  "discount_pct": 6.1,                           // derived: (list - price) / list * 100
  "rating": 4.7,
  "review_count": 171288,
  "rating_5_pct": 82, "rating_4_pct": 10, "rating_3_pct": 4, "rating_2_pct": 1, "rating_1_pct": 3,
  "review_summary": "Customers love this cast iron skillet for its durability, ...",
  "aspects_positive": ["quality", "value for money", "cooking performance", "durability", "functionality"],
  "aspects_negative": ["size", "weight", "non-stick"],
  "best_sellers_rank": 1298,
  "date_first_available": null,                  // YYYY-MM-DD when the page lists it
  "availability": "In Stock",
  "bullet_points": ["Quality Cast Iron Frying Pan: ...", "..."],
  "description": "Experience the unmatched versatility and performance of ...",
  "category_path": ["Home & Kitchen", "Kitchen & Dining", "Cookware", "Pots & Pans", "Skillets"],
  "category_normalized": "Home & Kitchen > Cookware",   // RAG: nearest label in taxonomy.yaml
  "images": ["https://m.media-amazon.com/images/I/71iH2iNxTZL._AC_SL1500_.jpg", "..."],
  "specifications": {"Brand": "Lodge", "Material": "Cast Iron", "Color": "Black", "...": "..."},
  "color": "Black",                              // RAG-filled from the spec table
  "material": "Cast Iron",                       // RAG-filled
  "item_weight": "2.3 kg",                       // RAG-filled
  "dimensions": null,
  "url": "https://www.amazon.com/dp/B00006JSUA",
  "marketplace": "amazon.com",
  "scraped_at": "2026-11-19T16:41:37+00:00",

  // ---- added by the exporter (export.extra_columns)
  "text": "Title: Lodge Pre-Seasoned Cast Iron Skillet ...\nBrand: Lodge\n...",  // training.text_template
  "label": "Home & Kitchen > Cookware",          // copy of training.label_field
  "quality": 0.904,                              // 0-1 record quality (§7)
  "split": "train",                              // train | validation | test

  // ---- products.jsonl only: where every value came from (shown on Browse data)
  "_meta": {
    "provenance": {"price": "css#1", "material": "spec_key", "category_normalized": "rag_taxonomy", "...": "..."},
    "confidence": {"price": 1.0, "material": 0.95, "...": "..."},
    "evidence":   {"material": "specifications.Material", "category_normalized": "taxonomy score 0.599"},
    "flags": [],     // e.g. "price: scraped value 4499.0 not found in main product text; dropped"
    "errors": [], "quality": 0.904, "n_chunks": "..."
  }
}
```

**Provenance codes.** `css#1` means the 2nd source in the field's `sources` list matched.
`spec_key`, `llm` and `pattern` mean RAG filled the value (§8). `rag_taxonomy` and
`rag_taxonomy_llm` mean the label was chosen by retrieval. `derived` means computed
from other fields. `default` and `marketplace_default` mean a fallback was used.

### Two use cases built in

* **Category classification:** `text` → `label` (`category_normalized`).
* **Price / rating prediction:** the numeric columns are the targets
  (`training.targets: [price, rating]`). They are deliberately **not** in
  `text`. The schema check warns if you add a target to the template.
  `rating_*_pct`, `review_summary` and the aspects summarise the reviews, so
  they leak the rating. Drop them from your features when predicting `rating`.

---

## 3. Reviews (`reviews.csv`)

`review_id, asin, rating, title, body, author, review_date, review_country,
verified_purchase, helpful_votes, variant, split`

**Important (checked Nov 2026):** amazon.com shows individual reviews only to
signed-in users. Logged-out product pages contain the *review signals* above
(histogram, "Customers say", aspects) instead, so a normal scrape gives an
empty `reviews.csv`. The review extractor works on pages that do contain
reviews, for example pages you saved from your browser while signed in:
`python -m amzpipe run --html-dir saved_pages/`.

The `reviews:` section of `schema.yaml` works like `fields:`, except that CSS
selectors are relative to one review element (`containers`), and
`{attr: id}` reads an attribute of that element itself. Review text is never
used as RAG evidence for product fields, so a reviewer's "I paid $19.99"
can't leak into `price`.

---

## 4. Top-level sections of `schema.yaml`

### `dataset`
| key | meaning |
|---|---|
| `name`, `version`, `description` | Written to the dataset card. Bump `version` when you change fields. |
| `primary_key` | Field used to remove duplicates (default `asin`). |

### `training`
| key | meaning |
|---|---|
| `text_template` | Builds the `text` column. `{field}` placeholders are filled from the record; lists are joined with `; `, dicts become `k: v; ...`, missing values become `""`. Use `{{` / `}}` for literal braces. |
| `label_field` | Field copied into `label` (classification). `null` = no label column. |
| `targets` | Numeric fields you want to predict. Reported in the dataset card (coverage, min / mean / max) and flagged if they appear in `text_template`. |
| `splits` | e.g. `{train: 0.8, validation: 0.1, test: 0.1}`; must add up to 1. Stratified per label, so very rare labels end up only in `train`. |
| `seed` | Makes the splits reproducible (they also don't depend on scraping order). |

### `export` (shape of the CSV files)
| key | meaning |
|---|---|
| `columns` | Fields (and extra columns) to include, **in this order**. `[]` = every field in schema order, then `extra_columns`. |
| `extra_columns` | Pipeline-added columns appended after `columns`: any of `text, label, quality, split`. |
| `rename` | `{field: "CSV header"}` for the product CSVs. |
| `list_separator` | How list values are joined in one cell (default `" \| "`). |
| `review_columns`, `review_rename` | Same, for `reviews.csv` (`split` is always added). |

### `fields`: one entry per column (§5)
### `reviews`: `enabled`, `primary_key`, `max_per_product`, `containers`, `fields` (§3)

---

## 5. Field keys

```yaml
- name: price                 # column name: letters, digits, _ (unique)
  type: float                 # string | int | float | bool | list | dict | enum
  required: false             # missing -> product rejected
  description: Current selling price.   # documentation; also shown to the LLM
  choices: [...]              # enum only
  sources: [...]              # where to read it (§6), tried top to bottom
  transforms: [parse_price]   # clean-up functions, in order (§6.2)
  constraints: {min: 0, max: 100000}    # validation (§7)
  default: null               # value if nothing found
  default_from_marketplace: false       # currency only: the marketplace's currency
  weight: 1.0                 # share in the quality score; 0 = not counted (§7)
  rag: {...}                  # retrieval-based fill / verification (§8)
```

---

## 6. `sources`: where values come from

Sources are tried **top to bottom**; the **first non-empty** value (after
transforms and type conversion) wins. Amazon uses several page layouts, so most
fields list several.

| source | example | returns |
|---|---|---|
| `css` (+ `attr`) | `{css: "#productTitle"}` · `{css: "#landingImage", attr: src}` | text (or attribute) of the first match |
| `css_all` | `{css_all: "#feature-bullets li"}` | list: text of every match |
| `spec` | `{spec: Best Sellers Rank}` · `{spec: [Brand, Brand Name]}` | one row of the merged specification tables (case-insensitive, first key found) |
| `specs` | `{specs: all}` | all specification tables / detail bullets merged into one dict |
| `table` (+ `key`) | `{table: "#productDetails_techSpec_section_1", key: Brand}` | one table as a dict, or one value |
| `detail_bullets` | `{detail_bullets: "#detailBullets_feature_div"}` | `"Key : Value"` bullets as a dict |
| `jsonld` | `{jsonld: offers.price}` | dotted path in the page's schema.org Product JSON-LD |
| `regex_url` | `{regex_url: "/dp/([A-Z0-9]{10})"}` | group 1 matched against the URL |
| `regex_html` / `regex_html_all` | `{regex_html_all: "\"hiRes\":\"(https://[^\"]+)\""}` | group 1 from the raw HTML (first / all matches) |
| `exists` | `{exists: "[data-hook^='avp-badge']"}` | `true` / `false` |
| `computed` | `{computed: url}` · `marketplace` · `scraped_at` · `asin` (reviews) | filled by the pipeline |
| `derived` | `{derived: discount_pct}` | computed from the finished record (§6.3) |
| `attr` alone | `{attr: id}` | attribute of the container element (reviews) |

**`regex` modifier.** You can add `regex:` to `css`, `css_all`, `spec` and `attr`
sources. It keeps group 1 of the extracted text, e.g.
`{css: "[data-hook='review-date']", regex: "Reviewed in (?:the )?(.+?) on "}`.

`sources: []` means the field is filled only by RAG.

**When Amazon changes its markup**, a field's fill rate drops (see
`DATASET_CARD.md` or **Browse data → Charts**). To fix it, open a cached page
in `data/cache/` (files are named `ASIN_xxxxxxxx.html`), find the new element,
add a source **above** the old one, then **Rebuild**.

### 6.1 Built-in sources worth knowing
* Prices: the visible price block is split into spans (`$ 228 . 36`) and its
  hidden `.a-offscreen` copy is often empty. Both forms are listed, and
  `parse_price` handles either.
* Star histogram: `[data-csa-c-content-id='customerReviews-histogram-fiveStar'] [aria-label]`
  (a 0% row is a `<span>`, not a link).

### 6.2 `transforms`
These are applied in order. On lists they run on each element; on dicts, on each value.

| name | effect |
|---|---|
| `clean_text` | Unicode-normalise, remove LTR/RTL marks, collapse whitespace |
| `strip_brand_prefix` | `"Visit the Sony Store"` / `"Brand: Sony"` → `"Sony"` |
| `parse_price` | `"$1,299.00"` → `1299.0`, `"$ 228 . 36"` → `228.36`, `"1.234,56 €"` → `1234.56` |
| `parse_float` | `"4.4 out of 5 stars"` → `4.4` |
| `parse_int` | `"12,483 ratings"` → `12483`, `"#1,298 in Kitchen"` → `1298` |
| `currency_code` | `"$"` → `USD` (or `CAD`/`AUD`/… on those marketplaces), `"£"` → `GBP`, `"PKR"` → `PKR` |
| `lower` | lowercase |
| `parse_date` | `"March 3, 2024"` / `"3 Mar. 2024"` / `"Reviewed in … on …"` → `"2024-03-03"` |
| `strip_star_text` | removes `"5.0 out of 5 stars"` from review titles |
| `clean_review_text` | removes `"Read more"`, `"The media could not be loaded."` |
| `parse_helpful` | `"One person found this helpful"` → `1` |

To add your own, write `def my_fn(value, ctx): ...` in `amzpipe/transforms.py`
and register it in `TRANSFORMS`. After transforms, the value is **converted to
`type`**. If that fails, the source counts as empty and the next one is tried.

### 6.3 `derived` functions
These run after extraction and RAG, so they can use any field: `discount_pct`,
`image_count`, `bullet_count`, `description_length`, `title_length`. To add
one, write `def my_fn(record): ...` in `amzpipe/transforms.py` and register it in `DERIVED`.

---

## 7. `constraints` and the quality score

| key | applies to | behaviour |
|---|---|---|
| `min`, `max` | int / float | violation → error; quality ×0.8 |
| `min_length` | string | violation → error |
| `regex` | string | must fully match (e.g. ASIN `^[A-Z0-9]{10}$`) |
| `max_length` | string | **truncated**, not rejected |
| `max_items` | list | **truncated**, not rejected |

`required: true` with a missing value gives quality 0, so the page goes to `rejected.csv`.

**Quality score** = the confidence-weighted share of fields that are filled,
excluding computed and derived fields. Each field counts `weight` (required
fields count double), multiplied by the confidence of its source (CSS 1.0,
spec table 0.95, LLM 0.85, regex pattern 0.7, …). Products below
`settings.yaml → output.min_quality` (default 0.5) are rejected. Give a new
optional field a low `weight` if many products won't have it, so it doesn't
push them under the threshold. The review signals use 0.2–0.5 for that reason.

---

## 8. `rag`: retrieval-augmented filling and verification

For every product, the pipeline builds a small knowledge base from the **main
product only**: title, bullets, description, spec tables, price block,
breadcrumbs, byline, variation selector and JSON-LD. Carousels, ads, "customers
also bought" and customer reviews are excluded. The text is split into chunks
and indexed with **hybrid retrieval**:

- TF-IDF (always on)
- dense embeddings from `sentence-transformers`, if installed (`rag.embedder` in settings)

```yaml
rag:
  mode: fill                # fill | taxonomy
  query: "item weight pounds ounces grams kg"   # retrieval query (default: name + description)
  spec_keys: [Item Weight, Weight]              # exact rows to look up in `specifications`
  patterns: ["([0-9.]+\\s?(?:pounds|oz|kg))\\b"] # regex over retrieved chunks, group 1 = value
  on_unverified: keep       # keep | drop (see Verification)
```

**`mode: fill`.** If the field is still empty after `sources`:
1. **Spec-table lookup** with `spec_keys` (confidence 0.95).
2. **LLM extraction**, if `rag.llm.provider` is set in settings. The top-k chunks
   for all pending fields go into **one** call per product. The LLM must cite a
   chunk, and its value must **appear in that chunk** (the grounding check).
   Otherwise the value is discarded and noted in `_meta.flags`, so hallucinated
   values never reach the data (confidence 0.85).
3. **Regex `patterns`** over the retrieved chunks, highest-ranked first (confidence 0.7).

**Verification** (`settings.yaml → rag.verify_existing: true`). A scraped
string or number in a field with a `rag` block is checked against the
main-product text. Prices split over spans (`$ 228 . 36`) are matched as
`228.36`. If the value isn't there (for example a price taken from a
comparison card), it is removed and re-derived with steps 1–3. If nothing is
found, what happens depends on the field:
* `on_unverified: keep` (default): keep the original at half confidence, with a flag.
* `on_unverified: drop` (set on `price` and `list_price`): leave it empty, with a flag.
  A wrong price is worse than a missing one for training.

**`mode: taxonomy`.** This retrieves the closest labels in `taxonomy_file`, using the
title, breadcrumbs and first 3 bullets. Options: `min_score` (below it the field stays empty), `candidates`
(how many labels to retrieve, default 3) and `llm_margin`. If an LLM is configured
and the top candidates score within `llm_margin` of each other, the LLM picks
one **of the retrieved candidates** (any other answer is ignored). Edit labels in
`config/taxonomy.yaml` (`Label: keywords / short description`). Richer
keywords give better matches.

---

## 9. Recipes

**Add a new attribute (e.g. battery life).** Use **Data structure → Add a field**, or:
```yaml
  - name: battery_life
    type: string
    description: Battery life with unit, e.g. "40 hours".
    weight: 0.5
    sources:
      - {spec: [Battery Life, Battery Average Life]}
    transforms: [clean_text]
    rag:
      mode: fill
      query: "battery life hours playtime charge"
      spec_keys: [Battery Life, Battery Average Life]
      patterns: ["([0-9]+\\s?(?:hours|hrs|h))\\b"]
```
Then **Rebuild**. To put it in the model text, add `{battery_life}` to `training.text_template`.

**Remove a field.** Delete it on **Data structure → Fields** (select the row, then the bin icon). It is
removed from the CSV settings too. Remove any `{placeholder}` for it from the template.

**Rename a column.**
* Only in the CSV: **Export CSV → CSV header** (`export.rename`).
* Everywhere: rename it on **Data structure → Fields**. References in the template, label and CSV
  settings are updated for you.

**Choose and order CSV columns.** **Export CSV** (`export.columns`). Untick a field to leave it out;
change `#` to reorder.

**Make a field mandatory.** `required: true`. Products without it go to `rejected.csv`.

**Use a different label (brand, price band, …).** Set `label_field: brand`, or add a
derived / enum field and point `label_field` at it.

**Price-prediction dataset.** Keep `price` out of `text_template`. Consider
`export.columns: [asin, text, brand, category_normalized, rating, review_count,
best_sellers_rank, discount_pct, price]` and `label_field: null` (or keep the
label as a feature).

**Instruction-tuning dataset.**
```yaml
  text_template: |
    ### Instruction: Write a product description.
    ### Input: {title} | {brand} | {bullet_points}
    ### Response: {description}
  label_field: null
```

**Add a classification label.** On **Data structure → Labels**, add a row such as
`Electronics > Cameras` with its keywords, then **Rebuild**.

**Different Amazon country.** Set `marketplace: amazon.co.uk` in settings (or choose it on the
Scrape page). Currency, number parsing and the default delivery postcode follow.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `price` empty, flag `delivery: ... can't ship` | Amazon thinks you're outside the marketplace's country. `fetch.delivery_zip: auto` sets a local ZIP automatically; if Amazon refuses it, use a proxy in that country. |
| Flag `price: scraped value … not found in main product text; dropped` | The only price on the page belonged to another item (carousel or comparison card). Working as intended. |
| `reviews.csv` empty | Expected on logged-out pages (§3). Use the review-signal columns. |
| Fill rate of a field dropped | Amazon changed its markup: add a new selector above the old one (§6), then **Rebuild**. |
| Many products rejected | Check `rejected.csv` → `errors`. Lower `output.min_quality`, or give new optional fields a lower `weight`. |
| `BlockedError` / run status "blocked" | Amazon is throttling. Wait, raise the delays, add proxies, then run again with **Add to the existing dataset**: cached pages are reused. |
