"""Quality scoring, splits and export.

Output folder (settings.yaml -> output.dir):
  products.csv                  all accepted products   <- the training data
  train.csv / validation.csv / test.csv                 <- same rows, split
  reviews.csv                   top reviews, linked by asin, same split as their product
  rejected.csv                  pages that failed validation, with the reason
  DATASET_CARD.md               fill rates, sources, label + target statistics
  products.jsonl / reviews.jsonl  full internal store (every field + _meta provenance);
                                  the frontend and `amzpipe export` rebuild the CSVs from these

Which columns the CSVs contain, their order and their names come from the
`export:` section of schema.yaml, so changing them needs no re-scrape.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

import pandas as pd

from .paths import resolve
from .schema import EXTRA_COLUMNS, Schema, check

PRODUCTS_STORE = "products.jsonl"
REVIEWS_STORE = "reviews.jsonl"


def quality(schema: Schema, rec: dict, meta: dict) -> tuple[float, list[str]]:
    """0-1 score: confidence-weighted fill rate of non-computed fields (required fields count
    double, each field times its `weight`); 0 if a required field is missing."""
    errors = [e for f in schema.fields for e in check(f, rec.get(f.name))]
    if any(e.endswith("required but missing") for e in errors):
        return 0.0, errors
    conf = meta.get("confidence", {})
    scored = [f for f in schema.fields if not f.is_pipeline_filled]

    def w(f):
        return (2 if f.required else 1) * f.weight
    total = sum(w(f) for f in scored)
    got = sum(w(f) * conf.get(f.name, 0.8) for f in scored if rec.get(f.name) not in (None, "", [], {}))
    score = got / total if total else 1.0
    return round(score * (0.8 if errors else 1.0), 3), errors


def training_text(schema: Schema, rec: dict) -> str:
    tpl = schema.training.get("text_template")
    if not tpl:
        return ""
    vals = {}
    for k, v in rec.items():
        if isinstance(v, list):
            v = "; ".join(map(str, v))
        elif isinstance(v, dict):
            v = "; ".join(f"{a}: {b}" for a, b in v.items())
        vals[k] = "" if v is None else v

    class D(dict):
        def __missing__(self, key):
            return ""
    return tpl.format_map(D(vals)).strip()


# ------------------------------------------------------------------- rows
def build_rows(schema: Schema, records: list[dict]) -> list[dict]:
    """Field values + text / label / quality / split. Works on fresh records and on
    rows re-read from products.jsonl (then text, label and split are recomputed)."""
    label = schema.training.get("label_field")
    rows = []
    for r in records:
        meta = r.get("_meta") or {}
        row = {k: r.get(k) for k in schema.names}
        row["text"] = training_text(schema, r)
        row["label"] = r.get(label) if label else None
        row["quality"] = meta.get("quality", r.get("quality"))
        row["_meta"] = meta
        rows.append(row)
    assign_splits(schema, rows)
    return rows


def assign_splits(schema: Schema, rows: list[dict]):
    """Stratified by label when there is one; reproducible via training.seed."""
    split_cfg = schema.training.get("splits") or {"train": 1.0}
    rng = random.Random(schema.training.get("seed", 42))
    groups: dict = {}
    for row in sorted(rows, key=lambda r: str(r.get(schema.primary_key))):
        groups.setdefault(row.get("label"), []).append(row)
    names = list(split_cfg)
    for _, grp in sorted(groups.items(), key=lambda kv: str(kv[0])):
        rng.shuffle(grp)
        n, start = len(grp), 0
        for i, name in enumerate(names):
            end = n if i == len(names) - 1 else start + round(split_cfg[name] * n)
            for row in grp[start:end]:
                row["split"] = name
            start = end


def flatten(v, sep=" | "):
    """A list / dict value as one CSV cell."""
    if isinstance(v, list):
        return sep.join(map(str, v))
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return v


def product_columns(schema: Schema) -> list[str]:
    exp = schema.export
    cols = list(exp.get("columns") or schema.names)
    extras = exp.get("extra_columns", EXTRA_COLUMNS)
    if not schema.training.get("label_field"):
        extras = [c for c in extras if c != "label"]
    return cols + [c for c in extras if c not in cols]


def review_columns(schema: Schema) -> list[str]:
    cols = list(schema.export.get("review_columns") or [f.name for f in schema.review_fields])
    return cols + ([] if "split" in cols else ["split"])


def table(schema: Schema, rows: list[dict], kind: str = "products") -> pd.DataFrame:
    """The CSV as a DataFrame: selected columns, in order, lists joined, columns renamed."""
    exp = schema.export
    sep = exp.get("list_separator", " | ")
    if kind == "reviews":
        cols, specs = review_columns(schema), {f.name: f for f in schema.review_fields}
        rename = exp.get("review_rename") or {}
    else:
        cols, specs = product_columns(schema), {f.name: f for f in schema.fields}
        rename = exp.get("rename") or {}
    df = pd.DataFrame([{c: flatten(r.get(c), sep) for c in cols} for r in rows], columns=cols)
    for c in cols:  # keep whole numbers whole (no 12483.0) even when some values are missing
        t = specs[c].type if c in specs else None
        if t == "int":
            df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
        elif t == "float" or c == "quality":
            df[c] = pd.to_numeric(df[c], errors="coerce")
        elif t == "bool":
            df[c] = df[c].astype("boolean")
    return df.rename(columns=rename)


def write_csv(df: pd.DataFrame, path: Path):
    # utf-8-sig: Excel on Windows otherwise garbles £, €, accents, emoji
    df.to_csv(path, index=False, encoding="utf-8-sig")


# ----------------------------------------------------------------- export
def export(schema: Schema, records: list[dict], rejected: list[dict], cfg: dict,
           reviews: list[dict] = ()) -> dict:
    out = resolve(cfg.get("dir", "data/output"))
    out.mkdir(parents=True, exist_ok=True)
    rows = build_rows(schema, records)
    _write_jsonl(out / PRODUCTS_STORE, rows)
    rej = pd.DataFrame([{
        "url": r.get("url"),
        "asin": (r.get("record") or {}).get("asin"),
        "title": (r.get("record") or {}).get("title"),
        "reason": r.get("reason"),
        "quality": r.get("quality"),
        "errors": "; ".join(r.get("errors") or []),
    } for r in rejected], columns=["url", "asin", "title", "reason", "quality", "errors"])
    write_csv(rej, out / "rejected.csv")
    return write_exports(schema, rows, list(reviews), cfg, out)


def re_export(schema: Schema, cfg: dict) -> dict:
    """Rebuild every CSV from the internal store with the current schema
    (text template, label, splits, columns, names). No scraping, no RAG."""
    out = resolve(cfg.get("dir", "data/output"))
    if not (out / PRODUCTS_STORE).exists():
        raise FileNotFoundError(f"No {PRODUCTS_STORE} in {out}. Run a scrape first.")
    rows = build_rows(schema, read_jsonl(out / PRODUCTS_STORE))
    reviews = read_jsonl(out / REVIEWS_STORE)
    _write_jsonl(out / PRODUCTS_STORE, rows)
    return write_exports(schema, rows, reviews, cfg, out)


def write_exports(schema: Schema, rows: list[dict], reviews: list[dict], cfg: dict, out: Path) -> dict:
    split_of = {r.get(schema.primary_key): r.get("split") for r in rows}
    for rv in reviews:  # a review always lands in the same split as its product (no leakage)
        rv["split"] = split_of.get(rv.get("asin"))
    _write_jsonl(out / REVIEWS_STORE, reviews)

    paths = {}
    df = table(schema, rows)
    write_csv(df, out / "products.csv")
    paths["products"] = str(out / "products.csv")
    split_col = (schema.export.get("rename") or {}).get("split", "split")
    row_split = pd.Series([r.get("split") for r in rows], index=df.index, dtype=object)
    for name in schema.training.get("splits") or {}:
        p = out / f"{name}.csv"
        write_csv(df[row_split == name].drop(columns=[split_col], errors="ignore"), p)
        paths[name] = str(p)
    if schema.reviews_enabled:
        write_csv(table(schema, reviews, "reviews"), out / "reviews.csv")
        paths["reviews"] = str(out / "reviews.csv")
    if (out / "rejected.csv").exists():
        paths["rejected"] = str(out / "rejected.csv")

    fmts = cfg.get("formats") or ["csv"]
    if "jsonl" in fmts:
        for name in schema.training.get("splits") or {}:
            _write_jsonl(out / f"{name}.jsonl",
                         [{k: v for k, v in r.items() if k != "_meta"} for r in rows if r.get("split") == name])
    if "parquet" in fmts:
        try:
            df.to_parquet(out / "products.parquet", index=False)
            paths["parquet"] = str(out / "products.parquet")
        except ImportError:
            pass

    n_rejected = len(pd.read_csv(out / "rejected.csv")) if (out / "rejected.csv").exists() else 0
    (out / "DATASET_CARD.md").write_text(dataset_card(schema, rows, reviews, n_rejected), encoding="utf-8")
    paths["card"] = str(out / "DATASET_CARD.md")
    return paths


def _write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# ----------------------------------------------------------- dataset card
def dataset_card(schema: Schema, rows: list[dict], reviews: list[dict], n_rejected: int) -> str:
    n = len(rows)
    lines = [f"# {schema.dataset.get('name')} v{schema.dataset.get('version')}", "",
             str(schema.dataset.get("description", "")).strip(), "",
             f"- Products: **{n}** (rejected: {n_rejected})",
             f"- Reviews: **{len(reviews)}**",
             f"- Splits: {dict(Counter(r.get('split') for r in rows))}",
             f"- CSV columns: {', '.join(map(str, table(schema, rows[:1]).columns))}", ""]
    if schema.training.get("label_field"):
        lines += [f"## Label distribution (`{schema.training['label_field']}`)", ""]
        for lab, c in Counter(r.get("label") for r in rows).most_common():
            lines.append(f"- {lab}: {c}")
        lines.append("")
    targets = schema.training.get("targets") or []
    if targets:
        lines += ["## Regression targets", "", "| target | filled | min | mean | max |", "|---|---|---|---|---|"]
        for t in targets:
            vals = [r.get(t) for r in rows if isinstance(r.get(t), (int, float))]
            if vals:
                lines.append(f"| {t} | {len(vals)}/{n} | {min(vals):g} | {sum(vals) / len(vals):.2f} | {max(vals):g} |")
            else:
                lines.append(f"| {t} | 0/{n} | - | - | - |")
        lines.append("")
    lines += ["## Field fill rate and sources", "", "| field | type | filled | sources used |",
              "|---|---|---|---|"]
    for f in schema.fields:
        filled = sum(r.get(f.name) not in (None, "", [], {}) for r in rows)
        srcs = Counter(r.get("_meta", {}).get("provenance", {}).get(f.name, "-").split("#")[0]
                       for r in rows if r.get(f.name) not in (None, "", [], {}))
        pct = f"{100 * filled / n:.0f}%" if n else "-"
        lines.append(f"| {f.name} | {f.type} | {pct} | {', '.join(f'{k}×{v}' for k, v in srcs.items())} |")
    return "\n".join(lines) + "\n"
