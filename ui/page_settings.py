"""Settings page: config/settings.yaml (fetching, RAG, LLM, output) without editing YAML by hand."""
import importlib.util
import os
import re

import streamlit as st
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from amzpipe.rag.llm import DEFAULT_KEY_ENV
from amzpipe.transforms import MARKETPLACE_CURRENCY
from ui.common import SETTINGS_PATH, dump_rt, load_rt, save_text

st.title("Settings")
st.caption("Saved to `config/settings.yaml` (a backup of the previous version goes to `config/backups/`).")

s = load_rt(SETTINGS_PATH)
fetch = s.setdefault("fetch", CommentedMap())
rag = s.setdefault("rag", CommentedMap())
llm = rag.setdefault("llm", CommentedMap())
out = s.setdefault("output", CommentedMap())

with st.form("settings"):
    st.subheader("Fetching")
    c = st.columns(3)
    markets = sorted(MARKETPLACE_CURRENCY)
    marketplace = c[0].selectbox("Default marketplace", markets, index=markets.index(s.get("marketplace", "amazon.com")))
    backends = ["auto", "requests", "playwright"]
    backend = c[1].selectbox("Fetching", backends, index=backends.index(fetch.get("backend", "auto")),
                             help="auto = fast HTTP requests, switching to a headless browser when Amazon blocks")
    fallback_after = c[2].number_input("Blocked attempts before browser fallback", 1, 10,
                                       int(fetch.get("fallback_after", 2)))
    c = st.columns(4)
    min_delay = c[0].number_input("Min delay (s)", 0.0, 120.0, float(fetch.get("min_delay", 2.0)), 0.5)
    max_delay = c[1].number_input("Max delay (s)", 0.0, 120.0, float(fetch.get("max_delay", 5.0)), 0.5)
    retries = c[2].number_input("Retries per page", 1, 20, int(fetch.get("max_retries", 4)))
    timeout = c[3].number_input("Timeout (s)", 5, 300, int(fetch.get("timeout", 25)))
    zip_code = st.text_input(
        "Delivery ZIP / postcode", str(fetch.get("delivery_zip", "auto") or ""),
        help="auto = a location in the marketplace's country (10001 for amazon.com). Outside that country "
             "Amazon otherwise says 'cannot be shipped to your location' and hides prices. Empty = don't change.")
    proxies = st.text_area("Proxies (one per line, e.g. http://user:pass@host:port)",
                           "\n".join(fetch.get("proxies") or []), height=90,
                           help="Rotated per request. Use proxies in the marketplace's country to get local "
                                "prices and fewer blocks.")

    st.subheader("RAG")
    c = st.columns(3)
    embedders = ["auto", "tfidf", "sentence-transformers"]
    embedder = c[0].selectbox("Retrieval embeddings", embedders, index=embedders.index(rag.get("embedder", "auto")),
                              help="auto = sentence-transformers if installed, else TF-IDF")
    top_k = c[1].number_input("Chunks retrieved per field (top-k)", 1, 20, int(rag.get("top_k", 4)))
    verify = c[2].toggle("Verify scraped values against the page text", bool(rag.get("verify_existing", True)))

    st.subheader("LLM for RAG extraction (optional)")
    st.caption("Fills fields the page doesn't state in a fixed place (material, weight, ...). Every answer must "
               "be found in the retrieved page text, or it is discarded.")
    c = st.columns(3)
    providers = ["none", "openai", "anthropic", "ollama"]
    provider = c[0].selectbox("Provider", providers, index=providers.index(llm.get("provider") or "none"))
    model = c[1].text_input("Model", llm.get("model") or "",
                            help="e.g. gpt-4o-mini · claude-haiku-5-5 · llama3.1 (Ollama)")
    base_url = c[2].text_input("Base URL (OpenAI-compatible servers only)", llm.get("base_url") or "")
    c = st.columns(2)
    key_env = c[0].text_input("Environment variable with the API key (its name, not the key)",
                              llm.get("api_key_env") or "",
                              placeholder=DEFAULT_KEY_ENV.get(provider, "not needed"))
    api_key = c[1].text_input("…or paste a key for this browser session only", type="password",
                              value=st.session_state.get("llm_api_key", ""),
                              help="Passed to scrape jobs started from this app. Never written to disk.")

    st.subheader("Output")
    c = st.columns(2)
    min_quality = c[0].slider("Minimum quality score (lower products go to rejected.csv)", 0.0, 1.0,
                              float(out.get("min_quality", 0.5)), 0.05)
    fmts = c[1].multiselect("Extra formats besides CSV", ["jsonl", "parquet"],
                            default=[f for f in out.get("formats") or [] if f in ("jsonl", "parquet")])
    saved = st.form_submit_button("Save settings", type="primary", icon=":material/save:")

if saved:
    if max_delay < min_delay:
        st.error("Max delay must be at least the min delay.")
    elif key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        st.error("“Environment variable with the API key” takes the variable's *name*, such as "
                 "`ANTHROPIC_API_KEY`, not the key itself. Paste the key into the box on the right instead "
                 "(kept for this browser session only, never written to disk). Nothing was saved.")
    else:
        s["marketplace"] = marketplace
        fetch.update(backend=backend, fallback_after=int(fallback_after), min_delay=float(min_delay),
                     max_delay=float(max_delay), max_retries=int(retries), timeout=int(timeout),
                     delivery_zip=zip_code.strip())
        fetch["proxies"] = CommentedSeq([p.strip() for p in proxies.splitlines() if p.strip()])
        rag.update(embedder=embedder, top_k=int(top_k), verify_existing=bool(verify))
        llm.update(provider=provider, model=model or None, base_url=base_url or None, api_key_env=key_env or None)
        out["min_quality"] = round(float(min_quality), 2)
        out["formats"] = CommentedSeq(["csv", *fmts])
        out["formats"].fa.set_flow_style()
        save_text(SETTINGS_PATH, dump_rt(s))
        st.session_state["llm_api_key"] = api_key
        st.session_state["llm_api_key_env"] = key_env or DEFAULT_KEY_ENV.get(provider)
        st.toast("Settings saved", icon=":material/check_circle:")
        st.rerun()

st.divider()
st.subheader("Environment")
env_key = llm.get("api_key_env") or DEFAULT_KEY_ENV.get(llm.get("provider") or "")
checks = {
    "Headless browser (Playwright)": importlib.util.find_spec("playwright") is not None,
    "Dense embeddings (sentence-transformers)": importlib.util.find_spec("sentence_transformers") is not None,
}
if env_key:
    checks[f"API key ({env_key})"] = bool(os.environ.get(env_key) or st.session_state.get("llm_api_key"))
for label, ok in checks.items():
    st.markdown(f"{':material/check_circle:' if ok else ':material/cancel:'} {label}: "
                f"{'available' if ok else 'not available'}")
if not checks["Dense embeddings (sentence-transformers)"]:
    st.caption("Retrieval uses TF-IDF, which works well for product pages. For semantic retrieval install "
               "it with `.venv\\Scripts\\pip install sentence-transformers` (downloads PyTorch, ~1 GB).")
