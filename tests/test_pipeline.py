"""Run: python tests/make_fixtures.py && python -m pytest -q tests"""
import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from amzpipe import Pipeline
from amzpipe.discovery import has_next_page, parse_search_page
from amzpipe.inputs import parse_asins
from amzpipe.rag.enricher import grounded
from amzpipe.schema import parse_schema

HERE = Path(__file__).parent
# Pinned copies of the default schema / taxonomy: editing config/ in the app must not break the tests.
SCHEMA = HERE / "schema_default.yaml"


def _overrides(tmp: Path) -> dict:
    return {"output.dir": str(tmp / "out"), "fetch.cache_dir": str(tmp / "cache"), "rag.embedder": "tfidf",
            "rag.llm.provider": "none"}


def _schema_raw() -> dict:
    return yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("run")
    res = Pipeline(SCHEMA, overrides=_overrides(tmp)).run(html_dir=HERE / "fixtures")
    out = tmp / "out"
    recs = {json.loads(l)["asin"]: json.loads(l) for l in open(out / "products.jsonl", encoding="utf-8")}
    return res, recs, out, tmp


# ------------------------------------------------------------------ inputs
def test_parse_asins_from_urls_csv_and_text():
    text = """asin,url,name
B0CHWRXH8B,https://www.amazon.com/Some-Product-Name/dp/B09JQMJHXY/ref=sr_1_3?keywords=x,Earbuds
https://www.amazon.co.uk/gp/product/b0abcdefgh?th=1
https://www.amazon.com/product-reviews/0596520689/
B0CHWRXH8B; https://amzn.to/3xyz HEADPHONES"""
    asins, bad = parse_asins(text)
    assert asins == ["B0CHWRXH8B", "B09JQMJHXY", "B0ABCDEFGH", "0596520689"]
    assert "HEADPHONES" in bad and any("short link" in b for b in bad)


def test_search_parsing():
    html = (HERE / "search_page.html").read_text()
    assert parse_search_page(html) == ["B0TEST0001", "B0TEST0002"]  # sponsored + dupes removed
    assert not has_next_page(html)


def test_grounding_numbers_use_boundaries():
    assert grounded(89.99, "Deal price: $89.99")
    assert not grounded(9.99, "Deal price: $89.99")
    assert grounded("Ocean Blue", "Color: Ocean Blue.")


# ------------------------------------------------------------- extraction
def test_counts_and_csv_files(result):
    res, recs, out, _ = result
    assert res["records"] == 4 and res["rejected"] == 1 and res["status"] == "finished"
    for f in ("products.csv", "train.csv", "validation.csv", "test.csv", "reviews.csv",
              "rejected.csv", "DATASET_CARD.md", "products.jsonl"):
        assert (out / f).exists(), f
    df = pd.read_csv(out / "products.csv", encoding="utf-8-sig")
    assert len(df) == 4 and {"asin", "title", "price", "text", "label", "split"} <= set(df.columns)
    assert str(df["review_count"].dtype) == "int64"  # whole numbers stay whole in the CSV
    assert (out / "products.csv").read_bytes()[:3] == b"\xef\xbb\xbf"  # BOM: Excel-safe UTF-8


def test_css_extraction(result):
    r = result[1]["B0TEST0001"]
    assert r["price"] == 49.99 and r["list_price"] == 79.99 and r["review_count"] == 12483
    assert r["brand"] == "SoundCore" and r["rating"] == 4.4 and len(r["images"]) == 2


def test_spec_and_derived_fields(result):
    r1, r2 = result[1]["B0TEST0001"], result[1]["B0TEST0002"]
    assert r1["best_sellers_rank"] == 1234 and r1["date_first_available"] == "2024-03-03"
    assert r2["best_sellers_rank"] == 5678 and r2["date_first_available"] == "2023-01-12"
    assert r1["discount_pct"] == 37.5 and r2["discount_pct"] == 0.0
    assert r1["specifications"]["Connectivity"] == "Bluetooth 5.3"


def test_rag_corrects_wrong_price(result):
    r = result[1]["B0TEST0002"]
    assert r["price"] == 89.99            # carousel $9.99 rejected, grounded value used
    assert r["material"] == "ceramic-coated steel"
    assert any("replaced by RAG" in f for f in r["_meta"]["flags"])


def test_jsonld_and_rag_fill(result):
    r = result[1]["B0TEST0003"]
    assert (r["brand"], r["price"], r["currency"]) == ("FlexFit", 24.95, "USD")
    assert (r["color"], r["material"], r["item_weight"]) == ("Ocean Blue", "TPE", "2.2 pounds")


def test_current_layout_price_and_review_signals(result):
    r = result[1]["B0TEST0005"]
    assert r["price"] == 228.36  # visible "$ 228 . 36", not the device card's "$4499"
    assert [r[f"rating_{i}_pct"] for i in (5, 4, 3, 2, 1)] == [87, 8, 2, 0, 3]
    assert r["review_summary"].startswith("Customers find these earbuds sound great")
    assert r["aspects_positive"] == ["sound quality", "value for money"] and r["aspects_negative"] == ["fit"]
    assert r["review_count"] == 28863 and r["brand"] == "Acme"


def test_unverified_price_is_dropped_and_delivery_flagged(tmp_path):
    """Live amazon.com from outside the US: no buy box, only a comparison card's price."""
    html = """<html><body><span id="productTitle">Echo Smart Speaker with Alexa</span>
    <div id="availability"><span>This item cannot be shipped to your selected delivery location.</span></div>
    <div id="amazon-device-desktop-dp_feature_div"><span class="a-price"><span class="a-offscreen">$4499</span></span></div>
    </body></html>"""
    rec = Pipeline(SCHEMA, overrides=_overrides(tmp_path)).process_html(html, "https://www.amazon.com/dp/B0TEST0006")
    assert rec["price"] is None and rec["discount_pct"] is None
    flags = " ".join(rec["_meta"]["flags"])
    assert "4499.0 not found in main product text; dropped" in flags and "delivery:" in flags


def test_delivery_location_flow(tmp_path):
    """Token from the page -> CSRF token -> POST zip code; then the page is fetched again."""
    from amzpipe import fetcher as F
    f = F.Fetcher({"backend": "requests", "cache_dir": str(tmp_path), "min_delay": 0, "max_delay": 0})
    calls = []

    class Resp:
        def __init__(self, text, status=200):
            self.text, self.status_code, self.ok = text, status, status < 400

        def raise_for_status(self):
            pass

    blocked = f'<input name="glow-validation-token" type="hidden" value="TOK1">{F.NOT_DELIVERABLE}'

    def get(url, **kw):
        calls.append(("GET", url.split("amazon.com")[-1][:40], kw.get("headers", {}).get("anti-csrftoken-a2z")))
        if "get-rendered-address-selections" in url:
            return Resp('CSRF_TOKEN : "TOK2",')
        if url.endswith("/dp/B0TEST0007"):
            return Resp("<html>US page</html>" if f.location_set else blocked)
        return Resp("<html>home</html>")

    def post(url, **kw):
        calls.append(("POST", kw["json"]["zipCode"], kw["headers"]["anti-csrftoken-a2z"]))
        return Resp('{"isValidAddress":1}')
    f.session.get, f.session.post = get, post
    assert f.get("https://www.amazon.com/dp/B0TEST0007") == "<html>US page</html>"
    assert f.location_set and ("POST", "10001", "TOK2") in calls
    assert ("GET", "/portal-migration/hz/glow/get-rendered-a", "TOK1") in calls


def test_taxonomy_labels(result):
    recs = result[1]
    assert recs["B0TEST0001"]["label"] == "Electronics > Audio > Headphones & Earbuds"
    assert recs["B0TEST0002"]["label"] == "Home & Kitchen > Kitchen Appliances"
    assert recs["B0TEST0003"]["label"] == "Sports & Outdoors"


def test_text_has_no_targets(result):
    r = result[1]["B0TEST0001"]
    assert "SoundCore Pro" in r["text"] and "49.99" not in r["text"]


# ----------------------------------------------------------------- reviews
def test_reviews(result):
    _, recs, out, _ = result
    rv = pd.read_csv(out / "reviews.csv", encoding="utf-8-sig")
    assert sorted(rv["review_id"]) == ["R1TESTREVIEW01", "R2TESTREVIEW02"]  # dupe + broken skipped
    a = rv.set_index("review_id").loc["R1TESTREVIEW01"]
    b = rv.set_index("review_id").loc["R2TESTREVIEW02"]
    assert (a["asin"], a["rating"], a["title"], a["review_date"]) == ("B0TEST0001", 5.0,
                                                                      "Best earbuds I have owned", "2024-03-03")
    assert a["verified_purchase"] and a["helpful_votes"] == 1 and a["variant"] == "Color: Midnight Black"
    assert "Read more" not in a["body"] and a["review_country"] == "United States"
    assert (b["rating"], b["title"], b["review_date"], b["helpful_votes"]) == (3.0, "Decent but bulky",
                                                                               "2024-01-05", 12)
    assert not b["verified_purchase"] and b["review_country"] == "United Kingdom"
    assert (rv["split"] == recs["B0TEST0001"]["split"]).all()  # same split as their product
    assert recs["B0TEST0001"]["price"] == 49.99  # "$19.99" in a review never reaches the product


# ------------------------------------------------------------------ export
def test_re_export_uses_export_section(result, tmp_path):
    _, _, out, tmp = result
    raw = _schema_raw()
    raw["export"].update(columns=["asin", "title", "price", "category_normalized"],
                         extra_columns=["label", "split"], rename={"price": "price_usd"})
    raw["training"]["text_template"] = "{title} by {brand}"
    path = tmp_path / "schema.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    Pipeline(schema_path=path, overrides=_overrides(tmp)).re_export()
    df = pd.read_csv(out / "products.csv", encoding="utf-8-sig")
    assert list(df.columns) == ["asin", "title", "price_usd", "category_normalized", "label", "split"]
    assert "split" not in pd.read_csv(out / "train.csv", encoding="utf-8-sig").columns
    store = [json.loads(l) for l in open(out / "products.jsonl", encoding="utf-8")]
    assert any(r["text"] == "SoundCore Pro Wireless Earbuds, Bluetooth 5.3, Active Noise Cancelling, "
                            "40H Playtime by SoundCore" for r in store)


def test_rebuild_is_offline(result):
    _, _, out, tmp = result
    inputs = json.loads((out / "run_inputs.json").read_text())
    inputs["jobs"].append({"url": "https://www.amazon.com/dp/B0NOTCACHE"})  # never downloaded
    (out / "run_inputs.json").write_text(json.dumps(inputs))
    res = Pipeline(SCHEMA, overrides=_overrides(tmp)).rebuild()
    assert res["records"] == 4 and res["skipped"] == 1  # skipped, not fetched


def test_stop_file_stops_cleanly_and_still_exports(tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("stop")
    res = Pipeline(SCHEMA, overrides=_overrides(tmp_path)).run(html_dir=HERE / "fixtures", stop_file=stop)
    assert res["status"] == "stopped" and res["records"] == 0
    assert (tmp_path / "out" / "products.csv").exists()


# ------------------------------------------------------------------ schema
def test_schema_validation_catches_mistakes():
    raw = _schema_raw()
    raw["fields"][1]["transforms"] = ["clean_txt"]
    raw["fields"][2]["sources"] = [{"csss": "#x"}]
    raw["training"]["label_field"] = "nope"
    raw["training"]["splits"] = {"train": 0.9, "test": 0.3}
    raw["export"]["columns"] = ["asin", "ghost"]
    schema, errors, _ = parse_schema(raw)
    assert schema is None
    msg = "\n".join(errors)
    for needle in ("clean_txt", "csss", "label_field 'nope'", "add up to 1.0", "'ghost'"):
        assert needle in msg, needle


def test_schema_warns_about_target_leakage():
    raw = _schema_raw()
    raw["training"]["text_template"] = "{title} costs {price}"
    schema, errors, warnings = parse_schema(raw)
    assert schema is not None and not errors
    assert any("leakage" in w for w in warnings)


# ------------------------------------------------------------------- fetch
def test_auto_backend_switches_to_browser_when_blocked(tmp_path, monkeypatch):
    from amzpipe import fetcher as F
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    f = F.Fetcher({"backend": "auto", "cache_dir": str(tmp_path), "min_delay": 0, "max_delay": 0})
    calls = []
    monkeypatch.setattr(f, "_get_requests", lambda url: calls.append("req") or "validateCaptcha")
    monkeypatch.setattr(f, "_get_browser", lambda url: calls.append("browser") or "<html>ok</html>")
    monkeypatch.setattr(f, "_browser_available", lambda: True)
    assert f.get("https://www.amazon.com/dp/B0TEST0009") == "<html>ok</html>"
    assert calls == ["req", "req", "browser"] and f.use_browser
    assert f.get("https://www.amazon.com/dp/B0TEST0009") == "<html>ok</html>"  # now cached
    assert calls == ["req", "req", "browser"]


def test_404_is_not_retried(tmp_path, monkeypatch):
    from amzpipe import fetcher as F
    f = F.Fetcher({"backend": "requests", "cache_dir": str(tmp_path), "min_delay": 0, "max_delay": 0})
    calls = []

    def not_found(url):
        calls.append(url)
        raise F.NotFoundError("404")
    monkeypatch.setattr(f, "_get_requests", not_found)
    with pytest.raises(F.NotFoundError):
        f.get("https://www.amazon.com/dp/B0GONE0000")
    assert len(calls) == 1


# --------------------------------------------------------------------- LLM
def test_api_key_pasted_as_env_var_name_is_refused_without_echoing_it():
    from amzpipe.rag.llm import LLMClient
    secret = "sk-test-0000-not-a-real-key"
    with pytest.raises(RuntimeError) as e:
        LLMClient({"provider": "anthropic", "api_key_env": secret})
    assert "NAME of an environment variable" in str(e.value) and secret not in str(e.value)



def test_llm_answers_must_be_grounded(tmp_path, monkeypatch):
    """A fake LLM returns one true and one hallucinated value; only the true one is kept."""
    from amzpipe.rag import llm as llm_mod

    def fake(self, system, user):
        if "Candidate categories" in user:  # taxonomy tie-break: answer outside the candidates
            return {"category": "Made Up > Category"}
        assert "Context chunks" in user
        return {"material": {"value": "TPE", "evidence": "bullets-0"},
                "dimensions": {"value": "80 x 30 inches", "evidence": "bullets-0"},  # not on page
                "color": {"value": "Ocean Blue", "evidence": "structured_data-0"},
                "item_weight": {"value": "2.2 pounds", "evidence": "structured_data-0"}}
    monkeypatch.setattr(llm_mod.LLMClient, "complete_json", fake)
    monkeypatch.setenv("FAKE_KEY", "x")
    p = Pipeline(SCHEMA, overrides={**_overrides(tmp_path), "rag.llm.provider": "openai",
                            "rag.llm.api_key_env": "FAKE_KEY"})
    html = (HERE / "fixtures" / "B0TEST0003.html").read_text()
    rec = p.process_html(html, "https://www.amazon.com/dp/B0TEST0003")
    assert rec["material"] == "TPE" and rec["_meta"]["provenance"]["material"] == "llm"
    assert rec["dimensions"] is None
    assert any("dimensions" in f and "rejected" in f for f in rec["_meta"]["flags"])
    assert rec["category_normalized"] != "Made Up > Category"  # LLM can only pick retrieved candidates
