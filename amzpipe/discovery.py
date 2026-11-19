"""Turn search queries / category URLs into a list of product URLs (by ASIN)."""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

ASIN_RE = re.compile(r"/(?:dp|gp/product)/([A-Z0-9]{10})")


def search_url(marketplace: str, query: str, page: int = 1) -> str:
    return f"https://www.{marketplace}/s?k={quote_plus(query)}&page={page}"


def product_url(marketplace: str, asin: str) -> str:
    return f"https://www.{marketplace}/dp/{asin}"


def parse_search_page(html: str, skip_sponsored: bool = True) -> list[str]:
    """Return ASINs from a search-results (or category/listing) page, in order, de-duplicated."""
    soup = BeautifulSoup(html, "lxml")
    asins: list[str] = []
    for div in soup.select("div[data-component-type='s-search-result'][data-asin]"):
        asin = div.get("data-asin", "").strip()
        if not asin:
            continue
        if skip_sponsored and (div.select_one(".puis-sponsored-label-text")
                               or "AdHolder" in " ".join(div.get("class", []))):
            continue
        asins.append(asin)
    if not asins:  # generic fallback: any product link on the page
        for a in soup.select("a[href]"):
            m = ASIN_RE.search(a["href"])
            if m:
                asins.append(m.group(1))
    seen, out = set(), []
    for a in asins:
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    nxt = soup.select_one("a.s-pagination-next")
    return nxt is not None and "s-pagination-disabled" not in " ".join(nxt.get("class", []))
