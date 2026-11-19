"""Turn user input (ASINs, product URLs, pasted text, .txt / .csv files) into a clean ASIN list."""
from __future__ import annotations

import re
from pathlib import Path

# /dp/ASIN, /gp/product/ASIN, /gp/aw/d/ASIN (mobile), /product-reviews/ASIN, /o/ASIN/ ...
URL_ASIN = re.compile(r"/(?:dp|gp/product|gp/aw/d|product-reviews|exec/obidos/ASIN|o/ASIN)/([A-Z0-9]{10})(?=[/?#&]|$)",
                      re.I)
# Bare ASINs: "B0" + 8 chars for most products, ISBN-10 for books. A looser
# [A-Z0-9]{10} would also match ordinary words such as "HEADPHONES".
BARE_ASIN = re.compile(r"^(?:B0[A-Z0-9]{8}|\d{9}[\dX])$")
SPLIT = re.compile(r"[\s,;\t\"'|]+")


def parse_asins(text: str) -> tuple[list[str], list[str]]:
    """Return (asins, unrecognised) from free text: one item per line, or
    separated by commas / semicolons / tabs, including CSV file contents.
    Duplicates are removed, order is kept."""
    asins, bad = [], []
    for tok in SPLIT.split(text or ""):
        tok = tok.strip()
        if not tok:
            continue
        m = URL_ASIN.search(tok)
        if m:
            asins.append(m.group(1).upper())
        elif BARE_ASIN.match(tok.upper()):
            asins.append(tok.upper())
        elif "amzn." in tok.lower() or "a.co/" in tok.lower():
            bad.append(f"{tok} (short link: open it in a browser and paste the full /dp/ URL)")
        else:
            bad.append(tok)
    return list(dict.fromkeys(asins)), bad


def read_input_file(path: str | Path) -> tuple[list[str], list[str]]:
    return parse_asins(Path(path).read_text(encoding="utf-8-sig", errors="ignore"))
