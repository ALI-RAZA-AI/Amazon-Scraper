"""Command line entry point.

Examples
  python -m amzpipe run --asins B0CHWRXH8B https://www.amazon.com/dp/B09JQMJHXY
  python -m amzpipe run -i products.csv             # .txt / .csv with ASINs or product URLs
  python -m amzpipe run -i more.txt --append        # add to the existing dataset
  python -m amzpipe run --html-dir saved_pages/     # offline / already-downloaded pages
  python -m amzpipe run -i asins.txt --llm openai --llm-model gpt-4o-mini
  python -m amzpipe rebuild                         # after editing fields: re-extract from cache
  python -m amzpipe export                          # after editing training/export: rewrite CSVs
  python -m amzpipe schema --check                  # list fields and validate schema.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .inputs import read_input_file
from .pipeline import ROOT, Pipeline


def _progress_writer(path):
    if not path:
        return None
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    def write(info: dict):
        try:  # write-then-rename so a reader never sees half a file
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(info), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass
    return write


def main(argv=None):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--schema", help="path to schema.yaml")
    common.add_argument("--settings", help="path to settings.yaml")
    common.add_argument("--out", help="output directory")
    common.add_argument("-v", "--verbose", action="store_true")
    common.add_argument("--log-file", help="also write the log to this file")
    common.add_argument("--progress-file", help="write JSON progress here (used by the frontend)")
    common.add_argument("--stop-file", help="stop cleanly (and export) when this file appears")
    common.add_argument("--llm", choices=["none", "openai", "anthropic", "ollama"])
    common.add_argument("--llm-model")
    common.add_argument("--embedder", choices=["auto", "sentence-transformers", "tfidf"])

    ap = argparse.ArgumentParser(prog="amzpipe", description="Amazon product -> training dataset pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", parents=[common], help="scrape products and build the dataset")
    r.add_argument("--asins", nargs="*", default=[], help="ASINs and/or product URLs")
    r.add_argument("-i", "--input", "--asin-file", dest="input_file",
                   help=".txt or .csv file containing ASINs and/or product URLs")
    r.add_argument("-q", "--query", action="append", default=[], help="search query (repeatable)")
    r.add_argument("--html-dir", help="folder of saved product .html pages (offline mode)")
    r.add_argument("-n", "--max-products", type=int, default=50, help="max products per search query")
    r.add_argument("--append", action="store_true",
                   help="keep the products of the previous run (re-read from cache) and add these")
    r.add_argument("--marketplace", help="e.g. amazon.co.uk")
    r.add_argument("--backend", choices=["auto", "requests", "playwright"], help="how pages are fetched")
    r.add_argument("--playwright", action="store_true", help="same as --backend playwright")

    sub.add_parser("rebuild", parents=[common],
                   help="re-extract the current dataset from cached pages (no network); use after changing fields")
    sub.add_parser("export", parents=[common],
                   help="rewrite the CSVs from products.jsonl; use after changing training/export settings")

    s = sub.add_parser("schema", help="print the active schema fields")
    s.add_argument("--schema")
    s.add_argument("--check", action="store_true", help="validate and show warnings")

    a = ap.parse_args(argv)
    handlers = [logging.StreamHandler()]
    if getattr(a, "log_file", None):
        Path(a.log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(a.log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if getattr(a, "verbose", False) else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S", handlers=handlers)

    if a.cmd == "schema":
        import yaml
        from .schema import parse_schema
        path = Path(a.schema or ROOT / "config/schema.yaml")
        sc, errors, warnings = parse_schema(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        for e in errors:
            print("ERROR  ", e)
        for w in warnings:
            print("WARNING", w)
        if sc is None:
            sys.exit(1)
        for f in sc.fields:
            rag = f" rag={f.rag.get('mode', 'fill')}" if f.rag else ""
            print(f"{f.name:22} {f.type:7} {'required' if f.required else '':8}{rag}  {f.description[:60]}")
        if sc.reviews_enabled:
            print("\nreviews:", ", ".join(f.name for f in sc.review_fields))
        return

    ov = {}
    if a.out: ov["output.dir"] = a.out
    if a.llm: ov["rag.llm.provider"] = a.llm
    if a.llm_model: ov["rag.llm.model"] = a.llm_model
    if a.embedder: ov["rag.embedder"] = a.embedder
    if a.cmd == "run":
        if a.marketplace: ov["marketplace"] = a.marketplace
        if a.backend: ov["fetch.backend"] = a.backend
        if a.playwright: ov["fetch.backend"] = "playwright"

    progress = _progress_writer(a.progress_file)
    try:
        p = Pipeline(a.schema, a.settings, ov)
        if a.cmd == "run":
            asins = list(a.asins)
            if a.input_file:
                found, bad = read_input_file(a.input_file)
                asins += found
                if bad:
                    logging.warning("Ignored %d entries in %s that are not ASINs/product URLs (e.g. %s)",
                                    len(bad), a.input_file, ", ".join(bad[:5]))
            if not (a.query or asins or a.html_dir or a.append):
                ap.error("give at least one of --asins, --input, --query, --html-dir (or --append)")
            res = p.run(a.query, asins, a.html_dir, a.max_products, a.append, progress, a.stop_file)
        elif a.cmd == "rebuild":
            res = p.rebuild(progress, a.stop_file)
        else:
            res = {"status": "finished", "files": p.re_export()}
            if progress:
                progress({"state": "finished", "result": res})
    except Exception as e:
        if progress:
            progress({"state": "failed", "error": f"{type(e).__name__}: {e}"})
        raise
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
