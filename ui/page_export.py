"""Export page: pick the CSV columns, their order and headers; preview; download the CSV files."""
import io
import zipfile
from datetime import datetime

import pandas as pd
import streamlit as st
import yaml
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from amzpipe.export import flatten, re_export, table
from amzpipe.schema import EXTRA_COLUMNS, parse_schema
from ui.common import (SCHEMA_PATH, dump_rt, load_products, load_reviews, load_rt, output_dir, save_schema,
                       settings, show_problems)

st.title("Export CSV")
st.caption("Choose which columns the CSV files contain, in what order and under which header. "
           "This only reformats the data you already have: no re-scraping.")

raw = load_rt(SCHEMA_PATH)
draft_base = yaml.safe_load(dump_rt(raw))
schema, errors, _ = parse_schema(draft_base)
if schema is None:
    show_problems(errors)
    st.stop()
rows, reviews = load_products(), load_reviews()
ex = draft_base.get("export") or {}
first = rows[0] if rows else {}
EXTRA_HELP = {"text": "training text (template)", "label": "classification label", "quality": "0-1 record quality",
              "split": "train / validation / test"}


def seq(items) -> CommentedSeq:
    s = CommentedSeq(items)
    s.fa.set_flow_style()
    return s


# ------------------------------------------------------------ product columns
names = schema.names
chosen = list(ex.get("columns") or names)
extras = [c for c in ex.get("extra_columns", EXTRA_COLUMNS) if c not in chosen]
included = chosen + extras
order = included + [c for c in names + EXTRA_COLUMNS if c not in included]
rename = ex.get("rename") or {}
types = {f.name: f.type for f in schema.fields}
ed = pd.DataFrame([{
    "include": c in included, "#": i + 1, "column": c, "header": rename.get(c, c),
    "kind": types.get(c, EXTRA_HELP.get(c, "")),
    "example": str(flatten(first.get(c)) if first.get(c) is not None else "")[:120],
} for i, c in enumerate(order)])

st.subheader("products.csv · train / validation / test.csv")
edited = st.data_editor(ed, hide_index=True, width="stretch", height=min(42 + 35 * len(ed), 700),
                        disabled=["column", "kind", "example"], key="export_cols", column_config={
                            "include": st.column_config.CheckboxColumn("Include", width="small"),
                            "#": st.column_config.NumberColumn("#", min_value=1, step=1, width="small",
                                                               help="Column position in the CSV"),
                            "column": st.column_config.TextColumn("Field"),
                            "header": st.column_config.TextColumn("CSV header", required=True),
                            "kind": st.column_config.TextColumn("Type"),
                            "example": st.column_config.TextColumn("Example (first product)", width="large")})
sep = st.text_input("List separator (bullets, images, breadcrumbs in one cell)", ex.get("list_separator", " | "))

# ------------------------------------------------------------- review columns
rnames = [f.name for f in schema.review_fields]
rv_included = list(ex.get("review_columns") or rnames)
rv_rename = ex.get("review_rename") or {}
rv_ed = None
if schema.reviews_enabled:
    st.subheader("reviews.csv")
    rv_order = rv_included + [c for c in rnames if c not in rv_included]
    rv_ed = st.data_editor(pd.DataFrame([{
        "include": c in rv_included, "#": i + 1, "column": c, "header": rv_rename.get(c, c)}
        for i, c in enumerate(rv_order)]), hide_index=True, width="stretch", disabled=["column"], key="export_rv",
        column_config={"include": st.column_config.CheckboxColumn("Include", width="small"),
                       "#": st.column_config.NumberColumn("#", min_value=1, step=1, width="small"),
                       "header": st.column_config.TextColumn("CSV header", required=True)})
    st.caption("A `split` column is always added so each review follows its product into the same split.")


# ------------------------------------------------------------------- draft
def build_export(edited: pd.DataFrame, rv_ed) -> dict:
    inc = edited[edited["include"]].sort_values("#", kind="stable")
    cols = list(inc["column"])
    field_cols = [c for c in cols if c in names]
    extra_cols = [c for c in cols if c in EXTRA_COLUMNS]
    if field_cols == names and cols == names + extra_cols:  # everything, default order: stay open-ended
        out = {"columns": [], "extra_columns": extra_cols}
    else:
        out = {"columns": cols, "extra_columns": []}
    out["rename"] = {r.column: r.header.strip() for r in inc.itertuples() if r.header and r.header.strip() != r.column}
    out["list_separator"] = sep
    if rv_ed is not None:
        rinc = rv_ed[rv_ed["include"]].sort_values("#", kind="stable")
        rcols = list(rinc["column"])
        out["review_columns"] = [] if rcols == rnames else rcols
        out["review_rename"] = {r.column: r.header.strip() for r in rinc.itertuples()
                                if r.header and r.header.strip() != r.column}
    return out


new_export = build_export(edited, rv_ed)
draft = {**draft_base, "export": {**ex, **new_export}}
dschema, derrs, dwarns = parse_schema(draft)
show_problems(derrs, [w for w in dwarns if w.startswith("export")])

if dschema and rows:
    st.subheader("Preview")
    st.dataframe(table(dschema, rows[:15]), hide_index=True, width="stretch")

if st.button("Save format and re-export CSVs", type="primary", icon=":material/save:", disabled=bool(derrs)):
    e = raw.setdefault("export", CommentedMap())
    e["columns"], e["extra_columns"] = seq(new_export["columns"]), seq(new_export["extra_columns"])
    e["rename"] = CommentedMap(new_export["rename"])
    e["rename"].fa.set_flow_style()
    e["list_separator"] = new_export["list_separator"]
    if "review_columns" in new_export:
        e["review_columns"] = seq(new_export["review_columns"])
        e["review_rename"] = CommentedMap(new_export["review_rename"])
        e["review_rename"].fa.set_flow_style()
    ok, errs, warns = save_schema(raw)
    if not ok:
        show_problems(errs, warns)
    elif rows:
        saved_schema, _, _ = parse_schema(yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8")))
        with st.spinner("Writing CSV files…"):
            re_export(saved_schema, settings().get("output") or {})
        st.toast("Saved and re-exported", icon=":material/check_circle:")
        st.rerun()
    else:
        st.toast("Saved. Scrape some products to get CSV files.", icon=":material/check_circle:")

# --------------------------------------------------------------- downloads
st.divider()
st.subheader("Download")
out = output_dir()
files = [p for p in (out / n for n in ("products.csv", "train.csv", "validation.csv", "test.csv", "reviews.csv",
                                       "rejected.csv", "DATASET_CARD.md")) if p.exists()]
if not files:
    st.info("No CSV files yet. Scrape some products first.")
    st.stop()
csv = out / "products.csv"
if csv.exists() and SCHEMA_PATH.stat().st_mtime > csv.stat().st_mtime + 1:
    st.warning("The data structure changed after these files were written. Use **Re-export CSVs** (or "
               "**Rebuild dataset** if you changed fields) on the Data structure page.", icon=":material/update:")
cols = st.columns(4)
for i, p in enumerate(files):
    n = sum(1 for _ in open(p, encoding="utf-8-sig")) - 1 if p.suffix == ".csv" else None
    with cols[i % 4]:
        st.download_button(f"{p.name}" + (f" · {max(n, 0):,} rows" if n is not None else ""), p.read_bytes(),
                           file_name=p.name, mime="text/csv" if p.suffix == ".csv" else "text/markdown",
                           icon=":material/download:", width="stretch")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
    for p in files:
        z.write(p, p.name)
st.download_button("Everything as .zip", buf.getvalue(), type="primary", icon=":material/folder_zip:",
                   file_name=f"amazon_dataset_{datetime.now():%Y%m%d}.zip", mime="application/zip")
st.caption(f"Files are in `{out}`. CSVs are UTF-8 with BOM, so Excel opens them correctly.")
