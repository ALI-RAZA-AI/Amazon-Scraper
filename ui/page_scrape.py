"""Scrape page: paste ASINs / URLs or upload a file, run the pipeline in the background."""
import time

import streamlit as st

from amzpipe.inputs import parse_asins
from amzpipe.transforms import MARKETPLACE_CURRENCY
from ui.common import (RUN_DIR, job_status, llm_env, load_products, log_tail, request_stop, settings,
                       start_job)

st.title("Scrape products")
st.caption("Paste Amazon ASINs or product links, or upload a .txt / .csv file. Pages are cached, so "
           "products you already scraped are never downloaded twice.")

status = job_status()
cfg = settings()
STATE_TEXT = {"running": "Running", "finished": "Finished", "stopped": "Stopped (partial data exported)",
              "blocked": "Amazon blocked the scraper (partial data exported)", "failed": "Failed"}
KIND_TEXT = {"run": "Scrape", "rebuild": "Rebuild from cache", "export": "Re-export"}


@st.fragment(run_every=2 if status["running"] else None)
def progress_panel():
    s = job_status()
    p = s["progress"]
    state = p.get("state") or ("running" if s["running"] else None)
    if state is None:
        return
    with st.container(border=True):
        st.subheader(f"{KIND_TEXT.get(s['kind'], 'Job')}: {STATE_TEXT.get(state, state)}")
        total, done = p.get("total") or 0, p.get("done") or 0
        if total:
            st.progress(min(done / total, 1.0), text=f"{done} of {total} pages")
        c = st.columns(4)
        c[0].metric("Accepted", p.get("ok", 0))
        c[1].metric("Rejected", p.get("rejected", 0))
        c[2].metric("Skipped (not cached)", p.get("skipped", 0))
        elapsed = time.time() - (s.get("started") or time.time())
        c[3].metric("Elapsed", f"{int(elapsed // 60)}m {int(elapsed % 60):02d}s")
        if p.get("title"):
            st.caption(f"Last product: {p.get('current')} · {p['title']}")
        if state == "failed":
            st.error(p.get("error", "The job failed; see the log below."))
        if state == "blocked":
            st.warning("Amazon is refusing requests. Wait a while, increase the delays or add proxies on the "
                       "Settings page, then run again with **Add to the existing dataset**: finished pages "
                       "come from the cache and the rest is retried.")
        if s["running"]:
            if st.button("Stop after the current product", icon=":material/stop:"):
                request_stop()
                st.toast("Stopping… the products collected so far will be exported.")
        elif state in ("finished", "stopped", "blocked"):
            if st.button("Browse the data", type="primary", icon=":material/table_view:"):
                st.switch_page("ui/page_data.py")
        with st.expander("Log", expanded=state in ("failed", "blocked")):
            st.code(log_tail(40) or "(empty)", language="log")
    if not s["running"] and status["running"]:
        st.rerun(scope="app")  # job just finished: refresh the page once and stop polling


progress_panel()
busy = status["running"]

left, right = st.columns([3, 2], gap="large")
with left:
    text = st.text_area("ASINs or product URLs", height=180, disabled=busy,
                        placeholder="B0CHWRXH8B\nhttps://www.amazon.com/dp/B09JQMJHXY\n"
                                    "https://www.amazon.com/Some-Product/dp/B0BSHF7WHW/ref=...")
    upload = st.file_uploader("…or upload a file", type=["txt", "csv"], disabled=busy,
                              help="Any .txt or .csv: every ASIN or Amazon product link in it is picked up, "
                                   "in any column.")
    raw = (text or "") + "\n" + (upload.getvalue().decode("utf-8-sig", errors="ignore") if upload else "")
    asins, bad = parse_asins(raw)
    if asins:
        st.success(f"{len(asins)} product(s) recognised", icon=":material/check_circle:")
    if bad:
        with st.expander(f"{len(bad)} entr{'y' if len(bad) == 1 else 'ies'} ignored (not an ASIN or product link)"):
            st.write(", ".join(bad[:200]))

with right:
    existing = len(load_products())
    mode = st.radio("Dataset", ["Add to the existing dataset", "Start a new dataset"], disabled=busy,
                    captions=[f"Keeps the {existing} products already scraped (re-read from cache, no downloads)",
                              "Only the products listed here end up in the CSVs"])
    markets = sorted(MARKETPLACE_CURRENCY)
    market = st.selectbox("Marketplace", markets, disabled=busy,
                          index=markets.index(cfg.get("marketplace", "amazon.com")))
    fetch = cfg.get("fetch") or {}
    backends = ["auto", "requests", "playwright"]
    backend = st.selectbox("Fetching", backends, index=backends.index(fetch.get("backend", "auto")), disabled=busy,
                           format_func={"auto": "Automatic (HTTP, browser fallback when blocked)",
                                        "requests": "HTTP only", "playwright": "Headless browser only"}.get)
    llm = (cfg.get("rag") or {}).get("llm") or {}
    st.caption(f"RAG extraction LLM: **{llm.get('provider') or 'none'}**"
               + (f" ({llm.get('model')})" if llm.get("provider") not in (None, "none") else "")
               + " · change it on the Settings page")

    if st.button("Start scraping", type="primary", icon=":material/play_arrow:", width="stretch",
                 disabled=busy or not asins):
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        inp = RUN_DIR / "inputs.txt"
        inp.write_text("\n".join(asins), encoding="utf-8")
        args = ["run", "-i", str(inp), "--marketplace", market, "--backend", backend]
        if mode.startswith("Add"):
            args.append("--append")
        start_job(args, llm_env())
        st.rerun()

st.divider()
st.subheader("Other actions")
a, b = st.columns(2, gap="large")
with a:
    st.markdown("**Rebuild from cache**: re-extract every product with the current data structure. "
                "Use after adding or changing fields on the *Data structure* page. No downloads.")
    if st.button("Rebuild dataset", icon=":material/refresh:", disabled=busy or not existing):
        start_job(["rebuild"], llm_env())
        st.rerun()
with b:
    st.markdown("**Sample data**: build a small offline demo dataset from the test pages in "
                "`tests/fixtures`, to try the app without contacting Amazon. Replaces the current dataset.")
    if st.button("Load sample data", icon=":material/science:", disabled=busy):
        start_job(["run", "--html-dir", "tests/fixtures", "--embedder", "tfidf"])
        st.rerun()

st.caption(f"Polite delays of {fetch.get('min_delay', 2)}–{fetch.get('max_delay', 5)} s between requests. "
           "Amazon's Conditions of Use restrict automated access: keep runs small and for research or "
           "personal datasets, and don't republish the scraped content.")
