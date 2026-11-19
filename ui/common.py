"""Shared helpers for the Streamlit frontend: config files, data loading, background jobs."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from ruamel.yaml import YAML

from amzpipe.export import PRODUCTS_STORE, REVIEWS_STORE, read_jsonl
from amzpipe.paths import CONFIG, ROOT, resolve
from amzpipe.schema import parse_schema

SCHEMA_PATH = CONFIG / "schema.yaml"
SETTINGS_PATH = CONFIG / "settings.yaml"
TAXONOMY_PATH = CONFIG / "taxonomy.yaml"
BACKUPS = CONFIG / "backups"
RUN_DIR = ROOT / "data" / "run"
PROGRESS = RUN_DIR / "progress.json"
LOG = RUN_DIR / "run.log"
STOP = RUN_DIR / "STOP"
JOB = RUN_DIR / "job.json"


# ------------------------------------------------------------------ YAML
def rt_yaml() -> YAML:
    """Round-trip YAML: keeps the comments and layout of the config files when saving."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    y.representer.add_representer(type(None), lambda r, _: r.represent_scalar("tag:yaml.org,2002:null", "null"))
    return y


def load_rt(path: Path):
    return rt_yaml().load(path.read_text(encoding="utf-8"))


def dump_rt(data) -> str:
    buf = io.StringIO()
    rt_yaml().dump(data, buf)
    return buf.getvalue()


def save_text(path: Path, text: str):
    """Write a config file, keeping a timestamped backup of the previous version."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (BACKUPS / f"{path.stem}-{stamp}.yaml").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        for old in sorted(BACKUPS.glob(f"{path.stem}-*.yaml"))[:-20]:  # keep the last 20
            old.unlink()
    path.write_text(text, encoding="utf-8")


def validate_schema_text(text: str):
    """(schema or None, errors, warnings) for schema YAML text."""
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        return None, [f"YAML syntax error: {e}"], []
    return parse_schema(raw)


def save_schema(data_or_text) -> tuple[bool, list[str], list[str]]:
    """Validate, then save schema.yaml (with backup). Nothing is written if invalid."""
    text = data_or_text if isinstance(data_or_text, str) else dump_rt(data_or_text)
    schema, errors, warnings = validate_schema_text(text)
    if schema is None:
        return False, errors, warnings
    save_text(SCHEMA_PATH, text)
    return True, errors, warnings


def current_schema():
    return validate_schema_text(SCHEMA_PATH.read_text(encoding="utf-8"))


def settings() -> dict:
    return yaml.safe_load(SETTINGS_PATH.read_text(encoding="utf-8")) or {}


def output_dir() -> Path:
    return resolve((settings().get("output") or {}).get("dir", "data/output"))


# ------------------------------------------------------------------ data
@st.cache_data(show_spinner=False)
def _jsonl(path: str, mtime: float) -> list[dict]:
    return read_jsonl(Path(path))


def _load(name: str) -> list[dict]:
    p = output_dir() / name
    return _jsonl(str(p), p.stat().st_mtime) if p.exists() else []


def load_products() -> list[dict]:
    return _load(PRODUCTS_STORE)


def load_reviews() -> list[dict]:
    return _load(REVIEWS_STORE)


def load_rejected() -> pd.DataFrame:
    p = output_dir() / "rejected.csv"
    return pd.read_csv(p, encoding="utf-8-sig") if p.exists() else pd.DataFrame()


def show_problems(errors=(), warnings=()):
    for e in errors:
        st.error(e, icon=":material/error:")
    for w in warnings:
        st.warning(w, icon=":material/warning:")


# ------------------------------------------------------------ background jobs
@st.cache_resource
def _jobs() -> dict:
    """Popen handles survive Streamlit reruns (one server process)."""
    return {}


def start_job(args: list[str], env_extra: dict | None = None):
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    for p in (STOP, PROGRESS, LOG):
        p.unlink(missing_ok=True)
    cmd = [sys.executable, "-m", "amzpipe", *args,
           "--log-file", str(LOG), "--progress-file", str(PROGRESS), "--stop-file", str(STOP)]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env_extra or {})}
    if _jobs().get("console"):
        _jobs()["console"].close()
    console = open(RUN_DIR / "console.log", "w", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=console, stderr=subprocess.STDOUT, creationflags=flags)
    _jobs().update(current=proc, console=console)
    JOB.write_text(json.dumps({"pid": proc.pid, "kind": args[0], "started": time.time()}), encoding="utf-8")


def job_status() -> dict:
    """{'running': bool, 'kind': str, 'progress': {...}} for the current/last job."""
    proc = _jobs().get("current")
    job = json.loads(JOB.read_text(encoding="utf-8")) if JOB.exists() else {}
    prog = {}
    if PROGRESS.exists():
        try:
            prog = json.loads(PROGRESS.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            prog = {}
    running = proc is not None and proc.poll() is None
    if proc is not None and not running and prog.get("state") in (None, "running"):
        prog = {**prog, "state": "failed",
                "error": prog.get("error") or f"process exited with code {proc.returncode}; see the log"}
    return {"running": running, "kind": job.get("kind"), "started": job.get("started"), "progress": prog}


def request_stop():
    STOP.parent.mkdir(parents=True, exist_ok=True)
    STOP.write_text("stop", encoding="utf-8")


def log_tail(lines: int = 30) -> str:
    for p in (LOG, RUN_DIR / "console.log"):
        if p.exists() and p.stat().st_size:
            return "\n".join(p.read_text(encoding="utf-8", errors="ignore").splitlines()[-lines:])
    return ""


def llm_env() -> dict:
    """API key typed on the Settings page: passed to the job's environment, never written to disk."""
    key, env_name = st.session_state.get("llm_api_key"), st.session_state.get("llm_api_key_env")
    return {env_name: key} if key and env_name else {}
