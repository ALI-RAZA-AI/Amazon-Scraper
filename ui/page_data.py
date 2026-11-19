"""Browse page: the dataset, per-product provenance (where each value came from), reviews, charts."""
import json

import pandas as pd
import streamlit as st

from ui import charts
from ui.common import current_schema, load_products, load_rejected, load_reviews, output_dir

st.title("Browse data")
rows = load_products()
if not rows:
    st.info("No data yet. Scrape some products first (or load the sample data).", icon=":material/info:")
    st.page_link("ui/page_scrape.py", label="Go to Scrape", icon=":material/download:")
    st.stop()

schema, _, _ = current_schema()
reviews = load_reviews()
rejected = load_rejected()
df = pd.DataFrame([{k: v for k, v in r.items() if k != "_meta"} for r in rows])

c = st.columns(5)
c[0].metric("Products", f"{len(df):,}")
c[1].metric("Reviews", f"{len(reviews):,}")
c[2].metric("Rejected pages", f"{len(rejected):,}")
c[3].metric("Average quality", f"{df['quality'].mean():.2f}" if "quality" in df else "-")
c[4].metric("Distinct labels", df["label"].nunique() if "label" in df else "-")

# ------------------------------------------------------------- filters (one row)
f = st.columns([3, 3, 2, 2])
query = f[0].text_input("Search title / brand / ASIN", placeholder="e.g. earbuds")
labels = sorted(df["label"].dropna().unique()) if "label" in df else []
pick_labels = f[1].multiselect("Label", labels)
splits = sorted(df["split"].dropna().unique()) if "split" in df else []
pick_splits = f[2].multiselect("Split", splits)
min_q = f[3].slider("Min quality", 0.0, 1.0, 0.0, 0.05)

view = df
if query:
    hay = view[[c for c in ("title", "brand", "asin") if c in view]].astype(str).agg(" ".join, axis=1)
    view = view[hay.str.contains(query, case=False, regex=False)]
if pick_labels:
    view = view[view["label"].isin(pick_labels)]
if pick_splits:
    view = view[view["split"].isin(pick_splits)]
if "quality" in view:
    view = view[view["quality"].fillna(0) >= min_q]

tab_products, tab_reviews, tab_rejected, tab_charts, tab_card = st.tabs(
    ["Products", "Reviews", "Rejected", "Charts", "Dataset card"])

# ----------------------------------------------------------------- products
with tab_products:
    show_all = st.toggle("Show every column", value=False)
    table = view.copy()
    if "images" in table:
        table.insert(0, "image", table["images"].map(lambda x: x[0] if isinstance(x, list) and x else None))
    for col in table.columns:  # lists / dicts as readable text
        if table[col].map(lambda x: isinstance(x, (list, dict))).any():
            table[col] = table[col].map(lambda x: " | ".join(map(str, x)) if isinstance(x, list)
                                        else json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else x)
    main_cols = [c for c in ("image", "asin", "title", "brand", "price", "currency", "rating", "review_count",
                             "label", "quality", "split", "url") if c in table]
    event = st.dataframe(
        table, hide_index=True, height=min(420, 38 + 35 * max(len(table), 1)), on_select="rerun",
        selection_mode="single-row",
        column_order=None if show_all else main_cols,
        column_config={
            "image": st.column_config.ImageColumn("Image", width="small"),
            "url": st.column_config.LinkColumn("Link", display_text="open"),
            "quality": st.column_config.ProgressColumn("Quality", min_value=0, max_value=1, format="%.2f"),
            "price": st.column_config.NumberColumn("Price", format="%.2f"),
            "rating": st.column_config.NumberColumn("Rating", format="%.1f ★"),
            "review_count": st.column_config.NumberColumn("Ratings", format="localized"),
            "title": st.column_config.TextColumn("Title", width="large"),
        })
    st.caption(f"{len(view)} of {len(df)} products · select a row to see where each value came from")

    sel = event.selection.rows if event and event.selection else []
    if sel:
        asin = view.iloc[sel[0]].get("asin")
        rec = next(r for r in rows if r.get("asin") == asin)
        meta = rec.get("_meta") or {}
        st.divider()
        img, info = st.columns([1, 4], gap="large")
        if rec.get("images"):
            img.image(rec["images"][0], width="stretch")
        info.subheader(rec.get("title") or asin)
        info.markdown(f"**{rec.get('brand') or ''}** · `{asin}` · label: **{rec.get('label') or '-'}** · "
                      f"quality {rec.get('quality') or 0:.2f} · split **{rec.get('split')}** · "
                      f"[open on Amazon]({rec.get('url')})")
        for flag in meta.get("flags") or []:
            info.warning(flag, icon=":material/flag:")
        for err in meta.get("errors") or []:
            info.error(err, icon=":material/error:")

        prov, conf, ev = meta.get("provenance", {}), meta.get("confidence", {}), meta.get("evidence", {})
        names = schema.names if schema else [k for k in rec if not k.startswith("_")]
        detail = pd.DataFrame([{
            "field": n,
            "value": (" | ".join(map(str, rec[n])) if isinstance(rec.get(n), list)
                      else json.dumps(rec[n], ensure_ascii=False) if isinstance(rec.get(n), dict)
                      else ("" if rec.get(n) is None else str(rec[n]))),
            "source": prov.get(n, "-"),
            "confidence": conf.get(n),
            "evidence": ev.get(n, ""),
        } for n in names])
        st.markdown("**Fields** · *source* says how each value was found: `css`/`jsonld`/`spec` = read from the "
                    "page, `spec_key`/`llm`/`pattern` = filled by RAG from retrieved page text, "
                    "`rag_taxonomy` = label chosen by retrieval")
        st.dataframe(detail, hide_index=True, width="stretch", column_config={
            "confidence": st.column_config.ProgressColumn("Confidence", min_value=0, max_value=1, format="%.2f"),
            "value": st.column_config.TextColumn("Value", width="large")})
        st.markdown("**Training text** (`text` column)")
        st.code(rec.get("text") or "", language=None, wrap_lines=True)
        mine = [r for r in reviews if r.get("asin") == asin]
        if mine:
            st.markdown(f"**Reviews** ({len(mine)})")
            st.dataframe(pd.DataFrame(mine), hide_index=True, width="stretch")

# ------------------------------------------------------------------ reviews
with tab_reviews:
    if not reviews:
        st.info("No individual reviews in this dataset. amazon.com shows them only to signed-in users, so "
                "logged-out product pages carry review *signals* instead: the star histogram "
                "(`rating_5_pct` … `rating_1_pct`), Amazon's \"Customers say\" summary (`review_summary`) and "
                "the positive / negative aspects. Those are columns on the Products tab. Pages you saved while "
                "signed in (`run --html-dir`) do yield individual reviews here.", icon=":material/info:")
    else:
        rv = pd.DataFrame(reviews)
        if query or pick_labels or pick_splits or min_q:
            rv = rv[rv["asin"].isin(view["asin"])]
        st.dataframe(rv, hide_index=True, width="stretch", height=480, column_config={
            "body": st.column_config.TextColumn("Review", width="large"),
            "rating": st.column_config.NumberColumn("Stars", format="%.0f ★")})
        st.caption(f"{len(rv)} reviews · linked to products by asin · same split as their product")

# ----------------------------------------------------------------- rejected
with tab_rejected:
    if rejected.empty:
        st.success("No pages were rejected.")
    else:
        st.caption("Pages that failed validation (missing required fields, quality below "
                   "`output.min_quality`, not found, or errors).")
        st.dataframe(rejected, hide_index=True, width="stretch", column_config={
            "url": st.column_config.LinkColumn("URL")})

# ------------------------------------------------------------------- charts
with tab_charts:
    left, right = st.columns(2, gap="large")
    with left:
        if "label" in view and view["label"].notna().any():
            st.markdown("**Products per label**")
            counts = view["label"].fillna("(none)").value_counts().rename_axis("label").reset_index(name="products")
            charts.show(charts.hbar(counts, "label", "products", "Products"))
        st.markdown("**Price**")
        if "price" in view and view["price"].notna().any():
            charts.show(charts.histogram(view["price"], "Price"))
        else:
            st.caption("No prices in the current selection.")
    with right:
        st.markdown("**Average rating**")
        if "rating" in view and view["rating"].notna().any():
            charts.show(charts.histogram(view["rating"], "Stars", step=0.25, extent=[1, 5]))
        else:
            st.caption("No ratings in the current selection.")
        if schema:
            st.markdown("**Field fill rate** (share of products with a value)")
            fill = pd.DataFrame([{"field": n, "filled": float(view[n].map(
                lambda x: x not in (None, "", [], {}) and not (isinstance(x, float) and pd.isna(x))).mean())
                if n in view and len(view) else 0.0} for n in schema.names])
            charts.show(charts.hbar(fill, "field", "filled", "Filled", fmt=".0%", sort=None))
    with st.expander("Table view of the charts"):
        num = [c for c in ("price", "rating", "review_count", "discount_pct", "best_sellers_rank") if c in view]
        if num:
            st.dataframe(view[num].describe().T, width="stretch")

# -------------------------------------------------------------- dataset card
with tab_card:
    card = output_dir() / "DATASET_CARD.md"
    st.markdown(card.read_text(encoding="utf-8") if card.exists() else "No dataset card yet.")
