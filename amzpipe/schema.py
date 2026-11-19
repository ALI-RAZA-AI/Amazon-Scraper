"""Load config/schema.yaml, validate it, and coerce / check values against it."""
from __future__ import annotations

import re
import string
from dataclasses import dataclass, field, fields as dc_fields
from pathlib import Path
from typing import Any

import yaml

from .transforms import DERIVED, TRANSFORMS

VALID_TYPES = {"string", "int", "float", "bool", "list", "dict", "enum"}
SOURCE_KINDS = {"css", "css_all", "table", "detail_bullets", "jsonld", "regex_url", "regex_html",
                "regex_html_all", "computed", "spec", "specs", "exists", "derived"}
SOURCE_MODIFIERS = {"attr", "key", "regex"}
COMPUTED_KEYS = {"url", "marketplace", "scraped_at", "asin"}
RAG_MODES = {"fill", "taxonomy"}
EXTRA_COLUMNS = ["text", "label", "quality", "split"]  # added by the exporter


@dataclass
class FieldSpec:
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    sources: list[dict] = field(default_factory=list)
    transforms: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    choices: list[str] = field(default_factory=list)
    rag: dict | None = None
    default: Any = None
    default_from_marketplace: bool = False
    weight: float = 1.0  # share in the quality score; 0 = not counted

    @property
    def is_pipeline_filled(self) -> bool:
        """computed / derived fields don't count towards the quality score."""
        return any("computed" in s or "derived" in s for s in self.sources)


FIELD_KEYS = {f.name for f in dc_fields(FieldSpec)}


@dataclass
class Schema:
    dataset: dict
    training: dict
    fields: list[FieldSpec]
    export: dict
    reviews: dict
    review_fields: list[FieldSpec]

    @property
    def primary_key(self) -> str:
        return self.dataset.get("primary_key", "asin")

    def field(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    @property
    def names(self) -> list[str]:
        return [f.name for f in self.fields]

    @property
    def reviews_enabled(self) -> bool:
        return bool(self.reviews.get("enabled", True) and self.review_fields)


# ---------------------------------------------------------------- loading
def _regex_ok(pat, where: str, errors: list[str]):
    try:
        re.compile(pat)
    except (re.error, TypeError) as e:
        errors.append(f"{where}: invalid regex {pat!r} ({e})")


def _parse_fields(raw_list, where: str, errors: list[str], warnings: list[str]) -> list[FieldSpec]:
    specs, seen = [], set()
    if raw_list is not None and not isinstance(raw_list, list):
        errors.append(f"'{where}s' must be a list")
        return specs
    for i, f in enumerate(raw_list or []):
        if not isinstance(f, dict) or not f.get("name"):
            errors.append(f"{where} #{i + 1}: every field needs a 'name'")
            continue
        name = f["name"]
        unknown = set(f) - FIELD_KEYS
        if unknown:
            errors.append(f"{where} '{name}': unknown key(s) {sorted(unknown)}. Allowed: {sorted(FIELD_KEYS)}")
            continue
        f = {k: v for k, v in f.items() if v is not None or k in ("default", "rag")}
        try:
            spec = FieldSpec(**f)
        except TypeError as e:
            errors.append(f"{where} '{name}': {e}")
            continue
        spec.sources = spec.sources or []
        spec.transforms = spec.transforms or []
        spec.constraints = spec.constraints or {}
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(name)):
            errors.append(f"{where} '{name}': names may only use letters, digits and _")
        if name in seen:
            errors.append(f"Duplicate {where} name '{name}'")
        seen.add(name)
        if spec.type not in VALID_TYPES:
            errors.append(f"{where} '{name}': unknown type '{spec.type}'. Use one of {sorted(VALID_TYPES)}")
        if spec.type == "enum" and not spec.choices:
            errors.append(f"{where} '{name}': enum fields need 'choices'")
        if not isinstance(spec.weight, (int, float)) or spec.weight < 0:
            errors.append(f"{where} '{name}': weight must be a number >= 0")
        for src in spec.sources:
            if not isinstance(src, dict):
                errors.append(f"{where} '{name}': each source must be a mapping like {{css: '#id'}}, got {src!r}")
                continue
            kinds = set(src) - SOURCE_MODIFIERS
            if not kinds and "attr" in src:
                pass  # {attr: id} = attribute of the container element itself (reviews)
            elif len(kinds) != 1 or not kinds <= SOURCE_KINDS:
                errors.append(f"{where} '{name}': unknown source {src!r}. Source types: {sorted(SOURCE_KINDS)}")
                continue
            if "derived" in src and src["derived"] not in DERIVED:
                errors.append(f"{where} '{name}': unknown derived function '{src['derived']}'. "
                              f"Available: {sorted(DERIVED)}")
            if "computed" in src and src["computed"] not in COMPUTED_KEYS:
                warnings.append(f"{where} '{name}': computed key '{src['computed']}' is not built in "
                                f"({sorted(COMPUTED_KEYS)}) and will usually be empty")
            for k in ("regex_url", "regex_html", "regex_html_all", "regex"):
                if k in src:
                    _regex_ok(src[k], f"{where} '{name}'", errors)
        for t in spec.transforms:
            if t not in TRANSFORMS:
                errors.append(f"{where} '{name}': unknown transform '{t}'. Available: {sorted(TRANSFORMS)}")
        if "regex" in spec.constraints:
            _regex_ok(spec.constraints["regex"], f"{where} '{name}' constraint", errors)
        if spec.rag:
            if not isinstance(spec.rag, dict):
                errors.append(f"{where} '{name}': 'rag' must be a mapping")
            else:
                if spec.rag.get("mode", "fill") not in RAG_MODES:
                    errors.append(f"{where} '{name}': rag.mode must be one of {sorted(RAG_MODES)}")
                if spec.rag.get("on_unverified", "keep") not in ("keep", "drop"):
                    errors.append(f"{where} '{name}': rag.on_unverified must be keep or drop")
                for p in spec.rag.get("patterns") or []:
                    _regex_ok(p, f"{where} '{name}' rag pattern", errors)
        if not spec.sources and not spec.rag:
            warnings.append(f"{where} '{name}' has no sources and no rag block, so it will always be empty")
        specs.append(spec)
    return specs


def parse_schema(raw: dict) -> tuple[Schema | None, list[str], list[str]]:
    """Validate a schema dict. Returns (schema or None, errors, warnings)."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return None, ["The schema file must be a YAML mapping"], []
    dataset = raw.get("dataset") or {}
    training = raw.get("training") or {}
    export = raw.get("export") or {}
    reviews = raw.get("reviews") or {}
    fields_ = _parse_fields(raw.get("fields"), "field", errors, warnings)
    review_fields = _parse_fields(reviews.get("fields"), "review field", errors, warnings)
    names = {f.name for f in fields_}
    if not fields_:
        errors.append("The schema has no fields")

    pk = dataset.get("primary_key", "asin")
    if fields_ and pk not in names:
        errors.append(f"dataset.primary_key '{pk}' is not a field")

    label = training.get("label_field")
    if label and label not in names:
        errors.append(f"training.label_field '{label}' is not a field")
    tpl = training.get("text_template") or ""
    try:
        placeholders = {p[1] for p in string.Formatter().parse(tpl) if p[1]}
    except ValueError as e:
        errors.append(f"training.text_template: {e} (use {{{{ and }}}} for literal braces)")
        placeholders = set()
    for p in sorted(placeholders - names):
        warnings.append(f"training.text_template uses {{{p}}}, which is not a field (it will be empty)")
    for t in training.get("targets") or []:
        if t not in names:
            errors.append(f"training.targets: '{t}' is not a field")
        elif t in placeholders:
            warnings.append(f"training.text_template contains the target {{{t}}} - a model trained on "
                            f"`text` would see the answer (target leakage)")
    splits = training.get("splits") or {}
    if splits:
        if any(not isinstance(v, (int, float)) or v < 0 for v in splits.values()):
            errors.append("training.splits: fractions must be numbers >= 0")
        elif abs(sum(splits.values()) - 1.0) > 0.01:
            errors.append(f"training.splits must add up to 1.0 (now {sum(splits.values()):.2f})")

    allowed_cols = names | set(EXTRA_COLUMNS)
    for c in export.get("columns") or []:
        if c not in allowed_cols:
            errors.append(f"export.columns: '{c}' is not a field or one of {EXTRA_COLUMNS}")
    for c in export.get("extra_columns") or []:
        if c not in EXTRA_COLUMNS:
            errors.append(f"export.extra_columns: '{c}' must be one of {EXTRA_COLUMNS}")
    review_names = {f.name for f in review_fields} | {"split"}
    for c in export.get("review_columns") or []:
        if c not in review_names:
            errors.append(f"export.review_columns: '{c}' is not a review field")
    for key, valid in (("rename", allowed_cols), ("review_rename", review_names)):
        rename = export.get(key) or {}
        if not isinstance(rename, dict):
            errors.append(f"export.{key} must be a mapping {{field: csv_column}}")
            continue
        for k in rename:
            if k not in valid:
                warnings.append(f"export.{key}: '{k}' is not a column")
        dupes = [v for v in set(rename.values()) if list(rename.values()).count(v) > 1]
        if dupes:
            errors.append(f"export.{key}: column name(s) used twice: {dupes}")

    if errors:
        return None, errors, warnings
    return Schema(dataset=dataset, training=training, fields=fields_, export=export,
                  reviews=reviews, review_fields=review_fields), errors, warnings


def load_schema(path: str | Path) -> Schema:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    schema, errors, _ = parse_schema(raw)
    if errors:
        raise ValueError(f"Invalid schema {path}:\n  - " + "\n  - ".join(errors))
    return schema


# ---------------------------------------------------------------- coercion
def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def coerce(spec: FieldSpec, value: Any) -> Any:
    """Convert a raw value to the field's declared type. Returns None if impossible."""
    if _empty(value):
        return None
    t = spec.type
    try:
        if t == "string":
            if isinstance(value, list):
                value = " ".join(map(str, value))
            return str(value).strip() or None
        if t == "int":
            if isinstance(value, str):
                m = re.search(r"-?[\d,]+", value)
                value = m.group(0).replace(",", "") if m else None
            return int(float(value)) if value is not None else None
        if t == "float":
            if isinstance(value, str):
                m = re.search(r"-?\d[\d,]*\.?\d*", value)
                value = m.group(0).replace(",", "") if m else None
            return float(value) if value is not None else None
        if t == "bool":
            if isinstance(value, str):
                return value.strip().lower() in {"true", "yes", "1", "y", "in stock"}
            return bool(value)
        if t == "list":
            if isinstance(value, str):
                return [value]
            return [v for v in value if not _empty(v)] or None
        if t == "dict":
            return dict(value) or None
        if t == "enum":
            s = str(value).strip()
            for c in spec.choices:
                if s.lower() == str(c).lower():
                    return c
            return None
    except (ValueError, TypeError):
        return None
    return value


def check(spec: FieldSpec, value: Any) -> list[str]:
    """Return a list of constraint violations (empty list = OK)."""
    errs = []
    if _empty(value):
        if spec.required:
            errs.append(f"{spec.name}: required but missing")
        return errs
    c = spec.constraints or {}
    if "min" in c and isinstance(value, (int, float)) and value < c["min"]:
        errs.append(f"{spec.name}: {value} < min {c['min']}")
    if "max" in c and isinstance(value, (int, float)) and value > c["max"]:
        errs.append(f"{spec.name}: {value} > max {c['max']}")
    if isinstance(value, str):
        if "min_length" in c and len(value) < c["min_length"]:
            errs.append(f"{spec.name}: shorter than {c['min_length']}")
        if "regex" in c and not re.fullmatch(c["regex"], value):
            errs.append(f"{spec.name}: '{value}' does not match {c['regex']}")
    return errs


def enforce_limits(spec: FieldSpec, value: Any) -> Any:
    """Truncate overly long strings / lists instead of rejecting them."""
    c = spec.constraints or {}
    if isinstance(value, str) and "max_length" in c:
        value = value[: c["max_length"]]
    if isinstance(value, list) and "max_items" in c:
        value = value[: c["max_items"]]
    return value
