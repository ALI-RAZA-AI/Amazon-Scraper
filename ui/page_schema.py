"""Data structure page: edit config/schema.yaml (fields, training format, reviews, labels) visually.

Every save is validated first (nothing invalid is written), keeps the comments
in the YAML file, and leaves a backup in config/backups/.
"""
import pandas as pd
import streamlit as st
import yaml
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from amzpipe.export import training_text
from amzpipe.schema import SOURCE_KINDS, VALID_TYPES, parse_schema
from amzpipe.transforms import DERIVED, TRANSFORMS
from ui.common import (BACKUPS, SCHEMA_PATH, TAXONOMY_PATH, dump_rt, job_status, llm_env, load_products,
                       load_rt, rt_yaml, save_schema, save_text, show_problems, start_job,
                       validate_schema_text)

st.title("Data structure")
st.caption("What each product record contains and how it becomes training data. Saved to "
           "`config/schema.yaml` (explained in `SCHEMA.md`).")

raw = load_rt(SCHEMA_PATH)
schema, errors, warnings = validate_schema_text(SCHEMA_PATH.read_text(encoding="utf-8"))
if errors:
    st.error("schema.yaml currently has errors; fix them in the YAML tab.")
    show_problems(errors)
products = load_products()
busy = job_status()["running"]


def flow(d: dict) -> CommentedMap:
    """{a: 1, b: 2} on one line, like the hand-written entries in schema.yaml."""
    m = CommentedMap(d)
    m.fa.set_flow_style()
    return m


def flow_seq(items) -> CommentedSeq:
    s = CommentedSeq(items)
    s.fa.set_flow_style()
    return s


def after_save(ok, errs, warns, needs: str):
    """needs: 'rebuild' (fields changed) or 'export' (only the output format changed)."""
    if not ok:
        show_problems(errs, warns)
        st.error("Not saved: fix the problems above.")
        return
    st.session_state["schema_saved"] = (needs, warns)
    st.rerun()


saved = st.session_state.pop("schema_saved", None)
if saved:
    needs, warns = saved
    show_problems((), warns)
    if needs == "rebuild":
        st.success("Saved. Rebuild the dataset to apply the new structure to the products you already "
                   "scraped (re-reads cached pages, no downloads).", icon=":material/check_circle:")
    else:
        st.success("Saved. Re-export to rewrite the CSV files (takes a second).", icon=":material/check_circle:")
if products:
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Rebuild dataset from cache", icon=":material/refresh:", disabled=busy,
                 help="Needed after changing fields, sources, transforms, reviews or labels"):
        start_job(["rebuild"], llm_env())
        st.switch_page("ui/page_scrape.py")
    if b2.button("Re-export CSVs", icon=":material/file_download:", disabled=busy,
                 help="Enough after changing the training text, label, splits or CSV columns"):
        start_job(["export"])
        st.switch_page("ui/page_scrape.py")

tab_fields, tab_add, tab_train, tab_reviews, tab_tax, tab_yaml = st.tabs(
    ["Fields", "Add a field", "Training format", "Reviews", "Labels (taxonomy)", "YAML"])


def summarize_sources(f) -> str:
    out = []
    for s in f.get("sources") or []:
        kind = next((k for k in s if k not in ("attr", "key", "regex")), "attr")
        val = s.get(kind, s.get("attr"))
        out.append(f"{kind}: {val if isinstance(val, str) else ', '.join(map(str, val)) if isinstance(val, list) else val}")
    return " → ".join(out)


# ------------------------------------------------------------------- fields
with tab_fields:
    st.markdown("Rename, retype, reorder (`#` column) or delete fields (select rows, then the bin icon). "
                "Renames are carried into the training text, label and CSV settings automatically.")
    fields = raw.get("fields") or []
    ed = pd.DataFrame([{
        "_orig": f["name"], "#": i + 1, "name": f["name"], "type": f.get("type", "string"),
        "required": bool(f.get("required", False)), "description": str(f.get("description") or "").strip(),
        "sources": summarize_sources(f), "rag": (f.get("rag") or {}).get("mode", "fill") if f.get("rag") else "",
    } for i, f in enumerate(fields)])
    edited = st.data_editor(
        ed, key="fields_editor", hide_index=True, num_rows="delete", width="stretch", height=560,
        disabled=["sources", "rag"],
        column_config={
            "_orig": None,
            "#": st.column_config.NumberColumn("#", min_value=1, step=1, width="small"),
            "name": st.column_config.TextColumn("Name", required=True, validate=r"^[A-Za-z_][A-Za-z0-9_]*$"),
            "type": st.column_config.SelectboxColumn("Type", options=sorted(VALID_TYPES), required=True),
            "required": st.column_config.CheckboxColumn("Required", help="Products without it are rejected"),
            "description": st.column_config.TextColumn("Description", width="large"),
            "sources": st.column_config.TextColumn("Sources (edit below)", width="large"),
            "rag": st.column_config.TextColumn("RAG", help="fill = filled/verified by retrieval; "
                                                           "taxonomy = label chosen by retrieval"),
        })
    if st.button("Save field changes", type="primary", icon=":material/save:"):
        edited = edited.sort_values("#", kind="stable")
        index = {f["name"]: i for i, f in enumerate(fields)}
        renames = {}
        for _, row in edited.iterrows():  # only touch what changed, so YAML styling/comments survive
            f = fields[index[row["_orig"]]]
            if row["name"] != row["_orig"]:
                renames[row["_orig"]] = row["name"]
                f["name"] = row["name"]
            if row["type"] != f.get("type", "string"):
                f["type"] = row["type"]
            if row["required"] != bool(f.get("required", False)):
                if row["required"]:
                    f["required"] = True
                else:
                    del f["required"]
            if row["description"] != str(f.get("description") or "").strip():
                if row["description"]:
                    f["description"] = row["description"]
                else:
                    f.pop("description", None)
        deleted = set(index) - set(edited["_orig"])
        new_order = [index[o] for o in edited["_orig"]]
        if new_order != list(range(len(fields))):  # deleted or reordered: rebuild, carrying comments along
            seq = CommentedSeq([fields[i] for i in new_order])
            for new_i, old_i in enumerate(new_order):
                if old_i in fields.ca.items:
                    seq.ca.items[new_i] = fields.ca.items[old_i]
            raw["fields"] = seq
        tr, ex = raw.setdefault("training", CommentedMap()), raw.setdefault("export", CommentedMap())
        for old, new in renames.items():  # carry renames everywhere a field is referenced
            if tr.get("text_template"):
                tr["text_template"] = LiteralScalarString(str(tr["text_template"]).replace(f"{{{old}}}", f"{{{new}}}"))
            if tr.get("label_field") == old:
                tr["label_field"] = new
            if ex.get("rename") and old in ex["rename"]:
                ex["rename"][new] = ex["rename"].pop(old)
        if tr.get("targets"):
            tr["targets"] = flow_seq([renames.get(t, t) for t in tr["targets"] if t not in deleted])
        if ex.get("columns"):
            ex["columns"] = flow_seq([renames.get(c, c) for c in ex["columns"] if c not in deleted])
        for d in deleted:
            if ex.get("rename") and d in ex["rename"]:
                del ex["rename"][d]
        if tr.get("label_field") in deleted:
            tr["label_field"] = None
        after_save(*save_schema(raw), needs="rebuild")

    st.divider()
    st.subheader("Edit one field in detail")
    st.caption("Sources, transforms, constraints and RAG settings. See SCHEMA.md §3–6 for every option.")
    names = [f["name"] for f in fields]
    if names:
        pick = st.selectbox("Field", names, key="edit_field_pick")
        idx = names.index(pick)
        block = dump_rt(CommentedSeq([fields[idx]]))
        new_block = st.text_area("YAML", block, height=320, key=f"edit_block_{pick}")
        with st.expander("Cheat sheet"):
            st.markdown(
                f"**Source types:** {', '.join(f'`{k}`' for k in sorted(SOURCE_KINDS))} "
                "(+ optional `attr:`, `key:`, `regex:`)  \n"
                f"**Transforms:** {', '.join(f'`{k}`' for k in TRANSFORMS)}  \n"
                f"**Derived functions:** {', '.join(f'`{k}`' for k in DERIVED)}  \n"
                "**RAG:** `mode: fill` with `query`, `spec_keys`, `patterns`; `mode: taxonomy` for labels")
        if st.button("Save this field", icon=":material/save:"):
            try:
                parsed = rt_yaml().load(new_block)
                if not isinstance(parsed, list) or len(parsed) != 1 or not isinstance(parsed[0], dict):
                    raise ValueError("Keep exactly one `- name: ...` block")
            except Exception as e:
                st.error(f"Could not read the YAML: {e}")
            else:
                old_name, new_name = pick, parsed[0].get("name")
                fields[idx] = parsed[0]
                if new_name != old_name:
                    st.warning("Renamed here: update the training text / CSV settings if they used the old name.")
                after_save(*save_schema(raw), needs="rebuild")

# ---------------------------------------------------------------- add field
with tab_add:
    st.markdown("Add a new column to every product. Pick where its value comes from; RAG can find values "
                "that have no fixed place on the page.")
    with st.form("add_field"):
        c1, c2, c3 = st.columns([2, 1, 1])
        name = c1.text_input("Field name", placeholder="battery_life")
        ftype = c2.selectbox("Type", ["string", "float", "int", "bool", "list", "dict"])
        required = c3.checkbox("Required", help="Products without a value are rejected")
        desc = st.text_input("Description", placeholder='Battery life with unit, e.g. "40 hours"',
                             help="Also shown to the LLM when RAG extraction is on")
        how = st.radio("Where does the value come from?", [
            "Specification table row (most product attributes)",
            "CSS selector on the page",
            "RAG only: search the product text",
            "Derived from other fields",
        ])
        spec_keys = st.text_input("Spec table row name(s), comma-separated",
                                  placeholder="Battery Life, Battery Average Life",
                                  help="Used by: specification table, and as the first RAG step")
        css = st.text_input("CSS selector", placeholder="#productOverview_feature_div .po-battery-life td:last-child")
        attr = st.text_input("Attribute (optional, e.g. src, title)")
        css_all = st.checkbox("All matches (makes a list)")
        query = st.text_input("RAG search query", placeholder="battery life hours playtime charge")
        pattern = st.text_input("RAG regex pattern (optional, group 1 = value)",
                                placeholder=r"([0-9]+\s?(?:hours|hrs|h))\b")
        derived = st.selectbox("Derived function", sorted(DERIVED))
        default_tf = {"string": ["clean_text"], "float": ["parse_float"], "int": ["parse_int"]}.get(ftype, [])
        tfs = st.multiselect("Transforms (applied in order)", list(TRANSFORMS), default=default_tf)
        after = st.selectbox("Insert after", [f["name"] for f in raw.get("fields") or []],
                             index=max(0, len(raw.get("fields") or []) - 4))
        in_text = st.checkbox("Also add it to the training text")
        submitted = st.form_submit_button("Add field", type="primary", icon=":material/add:")
    if submitted:
        keys = [k.strip() for k in spec_keys.split(",") if k.strip()]
        f = CommentedMap(name=name.strip(), type=ftype)
        if required:
            f["required"] = True
        if desc:
            f["description"] = desc
        sources = CommentedSeq()
        rag = None
        if how.startswith("Specification"):
            if keys:
                sources.append(flow({"spec": keys if len(keys) > 1 else keys[0]}))
            rag = CommentedMap(mode="fill", query=query or name.replace("_", " "), spec_keys=keys)
        elif how.startswith("CSS"):
            if css:
                src = {"css_all" if css_all else "css": css}
                if attr:
                    src["attr"] = attr
                sources.append(flow(src))
        elif how.startswith("RAG"):
            rag = CommentedMap(mode="fill", query=query or name.replace("_", " "))
            if keys:
                rag["spec_keys"] = keys
        else:
            sources.append(flow({"derived": derived}))
        if rag is not None and pattern and how[:3] in ("Spe", "RAG"):
            rag["patterns"] = [pattern]
        f["sources"] = sources
        if tfs and not how.startswith("Derived"):
            f["transforms"] = flow_seq(tfs)
        if rag is not None:
            for k in ("spec_keys", "patterns"):
                if k in rag:
                    rag[k] = flow_seq(rag[k])
            f["rag"] = rag
        names = [x["name"] for x in raw["fields"]]
        raw["fields"].insert(names.index(after) + 1 if after in names else len(names), f)
        if in_text and name:
            tr = raw.setdefault("training", CommentedMap())
            label = name.replace("_", " ").capitalize()
            tr["text_template"] = LiteralScalarString(str(tr.get("text_template") or "").rstrip("\n")
                                                      + f"\n{label}: {{{name.strip()}}}\n")
        after_save(*save_schema(raw), needs="rebuild")

# ----------------------------------------------------------- training format
with tab_train:
    tr = raw.get("training") or CommentedMap()
    field_names = [f["name"] for f in raw.get("fields") or []]
    left, right = st.columns([3, 2], gap="large")
    with left:
        tpl = st.text_area("Training text template (`text` column)", str(tr.get("text_template") or ""),
                           height=220, help="{field} placeholders are filled from each product. Lists are "
                                            "joined with '; '. Don't include targets you want to predict.")
        st.caption("Placeholders: " + " ".join(f"`{{{n}}}`" for n in field_names))
        lab_opts = ["(none: no label column)"] + field_names
        cur = tr.get("label_field")
        label = st.selectbox("Label field (`label` column, for classification)", lab_opts,
                             index=lab_opts.index(cur) if cur in lab_opts else 0)
        nums = [f["name"] for f in raw.get("fields") or [] if f.get("type") in ("float", "int")]
        targets = st.multiselect("Regression targets (reported in the dataset card; flagged if they leak "
                                 "into the text)", nums, default=[t for t in tr.get("targets") or [] if t in nums])
    with right:
        sp = dict(tr.get("splits") or {"train": 0.8, "validation": 0.1, "test": 0.1})
        st.markdown("**Splits** (stratified by label)")
        new_sp = {}
        for k, v in sp.items():
            new_sp[k] = st.number_input(k, 0.0, 1.0, float(v), 0.05, key=f"split_{k}")
        st.caption(f"Total: {sum(new_sp.values()):.2f} (must be 1.00)")
        seed = st.number_input("Random seed", 0, 10**9, int(tr.get("seed", 42)), 1)

    draft = yaml.safe_load(dump_rt(raw))
    draft["training"] = {"text_template": tpl, "label_field": None if label.startswith("(none") else label,
                         "targets": targets, "splits": new_sp, "seed": int(seed)}
    dschema, derrs, dwarns = parse_schema(draft)
    show_problems(derrs, dwarns)
    if products and dschema:
        st.markdown("**Preview** on a scraped product")
        sample = st.selectbox("Product", range(min(len(products), 50)),
                              format_func=lambda i: f"{products[i].get('asin')} · {(products[i].get('title') or '')[:70]}")
        st.code(training_text(dschema, products[sample]) or "(empty)", language=None, wrap_lines=True)
        if dschema.training.get("label_field"):
            st.caption(f"label = **{products[sample].get(dschema.training['label_field'])}**")
    if st.button("Save training format", type="primary", icon=":material/save:", disabled=bool(derrs)):
        t = raw.setdefault("training", CommentedMap())
        t["text_template"] = LiteralScalarString(tpl if tpl.endswith("\n") else tpl + "\n")
        t["label_field"] = draft["training"]["label_field"]
        t["targets"] = flow_seq(targets)
        t["splits"] = flow({k: round(v, 4) for k, v in new_sp.items()})
        t["seed"] = int(seed)
        after_save(*save_schema(raw), needs="export")

# ------------------------------------------------------------------ reviews
with tab_reviews:
    rv = raw.get("reviews") or CommentedMap()
    st.markdown("Individual reviews go to `reviews.csv`, linked by `asin`, in the same split as their product. "
                "Note: amazon.com currently shows individual reviews only to signed-in users, so logged-out "
                "pages yield none; the review *signals* (star histogram, \"Customers say\" summary, aspects) are "
                "product fields instead. These settings apply to pages that do contain reviews, e.g. pages saved "
                "while signed in and loaded with `run --html-dir`.")
    enabled = st.toggle("Collect reviews", value=bool(rv.get("enabled", True)))
    max_n = st.number_input("Max reviews per product", 1, 100, int(rv.get("max_per_product", 20)))
    rf = rv.get("fields") or []
    st.dataframe(pd.DataFrame([{"name": f["name"], "type": f.get("type", "string"),
                                "required": bool(f.get("required")), "sources": summarize_sources(f)}
                               for f in rf]), hide_index=True, width="stretch")
    rv_block = st.text_area("Review fields (YAML, selectors are relative to one review)",
                            dump_rt(rf) if rf else "", height=260)
    if st.button("Save review settings", type="primary", icon=":material/save:"):
        try:
            parsed = rt_yaml().load(rv_block) if rv_block.strip() else CommentedSeq()
        except Exception as e:
            st.error(f"Could not read the YAML: {e}")
        else:
            r = raw.setdefault("reviews", CommentedMap())
            r["enabled"], r["max_per_product"], r["fields"] = enabled, int(max_n), parsed
            after_save(*save_schema(raw), needs="rebuild")

# ----------------------------------------------------------------- taxonomy
with tab_tax:
    st.markdown("The label set for `category_normalized`. RAG embeds each label with its keywords and picks "
                "the closest one for every product (title + breadcrumbs + bullets). Richer keywords give "
                "better matches. Use `>` for hierarchy.")
    tax = load_rt(TAXONOMY_PATH)
    cats = tax.get("categories") or {}
    tdf = pd.DataFrame([{"label": k, "keywords": str(v)} for k, v in cats.items()])
    tedit = st.data_editor(tdf, num_rows="dynamic", hide_index=True, width="stretch", height=520,
                           column_config={"label": st.column_config.TextColumn("Label", required=True, width="medium"),
                                          "keywords": st.column_config.TextColumn("Keywords / description",
                                                                                  width="large")})
    if st.button("Save labels", type="primary", icon=":material/save:"):
        clean = tedit.dropna(subset=["label"])
        clean = clean[clean["label"].str.strip() != ""]
        if clean["label"].duplicated().any():
            st.error("Each label must be unique.")
        else:
            new = CommentedMap()
            for _, r in clean.iterrows():
                new[r["label"].strip()] = str(r["keywords"]).strip() if pd.notna(r["keywords"]) else ""
            tax["categories"] = new
            save_text(TAXONOMY_PATH, dump_rt(tax))
            st.session_state["schema_saved"] = ("rebuild", [])
            st.rerun()

# --------------------------------------------------------------------- yaml
with tab_yaml:
    st.markdown("The whole file. Validated before saving; the previous version is kept in `config/backups/`.")
    text = st.text_area("config/schema.yaml", SCHEMA_PATH.read_text(encoding="utf-8"), height=600,
                        key="yaml_full")
    c1, c2 = st.columns([1, 4])
    if c1.button("Validate", icon=":material/rule:"):
        s, e, w = validate_schema_text(text)
        show_problems(e, w)
        if s:
            st.success(f"Valid: {len(s.fields)} fields, {len(s.review_fields)} review fields.")
    if c2.button("Save", type="primary", icon=":material/save:"):
        after_save(*save_schema(text), needs="rebuild")
    backups = sorted(BACKUPS.glob("schema-*.yaml"), reverse=True) if BACKUPS.exists() else []
    if backups:
        with st.expander(f"Backups ({len(backups)})"):
            b = st.selectbox("Restore a previous version", backups, format_func=lambda p: p.stem)
            if st.button("Restore this version", icon=":material/history:"):
                after_save(*save_schema(b.read_text(encoding="utf-8")), needs="rebuild")
