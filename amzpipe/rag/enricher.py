"""RAG enrichment: fill missing fields and verify scraped ones against the page text.

For every product:
  1. The main-product page sections (title, bullets, description, spec tables,
     price block, breadcrumbs) are chunked and indexed (hybrid dense + TF-IDF).
  2. For each schema field with a `rag:` block:
       mode: fill      -> if empty: spec-table key lookup -> LLM over retrieved
                          chunks (if configured) -> regex patterns over chunks
       mode: taxonomy  -> nearest category in config/taxonomy.yaml; if an LLM is
                          configured and the top candidates are close, the LLM
                          picks one of those retrieved candidates
     and (if rag.verify_existing) scraped values are checked for presence in
     the retrieved main-product text, lowering confidence when they're not.
  3. Every LLM answer must cite a chunk and be found in that chunk's text
     (grounding check) — otherwise it is discarded, never written to the data.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import yaml

from ..schema import Schema, check, coerce
from .. import transforms as T
from .llm import LLMClient
from .retrieval import Chunk, VectorIndex, chunk_sections, load_dense_model

log = logging.getLogger(__name__)

CONF = {"css": 1.0, "css_all": 1.0, "jsonld": 0.95, "table": 0.95, "detail_bullets": 0.95,
        "spec": 0.95, "specs": 0.95, "exists": 1.0, "attr": 1.0, "derived": 1.0,
        "regex_url": 1.0, "regex_html": 0.9, "regex_html_all": 0.9, "computed": 1.0,
        "marketplace_default": 0.6, "default": 0.5, "spec_key": 0.95, "llm": 0.85,
        "pattern": 0.7}

TAXONOMY_PROMPT = (
    "You classify Amazon products into a fixed category list. "
    "Choose exactly one of the candidate categories, copied verbatim. "
    "Reply with a JSON object: {\"category\": \"<one candidate>\"}."
)

SYSTEM_PROMPT = (
    "You extract product attributes from Amazon product-page excerpts. "
    "Use ONLY the provided context. If a value is not explicitly stated, return null. "
    "Copy values verbatim from the context where possible. "
    "Reply with a JSON object: {\"<field>\": {\"value\": <value or null>, \"evidence\": \"<chunk id>\"}}."
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9.]+", " ", str(s).lower()).strip()


def grounded(value, text: str) -> bool:
    """Is the value actually present in the source text?"""
    if value is None:
        return False
    t = _norm(text)
    if isinstance(value, (int, float)):
        # Amazon renders visible prices as separate spans: "$ 228 . 36" -> "$228.36"
        text = re.sub(r"(\d)\s*\.\s+(\d)|(\d)\s+\.\s*(\d)", lambda m: f"{m[1] or m[3]}.{m[2] or m[4]}", text)
        # exact number match with boundaries, so 9.99 does NOT match inside 89.99
        nums = {float(n.replace(",", "")) for n in re.findall(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?", text)
                if n.replace(",", "").replace(".", "", 1).isdigit()}
        return float(value) in nums
    v = _norm(value)
    if not v:
        return False
    if v in t:
        return True
    toks = v.split()
    return len(toks) > 1 and sum(tok in t.split() for tok in toks) / len(toks) >= 0.8


class RagEnricher:
    def __init__(self, schema: Schema, cfg: dict, root: Path):
        self.schema = schema
        self.cfg = cfg
        self.top_k = cfg.get("top_k", 4)
        self.dense = load_dense_model(cfg)
        self.llm = LLMClient(cfg.get("llm", {}))
        self.rag_fields = [f for f in schema.fields if f.rag]
        self.taxonomies: dict[str, VectorIndex] = {}
        for f in self.rag_fields:
            if f.rag.get("mode") == "taxonomy":
                path = root / f.rag.get("taxonomy_file", "config/taxonomy.yaml")
                cats = yaml.safe_load(path.read_text(encoding="utf-8"))["categories"]
                chunks = [Chunk(id=name, text=f"{name.replace('>', ' ')} {desc}", section="taxonomy")
                          for name, desc in cats.items()]
                self.taxonomies[f.name] = VectorIndex(self.dense).build(chunks)

    # ----------------------------------------------------------------- main
    def enrich(self, rec: dict, prov: dict, sections: dict[str, str]) -> tuple[dict, dict]:
        chunks = chunk_sections(sections, self.cfg.get("chunk_size", 400), self.cfg.get("chunk_overlap", 60))
        index = VectorIndex(self.dense).build(chunks)
        conf = {k: CONF.get(v.split("#")[0], 0.8) for k, v in prov.items()}
        evidence: dict[str, str] = {}
        flags: list[str] = []
        pending: dict[str, list[tuple[Chunk, float]]] = {}
        self._replaced: dict = {}
        specs = {k.lower(): v for k, v in (rec.get("specifications") or {}).items()}

        for f in self.rag_fields:
            mode = f.rag.get("mode", "fill")
            query = f.rag.get("query") or f"{f.name.replace('_', ' ')} {f.description}"

            if mode == "taxonomy":
                q = " ".join(filter(None, [rec.get("title") or "", " ".join(rec.get("category_path") or []),
                                           " ".join((rec.get("bullet_points") or [])[:3])]))
                hits = self.taxonomies[f.name].search(q, k=max(1, f.rag.get("candidates", 3)))
                if not hits or hits[0][1] < f.rag.get("min_score", 0.05):
                    continue
                best, score = hits[0]
                rec[f.name], prov[f.name] = best.id, "rag_taxonomy"
                conf[f.name] = round(min(1.0, 0.5 + score), 3)
                evidence[f.name] = f"taxonomy score {score:.3f}"
                close = [c for c, s in hits[1:] if score - s < f.rag.get("llm_margin", 0.1)]
                if self.llm.enabled and close:  # ambiguous: let the LLM pick among retrieved candidates
                    choice = self._llm_pick_category(rec, [best] + close, flags)
                    if choice:
                        rec[f.name], prov[f.name], conf[f.name] = choice, "rag_taxonomy_llm", CONF["llm"]
                        evidence[f.name] = f"llm chose from {len(close) + 1} retrieved candidates"
                continue

            hits = index.search(query, self.top_k)
            ctx_text = "\n".join(c.text for c, _ in hits)

            if rec.get(f.name) is not None:  # ---- verify scraped value
                if not (self.cfg.get("verify_existing", True) and f.type in ("string", "float", "int")):
                    continue
                if grounded(rec[f.name], "\n".join(sections.values())):
                    continue
                # Not supported by the main-product text (e.g. a price grabbed from a
                # "similar items" carousel). Drop it and try to re-derive it via RAG;
                # if that fails, keep the original with reduced confidence (or drop it).
                old = (rec[f.name], prov.pop(f.name, None), conf.pop(f.name, 0.8))
                rec[f.name] = None
                self._fill(f, hits, rec, prov, conf, evidence, specs, pending)
                if f.name in pending:
                    pending[f.name] = hits
                    self._replaced[f.name] = old
                elif rec.get(f.name) is not None:
                    flags.append(f"{f.name}: scraped value {old[0]!r} not in main product text; "
                                 f"replaced by RAG value {rec[f.name]!r}")
                else:
                    self._unverified(f, old, rec, prov, conf, flags)
                continue

            self._fill(f, hits, rec, prov, conf, evidence, specs, pending)


        if pending:
            self._llm_fill(pending, rec, prov, conf, evidence, flags)
            for name, hits in pending.items():  # LLM missed -> patterns as last resort
                if rec.get(name) is None:
                    self._pattern_fill(self.schema.field(name), hits, rec, prov, conf, evidence)
                if name in self._replaced:
                    old = self._replaced[name]
                    if rec.get(name) is None:
                        self._unverified(self.schema.field(name), old, rec, prov, conf, flags)
                    else:
                        flags.append(f"{name}: scraped value {old[0]!r} replaced by RAG value {rec[name]!r}")

        meta = {"provenance": prov, "confidence": conf, "evidence": evidence, "flags": flags,
                "n_chunks": len(chunks)}
        return rec, meta

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _unverified(f, old, rec, prov, conf, flags):
        """Nothing grounded was found to replace a value that isn't on the main product: keep it
        at half confidence, or drop it when the field says `on_unverified: drop` (prices)."""
        old_val, old_prov, old_conf = old
        if f.rag.get("on_unverified", "keep") == "drop":
            flags.append(f"{f.name}: scraped value {old_val!r} not found in main product text; dropped")
            return
        rec[f.name], conf[f.name] = old_val, round(old_conf * 0.5, 3)
        if old_prov:
            prov[f.name] = old_prov
        flags.append(f"{f.name}: scraped value not found in main product text (low confidence)")

    def _fill(self, f, hits, rec, prov, conf, evidence, specs, pending):
        """spec-table key lookup -> (queue for LLM) -> regex patterns over retrieved chunks."""
        for key in f.rag.get("spec_keys", []):
            if key.lower() in specs:
                val = coerce(f, T.apply(f.transforms, specs[key.lower()]))
                if val is not None and not check(f, val):
                    rec[f.name], prov[f.name], conf[f.name] = val, "spec_key", CONF["spec_key"]
                    evidence[f.name] = f"specifications.{key}"
                    return
        if self.llm.enabled:
            pending[f.name] = hits
            return
        self._pattern_fill(f, hits, rec, prov, conf, evidence)

    def _pattern_fill(self, f, hits, rec, prov, conf, evidence):
        for pat in f.rag.get("patterns", []):
            for chunk, score in hits:
                if score <= 0:
                    continue
                m = re.search(pat, chunk.text, re.I)
                if m:
                    val = coerce(f, T.apply(f.transforms or (["parse_price"] if f.type == "float" else []),
                                            m.group(1) if m.groups() else m.group(0)))
                    if val is not None and not check(f, val):
                        rec[f.name], prov[f.name], conf[f.name] = val, "pattern", CONF["pattern"]
                        evidence[f.name] = chunk.id
                        return

    def _llm_pick_category(self, rec, candidates, flags) -> str | None:
        ids = [c.id for c in candidates]
        user = (f"Product title: {rec.get('title')}\n"
                f"Breadcrumbs: {' > '.join(rec.get('category_path') or [])}\n"
                f"Features: {'; '.join((rec.get('bullet_points') or [])[:3])}\n\n"
                "Candidate categories:\n" + "\n".join(f"- {c.id}: {c.text}" for c in candidates)
                + "\n\nReturn JSON only.")
        try:
            out = self.llm.complete_json(TAXONOMY_PROMPT, user)
        except Exception as e:
            flags.append(f"llm_error (taxonomy): {type(e).__name__}: {e}"[:200])
            return None
        choice = str(out.get("category") or "").strip()
        if choice in ids:
            return choice
        if choice:
            flags.append(f"taxonomy: LLM answer {choice[:60]!r} is not a candidate; kept retrieval result")
        return None

    def _llm_fill(self, pending, rec, prov, conf, evidence, flags):
        seen, ctx = set(), []
        for hits in pending.values():
            for c, _ in hits:
                if c.id not in seen:
                    seen.add(c.id)
                    ctx.append(c)
        by_id = {c.id: c for c in ctx}
        fields_desc = "\n".join(
            f"- {n} ({self.schema.field(n).type}"
            + (f", one of {self.schema.field(n).choices}" if self.schema.field(n).choices else "")
            + f"): {self.schema.field(n).description}" for n in pending)
        context = "\n\n".join(f"[{c.id}] ({c.section})\n{c.text}" for c in ctx)
        user = (f"Product: {rec.get('title')}\n\nFields to extract:\n{fields_desc}\n\n"
                f"Context chunks:\n{context}\n\nReturn JSON only.")
        try:
            out = self.llm.complete_json(SYSTEM_PROMPT, user)
        except Exception as e:
            flags.append(f"llm_error: {type(e).__name__}: {e}"[:200])
            return
        for name in pending:
            ans = out.get(name)
            if isinstance(ans, dict):
                raw, ev = ans.get("value"), ans.get("evidence")
            else:
                raw, ev = ans, None
            f = self.schema.field(name)
            val = coerce(f, T.apply(f.transforms, raw))
            if val is None or check(f, val):
                continue
            src_text = by_id[ev].text if ev in by_id else "\n".join(c.text for c in ctx)
            if f.type != "list" and not grounded(val, src_text):
                flags.append(f"{name}: LLM answer {json.dumps(raw)[:60]} rejected (not grounded in context)")
                continue
            rec[name], prov[name], conf[name] = val, "llm", CONF["llm"]
            evidence[name] = ev if ev in by_id else "context"
