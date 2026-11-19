"""End-to-end pipeline: inputs -> fetch -> extract -> RAG enrich -> derive -> validate -> export."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import yaml

from . import discovery
from .export import export, quality, re_export
from .extractor import Page, apply_derived, extract, extract_reviews
from .fetcher import NOT_DELIVERABLE, BlockedError, CacheMissError, Fetcher, FetchError
from .inputs import parse_asins
from .paths import ROOT, resolve
from .rag import RagEnricher
from .schema import load_schema
from .transforms import MARKETPLACE_CURRENCY

log = logging.getLogger(__name__)
INPUTS_FILE = "run_inputs.json"  # every page of the dataset, so it can be rebuilt / appended to

__all__ = ["Pipeline", "ROOT"]


class Pipeline:
    def __init__(self, schema_path=None, settings_path=None, overrides: dict | None = None):
        self.schema_path = Path(schema_path or ROOT / "config/schema.yaml")
        self.schema = load_schema(self.schema_path)
        self.settings = yaml.safe_load(Path(settings_path or ROOT / "config/settings.yaml")
                                       .read_text(encoding="utf-8")) or {}
        for k, v in (overrides or {}).items():  # e.g. {"rag.llm.provider": "openai"}
            cur = self.settings
            *parts, last = k.split(".")
            for p in parts:
                if not isinstance(cur.get(p), dict):
                    cur[p] = {}
                cur = cur[p]
            cur[last] = v
        self.marketplace = self.settings.get("marketplace", "amazon.com")
        self.out_dir = resolve((self.settings.get("output") or {}).get("dir", "data/output"))
        self._fetcher = None
        self.enricher = RagEnricher(self.schema, self.settings.get("rag") or {}, ROOT)

    @property
    def fetcher(self) -> Fetcher:
        if self._fetcher is None:
            self._fetcher = Fetcher(self.settings.get("fetch") or {}, self.marketplace)
        return self._fetcher

    # ---------------------------------------------------------- discovery
    def discover(self, queries: list[str], max_products: int) -> list[str]:
        dcfg = self.settings.get("discovery", {})
        asins: list[str] = []
        for q in queries:
            for page in range(1, dcfg.get("max_search_pages", 3) + 1):
                url = discovery.search_url(self.marketplace, q, page)
                try:
                    html = self.fetcher.get(url)
                except BlockedError as e:
                    log.error("%s", e)
                    break
                found = discovery.parse_search_page(html, dcfg.get("skip_sponsored", True))
                log.info("search '%s' p%d: %d products", q, page, len(found))
                asins += [a for a in found if a not in asins]
                if len(asins) >= max_products or not discovery.has_next_page(html):
                    break
            if len(asins) >= max_products:
                break
        return asins[:max_products]

    # ------------------------------------------------------------ process
    def process_html(self, html: str, url: str) -> dict:
        page = Page(html, url)
        ctx = {"marketplace": self.marketplace}
        rec, prov = extract(self.schema, page, ctx)
        rec, meta = self.enricher.enrich(rec, prov, page.sections())
        apply_derived(self.schema.fields, rec, meta["provenance"], meta["confidence"])
        self._check_market(page, rec, meta)
        q, errors = quality(self.schema, rec, meta)
        meta["quality"], meta["errors"] = q, errors
        rec["_meta"] = meta
        if self.schema.reviews_enabled:
            rec["_reviews"] = extract_reviews(self.schema, page, {**ctx, "asin": rec.get("asin"), "url": url})
        return rec

    def _check_market(self, page: Page, rec: dict, meta: dict):
        """Flag pages Amazon rendered for the wrong country (hidden or converted prices)."""
        expected = MARKETPLACE_CURRENCY.get(self.marketplace)
        cur = rec.get("currency")
        if expected and cur and cur != expected:
            meta["flags"].append(f"currency: page shows {cur} but {self.marketplace} sells in {expected}; "
                                 "Amazon may have converted prices for your location (use a proxy in that country)")
        if NOT_DELIVERABLE in page.html:
            meta["flags"].append("delivery: Amazon says this item can't ship to the session's delivery location, "
                                 "so price / reviews may be missing (check fetch.delivery_zip or use a proxy in "
                                 f"the {self.marketplace} country)")

    # ---------------------------------------------------------------- runs
    def run(self, queries=(), asins=(), html_dir=None, max_products=50, append=False,
            progress: Callable[[dict], None] | None = None, stop_file=None) -> dict:
        """Scrape and build the dataset.

        asins: ASINs and/or product URLs. max_products limits search results only.
        append: also keep every page of the previous run (re-read from cache).
        """
        jobs: list[dict] = self.load_inputs() if append else []
        if html_dir:
            for p in sorted(Path(html_dir).glob("*.htm*")):
                m = re.search(r"([A-Z0-9]{10})", p.stem)
                url = discovery.product_url(self.marketplace, m.group(1)) if m else f"file://{p.name}"
                jobs.append({"url": url, "file": str(p.resolve())})
        ids, bad = parse_asins("\n".join(asins))
        for b in bad:
            log.warning("Ignored input (not an ASIN or product URL): %s", b)
        if queries:
            ids += [a for a in self.discover(list(queries), max_products) if a not in ids]
        jobs += [{"url": discovery.product_url(self.marketplace, a)} for a in ids]
        jobs = list({j["url"]: j for j in jobs}.values())  # de-duplicate, keep order
        self._save_inputs(jobs)
        return self._process(jobs, progress, stop_file)

    def rebuild(self, progress=None, stop_file=None) -> dict:
        """Re-extract every page of the last run from the cache: no network.
        Use after changing fields, sources, transforms or RAG settings."""
        jobs = self.load_inputs()
        if not jobs:
            raise RuntimeError(f"No previous run found ({self.out_dir / INPUTS_FILE}). Scrape something first.")
        self.settings.setdefault("fetch", {})["cache_only"] = True
        return self._process(jobs, progress, stop_file)

    def re_export(self) -> dict:
        """Rewrite the CSVs from products.jsonl (text template, label, splits, columns)."""
        return re_export(self.schema, self.settings.get("output") or {})

    def _process(self, jobs: list[dict], progress, stop_file) -> dict:
        records, rejected, reviews, seen = [], [], [], set()
        pk = self.schema.primary_key
        min_q = (self.settings.get("output") or {}).get("min_quality", 0.5)
        stop = Path(stop_file) if stop_file else None
        status, skipped, n, done = "finished", 0, len(jobs), 0

        def report(i, **extra):
            if progress:
                progress({"state": "running", "done": i, "total": n, "ok": len(records),
                          "rejected": len(rejected), "skipped": skipped, **extra})

        report(0)
        try:
            for i, job in enumerate(jobs, 1):
                done = i - 1
                if stop is not None and stop.exists():
                    log.warning("Stop requested; exporting the %d products collected so far", len(records))
                    status = "stopped"
                    break
                url = job["url"]
                try:
                    if job.get("file"):
                        html = Path(job["file"]).read_text(encoding="utf-8", errors="ignore")
                    else:
                        html = self.fetcher.get(url)
                    rec = self.process_html(html, url)
                except BlockedError as e:
                    log.error("Stopping: %s", e)
                    status = "blocked"
                    break
                except CacheMissError:
                    skipped += 1
                    report(i)
                    continue
                except FetchError as e:
                    log.warning("Skipped %s: %s", url, e)
                    rejected.append({"url": url, "reason": str(e)})
                    report(i)
                    continue
                except Exception as e:  # keep going on single-page failures
                    log.exception("Failed %s: %s", url, e)
                    rejected.append({"url": url, "reason": f"{type(e).__name__}: {e}"})
                    report(i)
                    continue
                key = rec.get(pk)
                if key is not None and key in seen:
                    report(i)
                    continue
                seen.add(key)
                q = rec["_meta"]["quality"]
                page_reviews = rec.pop("_reviews", [])
                log.info("[%d/%d] %s q=%.2f reviews=%d %s", i, n, key, q, len(page_reviews),
                         (rec.get("title") or "")[:60])
                if q < min_q:
                    rejected.append({"url": url, "reason": "low_quality", "quality": q,
                                     "errors": rec["_meta"]["errors"],
                                     "record": {k: v for k, v in rec.items() if k != "_meta"}})
                else:
                    records.append(rec)
                    reviews += page_reviews
                report(i, current=key, title=(rec.get("title") or "")[:80])
            else:
                done = n
        finally:
            if self._fetcher:
                stats = self._fetcher.stats
                self._fetcher.close()
                self._fetcher = None
                log.info("fetch stats: %s", stats)
        paths = export(self.schema, records, rejected, self.settings.get("output") or {}, reviews)
        res = {"status": status, "records": len(records), "rejected": len(rejected),
               "reviews": len(reviews), "skipped": skipped, "files": paths}
        if progress:
            progress({"state": status, "done": done, "total": n,
                      "ok": len(records), "rejected": len(rejected), "skipped": skipped, "result": res})
        return res

    # -------------------------------------------------------- run inputs
    def load_inputs(self) -> list[dict]:
        p = self.out_dir / INPUTS_FILE
        if not p.exists():
            return []
        return json.loads(p.read_text(encoding="utf-8")).get("jobs", [])

    def _save_inputs(self, jobs: list[dict]):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / INPUTS_FILE).write_text(json.dumps({
            "marketplace": self.marketplace,
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "jobs": jobs}, indent=1), encoding="utf-8")
