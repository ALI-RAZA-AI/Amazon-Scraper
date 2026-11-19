# amazon_products v1.1.0

Amazon product listings scraped from public product pages, cleaned, validated and enriched with retrieval-grounded attributes.

- Products: **4** (rejected: 1)
- Reviews: **2**
- Splits: {'train': 4}
- CSV columns: asin, title, brand, price, currency, list_price, discount_pct, rating, review_count, rating_5_pct, rating_4_pct, rating_3_pct, rating_2_pct, rating_1_pct, review_summary, aspects_positive, aspects_negative, best_sellers_rank, date_first_available, availability, bullet_points, description, category_path, category_normalized, images, specifications, color, material, item_weight, dimensions, url, marketplace, scraped_at, text, label, quality, split

## Label distribution (`category_normalized`)

- Electronics > Audio > Headphones & Earbuds: 2
- Home & Kitchen > Kitchen Appliances: 1
- Sports & Outdoors: 1

## Regression targets

| target | filled | min | mean | max |
|---|---|---|---|---|
| price | 4/4 | 24.95 | 98.32 | 228.36 |
| rating | 4/4 | 4.4 | 4.60 | 4.7 |

## Field fill rate and sources

| field | type | filled | sources used |
|---|---|---|---|
| asin | string | 100% | css×2, regex_url×2 |
| title | string | 100% | css×3, jsonld×1 |
| brand | string | 100% | css×3, jsonld×1 |
| price | float | 100% | css×2, pattern×1, jsonld×1 |
| currency | enum | 100% | css×2, marketplace_default×1, jsonld×1 |
| list_price | float | 25% | css×1 |
| discount_pct | float | 100% | derived×4 |
| rating | float | 100% | css×3, jsonld×1 |
| review_count | int | 100% | css×3, jsonld×1 |
| rating_5_pct | int | 25% | css×1 |
| rating_4_pct | int | 25% | css×1 |
| rating_3_pct | int | 25% | css×1 |
| rating_2_pct | int | 25% | css×1 |
| rating_1_pct | int | 25% | css×1 |
| review_summary | string | 25% | css×1 |
| aspects_positive | list | 25% | regex_html_all×1 |
| aspects_negative | list | 25% | regex_html_all×1 |
| best_sellers_rank | int | 50% | spec×2 |
| date_first_available | string | 50% | spec×2 |
| availability | string | 25% | css×1 |
| bullet_points | list | 100% | css_all×4 |
| description | string | 75% | css×2, jsonld×1 |
| category_path | list | 50% | css_all×2 |
| category_normalized | string | 100% | rag_taxonomy×4 |
| images | list | 50% | regex_html_all×1, css×1 |
| specifications | dict | 50% | specs×2 |
| color | string | 50% | css×1, pattern×1 |
| material | string | 50% | pattern×2 |
| item_weight | string | 75% | spec_key×2, pattern×1 |
| dimensions | string | 50% | spec_key×2 |
| url | string | 100% | computed×4 |
| marketplace | string | 100% | computed×4 |
| scraped_at | string | 100% | computed×4 |
