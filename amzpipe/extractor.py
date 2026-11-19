"""Schema-driven extraction from a product page.

Each field in schema.yaml lists `sources`; they are tried in order and the
first non-empty value wins. Provenance (which source matched) is recorded so
the RAG step and dataset users know how each value was obtained.

Reviews use the same machinery, with CSS selectors scoped to each review
element listed under `reviews.containers`.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from . import transforms as T
from .schema import FieldSpec, Schema, check, coerce, enforce_limits

# Main-product sections only: this text is the RAG knowledge base, so carousels,
# ads, "customers also bought" and customer reviews are deliberately left out.
TEXT_SECTIONS = {
    "title": ["#productTitle"],
    "bullets": ["#feature-bullets"],
    "description": ["#productDescription", "#aplus"],
    "breadcrumbs": ["#wayfinding-breadcrumbs_feature_div"],
    "price": ["#corePrice_feature_div", "#corePriceDisplay_desktop_feature_div", "#apex_desktop"],
    "byline": ["#bylineInfo"],
    "variations": ["#twister", "#variation_color_name"],
}
# Every table / bullet list Amazon uses for specifications, merged into one dict.
SPEC_CONTAINERS = ["#productDetails_techSpec_section_1", "#productDetails_techSpec_section_2",
                   "#productDetails_detailBullets_sections1", "#productOverview_feature_div",
                   "#detailBullets_feature_div", "#productDetails_feature_div", "#prodDetails", "#tech"]


class Page:
    def __init__(self, html: str, url: str):
        self.html = html
        self.url = url
        self.soup = BeautifulSoup(html, "lxml")
        for t in self.soup(["script", "style", "noscript"]):
            # nested tags (e.g. <script> inside <noscript>) are already gone with their parent
            if not t.decomposed and t.get("type") != "application/ld+json":
                t.decompose()
        self._jsonld = None
        self._specs = None

    # ---------------------------------------------------------- JSON-LD
    @property
    def jsonld(self) -> dict:
        if self._jsonld is None:
            self._jsonld = {}
            for s in BeautifulSoup(self.html, "lxml").select("script[type='application/ld+json']"):
                try:
                    data = json.loads(s.string or "")
                except (json.JSONDecodeError, TypeError):
                    continue
                items = data if isinstance(data, list) else data.get("@graph", [data])
                for it in items:
                    if isinstance(it, dict) and it.get("@type") in ("Product", ["Product"]):
                        self._jsonld = it
                        break
        return self._jsonld

    def jsonld_get(self, path: str):
        cur = self.jsonld
        for part in path.split("."):
            if isinstance(cur, list):
                cur = cur[0] if cur else None
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    # ------------------------------------------------------------ specs
    @property
    def specs(self) -> dict:
        """All specification tables / detail bullets merged (first occurrence of a key wins)."""
        if self._specs is None:
            self._specs = {}
            for sel in SPEC_CONTAINERS:
                for el in self.soup.select(sel):
                    for k, v in table_to_dict(el).items():
                        self._specs.setdefault(k, v)
        return self._specs

    # ----------------------------------------------------- page sections
    def sections(self) -> dict[str, str]:
        """Readable text per page section — the knowledge base for RAG."""
        out = {}
        for name, sels in TEXT_SECTIONS.items():
            parts = []
            for sel in sels:
                for el in self.soup.select(sel):
                    txt = el.get_text(" ", strip=True)
                    if txt:
                        parts.append(txt)
            if parts:
                out[name] = "\n".join(dict.fromkeys(parts))
        if self.specs:
            out["specs"] = "\n".join(f"{k}: {v}" for k, v in self.specs.items())
        ld = self.jsonld
        if ld:  # structured data embedded in the page also counts as main-product text
            lines = []
            for k in ("name", "description", "sku", "color", "material", "weight"):
                if isinstance(ld.get(k), (str, int, float)):
                    lines.append(f"{k}: {ld[k]}")
            for path in ("brand.name", "offers.price", "offers.priceCurrency",
                         "aggregateRating.ratingValue", "aggregateRating.reviewCount"):
                v = self.jsonld_get(path)
                if v is not None:
                    lines.append(f"{path}: {v}")
            if lines:
                out["structured_data"] = "\n".join(lines)
        return out


def table_to_dict(el) -> dict:
    d = {}
    for tr in el.select("tr"):
        k, v = tr.select_one("th"), tr.select_one("td:last-child")
        if k is None:
            tds = tr.select("td")
            if len(tds) >= 2:
                k, v = tds[0], tds[-1]
        if k is not None and v is not None and k is not v:
            key = T.clean_text(k.get_text(" ", strip=True))
            val = T.clean_text(v.get_text(" ", strip=True))
            if key and val:
                d[key.rstrip(":").strip()] = val
    for li in el.select("li"):  # detail-bullets style "Key : Value"
        txt = T.clean_text(li.get_text(" ", strip=True)) or ""
        m = re.match(r"^([^:]{2,60}?)\s*:\s*(.+)$", txt)
        if m and m.group(1).strip() not in d:
            d[m.group(1).strip()] = m.group(2).strip()
    return d


def _regex(src: dict, val):
    """Optional `regex:` modifier: keep group 1 (or the whole match) of the extracted text."""
    if not src.get("regex") or val is None:
        return val
    if isinstance(val, list):
        return [x for x in (_regex(src, v) for v in val) if x]
    m = re.search(src["regex"], str(val))
    return (m.group(1) if m.groups() else m.group(0)) if m else None


def _run_source(src: dict, page: Page, ctx: dict, root=None):
    root = page.soup if root is None else root
    html = page.html if root is page.soup else str(root)
    if "computed" in src:
        key = src["computed"]
        if key == "url":
            return page.url
        if key == "scraped_at":
            return datetime.now(timezone.utc).isoformat(timespec="seconds")
        return ctx.get(key)
    if "derived" in src:
        return None  # filled after RAG by apply_derived()
    if "spec" in src:
        keys = src["spec"] if isinstance(src["spec"], list) else [src["spec"]]
        low = {k.lower(): v for k, v in page.specs.items()}
        return _regex(src, next((low[k.lower()] for k in keys if k.lower() in low), None))
    if "specs" in src:
        return dict(page.specs) or None
    if "exists" in src:
        return root.select_one(src["exists"]) is not None
    if "css_all" in src:
        return _regex(src, [e.get_text(" ", strip=True) for e in root.select(src["css_all"])
                            if e.get_text(strip=True)])
    if "css" in src:
        el = root.select_one(src["css"])
        if el is None:
            return None
        return _regex(src, el.get(src["attr"]) if src.get("attr") else el.get_text(" ", strip=True))
    if "table" in src:
        el = root.select_one(src["table"])
        if el is None:
            return None
        d = table_to_dict(el)
        if src.get("key"):
            return next((v for k, v in d.items() if k.lower() == src["key"].lower()), None)
        return d
    if "detail_bullets" in src:
        el = root.select_one(src["detail_bullets"])
        return table_to_dict(el) if el is not None else None
    if "jsonld" in src:
        return page.jsonld_get(src["jsonld"])
    if "regex_url" in src:
        m = re.search(src["regex_url"], page.url)
        return m.group(1) if m else None
    if "regex_html" in src:
        m = re.search(src["regex_html"], html)
        return m.group(1) if m else None
    if "regex_html_all" in src:
        return list(dict.fromkeys(re.findall(src["regex_html_all"], html))) or None
    if "attr" in src:  # attribute of the container element itself, e.g. a review's id
        return _regex(src, root.get(src["attr"]) if root is not page.soup else None)
    raise ValueError(f"Unknown source type: {src}")


def extract_fields(fields: list[FieldSpec], page: Page, ctx: dict, root=None) -> tuple[dict, dict]:
    """Return (record, provenance) where provenance[field] = source description."""
    rec, prov = {}, {}
    for spec in fields:
        value, used = None, None
        for i, src in enumerate(spec.sources):
            raw = _run_source(src, page, ctx, root)
            raw = T.apply(spec.transforms, raw, ctx)
            val = coerce(spec, raw)
            if val is not None:
                value, used = val, f"{next(iter(k for k in src if k not in ('attr', 'key', 'regex')), 'attr')}#{i}"
                break
        if value is None and spec.default_from_marketplace:
            value = T.MARKETPLACE_CURRENCY.get(ctx.get("marketplace", ""))
            used = "marketplace_default" if value else None
        if value is None and spec.default is not None:
            value, used = spec.default, "default"
        rec[spec.name] = enforce_limits(spec, value)
        if used:
            prov[spec.name] = used
    return rec, prov


def extract(schema: Schema, page: Page, ctx: dict) -> tuple[dict, dict]:
    return extract_fields(schema.fields, page, ctx)


def apply_derived(fields: list[FieldSpec], rec: dict, prov: dict, conf: dict):
    """Compute `{derived: name}` fields from the finished record (after RAG)."""
    for spec in fields:
        for src in spec.sources:
            if "derived" in src:
                val = coerce(spec, T.DERIVED[src["derived"]](rec))
                if val is not None:
                    rec[spec.name] = enforce_limits(spec, val)
                    prov[spec.name], conf[spec.name] = "derived", 1.0
                    break


def extract_reviews(schema: Schema, page: Page, ctx: dict) -> list[dict]:
    """Customer reviews shown on the product page (no login needed for these)."""
    cfg = schema.reviews
    pk = cfg.get("primary_key", "review_id")
    limit = cfg.get("max_per_product", 20)
    out, seen = [], set()
    for sel in cfg.get("containers") or ["[data-hook='review']"]:
        for el in page.soup.select(sel):
            rec, _ = extract_fields(schema.review_fields, page, ctx, root=el)
            key = rec.get(pk)
            if key in seen or any(check(f, rec.get(f.name)) for f in schema.review_fields if f.required):
                continue
            seen.add(key)
            out.append(rec)
            if len(out) >= limit:
                return out
    return out
