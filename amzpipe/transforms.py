"""Named transforms referenced from schema.yaml (`transforms: [...]`), and the
derived-field functions referenced by `{derived: name}` sources.

Add your own transform: write `def my_fn(value, ctx) -> value` and register it
in TRANSFORMS. Transforms are applied element-wise to list values.

Add your own derived field: write `def my_fn(record) -> value` and register it
in DERIVED. Derived fields are computed after extraction and RAG, so they can
use any other field.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime

CURRENCY_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR", "¥": "JPY",
                    "C$": "CAD", "CA$": "CAD", "A$": "AUD", "AU$": "AUD", "R$": "BRL",
                    "S$": "SGD", "zł": "PLN", "kr": "SEK", "₺": "TRY", "AED": "AED",
                    "SAR": "SAR", "EGP": "EGP", "PKR": "PKR"}
CURRENCY_CODES = {"USD", "GBP", "EUR", "INR", "JPY", "CAD", "AUD", "AED", "SAR", "MXN", "BRL",
                  "SGD", "SEK", "PLN", "TRY", "EGP", "PKR"}
MARKETPLACE_CURRENCY = {"amazon.com": "USD", "amazon.co.uk": "GBP", "amazon.de": "EUR",
                        "amazon.fr": "EUR", "amazon.it": "EUR", "amazon.es": "EUR",
                        "amazon.nl": "EUR", "amazon.com.be": "EUR", "amazon.in": "INR",
                        "amazon.ca": "CAD", "amazon.com.au": "AUD", "amazon.co.jp": "JPY",
                        "amazon.ae": "AED", "amazon.sa": "SAR", "amazon.com.mx": "MXN",
                        "amazon.com.br": "BRL", "amazon.sg": "SGD", "amazon.se": "SEK",
                        "amazon.pl": "PLN", "amazon.com.tr": "TRY", "amazon.eg": "EGP"}
DOLLAR_CURRENCIES = {"USD", "CAD", "AUD", "MXN", "SGD"}


def clean_text(v, ctx=None):
    if not isinstance(v, str):
        return v
    v = unicodedata.normalize("NFKC", v)
    v = v.replace("‎", "").replace("‏", "")
    v = re.sub(r"\s+", " ", v).strip()
    return v or None


def strip_brand_prefix(v, ctx=None):
    if not isinstance(v, str):
        return v
    v = re.sub(r"^(Visit the|Brand:)\s*", "", v, flags=re.I)
    v = re.sub(r"\s*Store$", "", v, flags=re.I)
    return v.strip() or None


def parse_price(v, ctx=None):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\xa0", " ")
    m = re.search(r"\d[\d.,\s]*", s)
    if not m:
        return None
    num = m.group(0).strip().replace(" ", "")
    # European format 1.234,56 -> 1234.56
    if re.search(r",\d{2}$", num) and num.count(",") == 1:
        num = num.replace(".", "").replace(",", ".")
    else:
        num = num.replace(",", "")
    try:
        return float(num)
    except ValueError:
        return None


def parse_float(v, ctx=None):
    if v is None or isinstance(v, (int, float)):
        return v
    m = re.search(r"\d+(?:[.,]\d+)?", str(v))
    return float(m.group(0).replace(",", ".")) if m else None


def parse_int(v, ctx=None):
    if v is None or isinstance(v, int):
        return v
    m = re.search(r"\d[\d,.]*", str(v))
    return int(re.sub(r"[,.]", "", m.group(0))) if m else None


def currency_code(v, ctx=None):
    if not v:
        return None
    s = str(v).strip()
    if s.upper() in CURRENCY_CODES:
        return s.upper()
    m = re.search(r"\b([A-Z]{3})\b", s)
    if m and m.group(1) in CURRENCY_CODES:
        return m.group(1)
    for sym in sorted(CURRENCY_SYMBOLS, key=len, reverse=True):
        if sym in s:
            if sym == "$":  # "$" is also CAD / AUD / MXN / SGD on those marketplaces
                market = MARKETPLACE_CURRENCY.get((ctx or {}).get("marketplace", ""))
                if market in DOLLAR_CURRENCIES:
                    return market
            return CURRENCY_SYMBOLS[sym]
    return None


def lower(v, ctx=None):
    return v.lower() if isinstance(v, str) else v


def parse_date(v, ctx=None):
    """'Reviewed in the United States on March 3, 2024' / '3 Mar. 2024' -> '2024-03-03'."""
    if not isinstance(v, str):
        return v
    for pat, fmt in ((r"([A-Z][a-z]+\.? \d{1,2}, \d{4})", "%B %d, %Y"),
                     (r"(\d{1,2} [A-Z][a-z]+\.? \d{4})", "%d %B %Y")):
        m = re.search(pat, v)
        if m:
            s = m.group(1).replace(".", "")
            for f in (fmt, fmt.replace("%B", "%b")):
                try:
                    return datetime.strptime(s, f).date().isoformat()
                except ValueError:
                    pass
    return None


def strip_star_text(v, ctx=None):
    """Remove '5.0 out of 5 stars' that Amazon puts inside review titles."""
    if not isinstance(v, str):
        return v
    return re.sub(r"\d(?:[.,]\d)?\s+out of\s+5\s+stars", "", v).strip() or None


def clean_review_text(v, ctx=None):
    if not isinstance(v, str):
        return v
    v = re.sub(r"The media could not be loaded\.?|\bRead more\b", "", v)
    return re.sub(r"\s+", " ", v).strip() or None


def parse_helpful(v, ctx=None):
    """'One person found this helpful' -> 1, '1,024 people found this helpful' -> 1024."""
    if isinstance(v, str) and re.match(r"\s*one\b", v, re.I):
        return 1
    return parse_int(v)


TRANSFORMS = {
    "clean_text": clean_text,
    "strip_brand_prefix": strip_brand_prefix,
    "parse_price": parse_price,
    "parse_float": parse_float,
    "parse_int": parse_int,
    "currency_code": currency_code,
    "lower": lower,
    "parse_date": parse_date,
    "strip_star_text": strip_star_text,
    "clean_review_text": clean_review_text,
    "parse_helpful": parse_helpful,
}


def apply(names, value, ctx=None):
    for n in names or []:
        fn = TRANSFORMS.get(n)
        if fn is None:
            raise ValueError(f"Unknown transform '{n}'. Available: {sorted(TRANSFORMS)}")
        if isinstance(value, list):
            value = [x for x in (fn(i, ctx) for i in value) if x not in (None, "")]
        elif isinstance(value, dict):
            value = {k: fn(x, ctx) if isinstance(x, str) else x for k, x in value.items()}
        else:
            value = fn(value, ctx)
    return value


# ------------------------------------------------------------ derived fields
def discount_pct(rec):
    """Percent off the list price; 0 when no higher list price is shown."""
    price, lp = rec.get("price"), rec.get("list_price")
    if price is None:
        return None
    if not lp or lp <= price:
        return 0.0
    return round((lp - price) / lp * 100, 1)


DERIVED = {
    "discount_pct": discount_pct,
    "image_count": lambda r: len(r.get("images") or []),
    "bullet_count": lambda r: len(r.get("bullet_points") or []),
    "description_length": lambda r: len(r.get("description") or ""),
    "title_length": lambda r: len(r.get("title") or ""),
}
