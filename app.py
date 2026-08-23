"""
WT Quant Systems Dashboard - Streamlit + GitHub

Client-facing portfolio analytics for multiple Sierra Chart algo strategies.

Key principles:
- P/L is rebuilt from CLOSED/EXIT trades only.
- Equity and drawdown are recalculated AFTER every dashboard filter so the
  headline P/L and the chart always end at the same value.
- TradeAccount is the authoritative algorithm identifier (Sim1, Sim2, ...).
- New algos therefore appear automatically; removed algos simply stop producing
  new trades and remain available in historical periods.
- Multi-algo P/L and drawdown curves use a stable per-algo color mapping.
- Planned Risk:Reward is derived from RewardTicks / RiskTicks on closed trades.
- P/L is standardized from PNL_Ticks to ES (12.50 USD/tick, default) or MES (1.25 USD/tick).
- Duplicate closed-trade export rows are removed before any KPI is calculated.
- Dedicated Data Quality diagnostics identify the affected Sim/account and exact CSV source line(s).
- Global session view can be switched between Globex (RTH + ETH, default), RTH, and ETH.
"""
from __future__ import annotations

import base64
import csv
import html
import hashlib
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Tuple

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components
from plotly.subplots import make_subplots

APP_TITLE = "WT Quant Systems | Portfolio Analytics"
APP_VERSION = "2.0.18"
LOCAL_CSV = os.path.join("data", "trades.csv")
DEFAULT_REFRESH_SECONDS = 60

# Separate access protection for the sensitive Data Quality & Korrektur tab.
# Requested as a fixed password in code.
DATA_QUALITY_PASSWORD = "Hallo123"

# Dashboard palette only. It does not alter source data.
BG = "#070a10"
PANEL = "#0d121c"
PANEL_2 = "#111826"
BORDER = "#202a3a"
TEXT = "#e8edf5"
MUTED = "#8f9bad"
ACCENT = "#6aa7ff"
POSITIVE = "#2ed18a"
NEGATIVE = "#ff5f72"
WARNING = "#f3c969"
GRID = "rgba(143,155,173,0.14)"

# Stable, high-contrast algorithm colors for the dark dashboard.
# The same algorithm receives the same color in all multi-algo line charts.
ALGO_COLORS = [
    "#6AA7FF", "#2ED18A", "#F3C969", "#FF6B81", "#A88BFF",
    "#43D4FF", "#FF9F43", "#B8E986", "#E76BF3", "#7FDBFF",
    "#FF8A65", "#4DD0E1", "#C0CA33", "#AB47BC", "#FFA726",
    "#26A69A", "#EC407A", "#90A4AE", "#D4E157", "#5C6BC0",
]

# Standardized USD value per Sierra "tick" for S&P 500 futures.
# The dashboard can normalize a mixed ES/MES trade history to either basis.
CONTRACT_TICK_VALUES = {
    "MES": 1.25,
    "ES": 12.50,
}
DEFAULT_DISPLAY_CONTRACT = "ES"

# Session display control. Sierra timestamps are interpreted as Central Time (CT).
# RTH: 08:30 <= entry/signal time < 15:00 CT. ETH: all remaining valid Globex times.
# "Globex" is the union of RTH + ETH and is the default dashboard view.
SESSION_CHOICES = ["Globex", "RTH", "ETH"]
DEFAULT_SESSION_VIEW = "Globex"
RTH_START_MINUTE = 8 * 60 + 30
RTH_END_MINUTE = 15 * 60


@dataclass
class GitHubConfig:
    owner: str = ""
    repo: str = ""
    branch: str = "main"
    data_path: str = "data/trades.csv"
    token: str = ""


def _secret(name: str, default: str = "") -> str:
    try:
        value = st.secrets.get(name, default)
        return str(value) if value is not None else default
    except Exception:
        return os.getenv(name, default)


def get_github_config() -> GitHubConfig:
    try:
        gh = st.secrets.get("github", {})
    except Exception:
        gh = {}

    return GitHubConfig(
        owner=str(gh.get("owner", os.getenv("GITHUB_OWNER", ""))).strip(),
        repo=str(gh.get("repo", os.getenv("GITHUB_REPO", ""))).strip(),
        branch=str(gh.get("branch", os.getenv("GITHUB_BRANCH", "main"))).strip() or "main",
        data_path=str(gh.get("data_path", os.getenv("GITHUB_TARGET_PATH", "data/trades.csv"))).strip() or "data/trades.csv",
        token=str(gh.get("token", os.getenv("GITHUB_TOKEN", ""))).strip(),
    )


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        :root {{
            --wt-bg: {BG};
            --wt-panel: {PANEL};
            --wt-panel2: {PANEL_2};
            --wt-border: {BORDER};
            --wt-text: {TEXT};
            --wt-muted: {MUTED};
            --wt-accent: {ACCENT};
            --wt-positive: {POSITIVE};
            --wt-negative: {NEGATIVE};
            --wt-warning: {WARNING};
        }}

        .stApp {{ background: var(--wt-bg); color: var(--wt-text); }}
        [data-testid="stHeader"] {{ background: rgba(7,10,16,0.86); }}
        [data-testid="stSidebar"] {{
            background: #0a0f18;
            border-right: 1px solid var(--wt-border);
        }}
        [data-testid="stSidebar"] * {{ color: var(--wt-text); }}
        [data-testid="stMetric"] {{
            background: linear-gradient(180deg, rgba(17,24,38,0.96), rgba(13,18,28,0.96));
            border: 1px solid var(--wt-border);
            padding: 14px 16px;
            border-radius: 12px;
            min-height: 110px;
        }}
        [data-testid="stMetricLabel"] {{ color: var(--wt-muted); }}
        [data-testid="stMetricValue"] {{ color: var(--wt-text); }}
        [data-testid="stMetricDelta"] {{ font-size: .80rem; }}
        [data-baseweb="tab-list"] {{ gap: 4px; border-bottom: 1px solid var(--wt-border); }}
        [data-baseweb="tab"] {{
            height: 46px;
            padding-left: 16px;
            padding-right: 16px;
            color: var(--wt-muted);
        }}
        [aria-selected="true"][data-baseweb="tab"] {{ color: var(--wt-text); }}
        .block-container {{ max-width: 1650px; padding-top: 1.6rem; padding-bottom: 3rem; }}

        .wt-topline {{
            display:flex; align-items:center; justify-content:space-between; gap:14px;
            margin-bottom: 14px;
        }}
        .wt-brand {{
            font-size:.75rem; letter-spacing:.16em; font-weight:700; color:var(--wt-accent);
            text-transform:uppercase;
        }}
        .wt-title {{
            font-size:1.65rem; line-height:1.15; font-weight:700; color:var(--wt-text); margin-top:3px;
        }}
        .wt-subtitle {{ color:var(--wt-muted); font-size:.92rem; margin-top:5px; }}
        .wt-badge {{
            display:inline-flex; align-items:center; gap:7px; border:1px solid var(--wt-border);
            background:var(--wt-panel); color:var(--wt-muted); border-radius:999px;
            padding:7px 11px; font-size:.76rem; font-weight:650; white-space:nowrap;
        }}
        .wt-dot {{ width:7px; height:7px; border-radius:50%; background:var(--wt-positive); display:inline-block; }}
        .wt-dot.sim {{ background:var(--wt-warning); }}

        .wt-hero {{
            display:grid; grid-template-columns:minmax(320px,1.5fr) minmax(240px,.7fr) minmax(240px,.7fr);
            gap:12px; margin:10px 0 14px 0;
        }}
        .wt-card {{
            background: linear-gradient(160deg, rgba(17,24,38,.98), rgba(12,17,26,.98));
            border:1px solid var(--wt-border); border-radius:14px; padding:18px 20px;
            box-shadow:0 10px 28px rgba(0,0,0,.13);
        }}
        .wt-card.primary {{
            border-color:rgba(106,167,255,.28);
            background: radial-gradient(circle at 0% 0%, rgba(106,167,255,.10), transparent 40%),
                        linear-gradient(160deg, rgba(17,24,38,.98), rgba(12,17,26,.98));
        }}
        .wt-label {{
            color:var(--wt-muted); font-size:.72rem; letter-spacing:.08em; text-transform:uppercase;
            font-weight:700;
        }}
        .wt-value {{ color:var(--wt-text); font-size:2.42rem; line-height:1.05; font-weight:750; margin-top:8px; }}
        .wt-value.positive {{ color:var(--wt-positive); }}
        .wt-value.negative {{ color:var(--wt-negative); }}
        .wt-value.small {{ font-size:1.55rem; }}
        .wt-meta {{ color:var(--wt-muted); font-size:.82rem; margin-top:10px; line-height:1.45; }}
        .wt-meta strong {{ color:var(--wt-text); font-weight:650; }}

        .wt-section-title {{ font-size:1.08rem; font-weight:700; color:var(--wt-text); margin:12px 0 3px 0; }}
        .wt-section-sub {{ color:var(--wt-muted); font-size:.83rem; margin-bottom:9px; }}

        .wt-status-grid {{ display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:10px; }}
        .wt-status-item {{
            background:var(--wt-panel); border:1px solid var(--wt-border); border-radius:11px; padding:12px 14px;
        }}
        .wt-status-k {{ color:var(--wt-muted); font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; }}
        .wt-status-v {{ color:var(--wt-text); font-size:.94rem; margin-top:4px; overflow-wrap:anywhere; }}

        .wt-disclosure {{
            color:var(--wt-muted); border-top:1px solid var(--wt-border); margin-top:22px; padding-top:14px;
            font-size:.76rem; line-height:1.55;
        }}
        .wt-positive {{ color:var(--wt-positive); }}
        .wt-negative {{ color:var(--wt-negative); }}
        .wt-muted {{ color:var(--wt-muted); }}

        @media (max-width: 1050px) {{
            .wt-hero {{ grid-template-columns:1fr; }}
            .wt-status-grid {{ grid-template-columns:repeat(2, minmax(0,1fr)); }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def require_password() -> None:
    password = _secret("DASHBOARD_PASSWORD", "").strip()
    if not password:
        st.sidebar.warning(
            "Kein Dashboard-Passwort gesetzt. Für eine kundenfähige öffentliche App unbedingt ein Passwort setzen."
        )
        return

    if st.session_state.get("auth_ok") is True:
        return

    st.title("WT Quant Systems")
    st.caption("Secure Portfolio Analytics")
    entered = st.text_input("Dashboard-Passwort", type="password")
    if st.button("Einloggen", type="primary"):
        if entered == password:
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Passwort falsch.")
    st.stop()


def normalize_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        try:
            if pd.isna(value):
                return 0.0
        except Exception:
            pass
        return float(value)
    s = str(value).strip()
    if not s:
        return 0.0
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    cleaned = "".join(ch for ch in s if ch in "0123456789.-")
    try:
        return float(cleaned)
    except Exception:
        return 0.0


def safe_col(df: pd.DataFrame, col: str, default: Any = "") -> pd.Series:
    if col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def clean_label(value: Any, fallback: str = "Nicht angegeben") -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "<na>"}:
        return fallback
    return text


def detect_contract_from_symbol(symbol: Any) -> str:
    """Identify MES vs ES from common Sierra/CQG symbols.

    Examples used in this project:
    - F.US.MESU26 -> MES
    - F.US.EPU26  -> ES (CQG EP root for E-mini S&P)
    """
    text = clean_label(symbol, "").upper().replace(" ", "")
    if not text:
        return "UNKNOWN"

    # MES must be tested first because it also contains the letters "ES".
    if "MES" in text:
        return "MES"

    tail = text.split(".")[-1]
    tail = re.sub(r"^\[SIM\]", "", tail)
    if tail.startswith("EP") or tail.startswith("ES"):
        return "ES"

    # Additional common forms such as [Sim]F.US.EPU26.
    if re.search(r"(?:^|\.)EP[A-Z]\d{1,2}$", text) or re.search(r"(?:^|\.)ES[A-Z]\d{1,2}$", text):
        return "ES"
    return "UNKNOWN"


def normalize_pnl_to_contract(out: pd.DataFrame, display_contract: str) -> pd.DataFrame:
    """Standardize monetary P/L to the selected ES or MES tick basis.

    PNL_Ticks is the canonical realized price result. Therefore every trade with
    a non-zero tick result is displayed as:

        PNL_Ticks × selected tick value

    This makes the ES/MES selector deterministic and prevents mixed/incorrect
    source-dollar scaling from changing portfolio statistics. The original
    exported dollar P/L is preserved only for audit in PNL_Currency_Source.

    ExportTickValue and PNL_Adjustment_Source describe how the source row appears
    to have been monetized; they never alter standardized dashboard P/L.
    """
    contract = str(display_contract or DEFAULT_DISPLAY_CONTRACT).upper().strip()
    if contract not in CONTRACT_TICK_VALUES:
        contract = DEFAULT_DISPLAY_CONTRACT
    target_tick_value = float(CONTRACT_TICK_VALUES[contract])

    result = out.copy()
    result["PNL_Currency_Source"] = pd.to_numeric(
        result.get("PNL_Currency", 0.0), errors="coerce"
    ).fillna(0.0)
    result["SourceContract"] = result.get(
        "Symbol", pd.Series("", index=result.index)
    ).map(detect_contract_from_symbol)
    result["DisplayContract"] = contract
    result["DisplayTickValue"] = target_tick_value

    ticks = pd.to_numeric(result.get("PNL_Ticks", 0.0), errors="coerce").fillna(0.0)
    source_pnl = result["PNL_Currency_Source"]

    # Audit-only inference of the source monetary basis.
    observed_ratio = (source_pnl.abs() / ticks.abs().replace(0.0, pd.NA)).astype("Float64")

    def nearest_known_tick(value: Any) -> float:
        try:
            v = float(value)
        except Exception:
            return float("nan")
        if not pd.notna(v) or v <= 0:
            return float("nan")
        candidates = list(CONTRACT_TICK_VALUES.values())
        nearest = min(candidates, key=lambda x: abs(v - x))
        if abs(v - nearest) / nearest <= 0.20:
            return float(nearest)
        return float("nan")

    export_tick_value = observed_ratio.map(nearest_known_tick)
    symbol_tick_value = result["SourceContract"].map(CONTRACT_TICK_VALUES).astype("Float64")
    export_tick_value = export_tick_value.fillna(symbol_tick_value).fillna(target_tick_value).astype(float)
    result["ExportTickValue"] = export_tick_value

    source_tick_component = ticks * export_tick_value
    result["PNL_Adjustment_Source"] = (source_pnl - source_tick_component).astype(float)

    # Canonical standardized display P/L. No source-dollar residual is carried
    # into performance figures because the requested ES/MES view is a pure
    # tick-value normalization.
    normalized = ticks * target_tick_value

    # If a legacy row has non-zero dollars but zero ticks, PNL_Ticks cannot be
    # used. Only for that exceptional row do we scale the source monetary value
    # by its inferred/source contract basis.
    fallback_mask = (ticks == 0.0) & (source_pnl != 0.0)
    if fallback_mask.any():
        base_tick = pd.to_numeric(export_tick_value, errors="coerce").replace(0.0, pd.NA)
        factor = target_tick_value / base_tick
        fallback = source_pnl * factor.fillna(1.0)
        normalized = normalized.where(~fallback_mask, fallback)

    result["PNL_Currency"] = normalized.astype(float)
    return result


@st.cache_data(ttl=15, show_spinner=False)
def fetch_from_github(owner: str, repo: str, branch: str, data_path: str, token: str) -> Tuple[str, Dict[str, Any]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{data_path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"ref": branch} if branch else {}
    r = requests.get(url, headers=headers, params=params, timeout=20)
    if r.status_code == 404:
        raise FileNotFoundError(f"GitHub-Datei nicht gefunden: {owner}/{repo}/{data_path} auf Branch {branch}")
    r.raise_for_status()
    meta = r.json()
    content = base64.b64decode(meta.get("content", "")).decode("utf-8-sig", errors="replace")
    info = {
        "source": "github",
        "path": f"{owner}/{repo}/{data_path}",
        "branch": branch,
        "sha": meta.get("sha", ""),
        "size": meta.get("size", 0),
        "download_url": meta.get("download_url", ""),
        "loaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return content, info


def read_local_csv(path: str = LOCAL_CSV) -> Tuple[str, Dict[str, Any]]:
    if not os.path.exists(path):
        return "", {"source": "local", "path": path, "error": "Lokale CSV nicht gefunden"}
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        content = f.read()
    stat = os.stat(path)
    return content, {
        "source": "local",
        "path": path,
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "loaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def detect_dialect(text: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except Exception:
        return csv.excel


def parse_csv(text: str) -> pd.DataFrame:
    if not text or not text.strip():
        return pd.DataFrame()
    dialect = detect_dialect(text)
    df = pd.read_csv(io.StringIO(text), sep=dialect.delimiter, dtype=str, keep_default_na=False)

    # Some Sierra exporters append legacy semicolons to the Notes header/value.
    # They are not field separators in this comma-delimited file and should not
    # become part of the logical column name.
    df.columns = [re.sub(r";+$", "", str(c).strip()) for c in df.columns]
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()
    if "Notes" in df.columns:
        df["Notes"] = df["Notes"].astype(str).str.replace(r";+$", "", regex=True).str.strip()

    # Physical source-line reference for audit. The CSV header is line 1, so the
    # first data record is line 2. BlueBlack exports in this project are
    # one-record-per-line, which makes this a reliable operator reference.
    if "CSVLine" not in df.columns:
        df["CSVLine"] = range(2, len(df) + 2)
    return adapt_input_schema(df)


SIERRA_TRADES_REQUIRED_COLUMNS = {
    "Trade Type",
    "Symbol",
    "Entry DateTime",
    "Exit DateTime",
    "Entry Price",
    "Exit Price",
    "Trade Quantity",
    "Profit/Loss (T)",
    "Account",
}


def _stable_sierra_trade_id(row: pd.Series) -> str:
    """Create a stable internal ID for Sierra Trades rows.

    The new Sierra Trade Activity Log -> Trades export contains a complete
    closed trade in one row but no TradeID column. The hash is intentionally
    based on execution fields, not CSV row number. If two legitimate limit
    orders have identical execution data they receive the same logical ID;
    both rows are still preserved and the existing Multi-Limit audit can show
    them as informational repeated-looking trades.
    """
    values = [
        clean_label(row.get("Account", ""), ""),
        clean_label(row.get("Symbol", ""), ""),
        clean_label(row.get("Entry DateTime", ""), ""),
        clean_label(row.get("Exit DateTime", ""), ""),
        clean_label(row.get("Entry Price", ""), ""),
        clean_label(row.get("Exit Price", ""), ""),
        clean_label(row.get("Trade Quantity", ""), ""),
        clean_label(row.get("Profit/Loss (T)", ""), ""),
        clean_label(row.get("Note", ""), ""),
    ]
    digest = hashlib.sha1("|".join(values).encode("utf-8", errors="replace")).hexdigest()[:18]
    account = re.sub(r"[^A-Za-z0-9_-]+", "", values[0]) or "NA"
    return f"SC-{account}-{digest}"


def adapt_input_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Map supported source schemas into the dashboard's canonical fields.

    Existing/legacy BlueBlack CSV:
        Passed through unchanged.

    New Sierra Chart source:
        Sierra Chart -> Trade -> Trade Activity Log -> Trades
        One source row already represents one completed trade. The adapter
        creates the canonical fields expected by the dashboard without changing
        any dashboard layout, filters, KPI logic, tabs, charts or audit features.

    Fields not present in the new Sierra Trades export (for example original
    StopPrice, TargetPrice, RiskTicks, RewardTicks and ExitReason) are not
    invented. They stay empty/zero so the dashboard never fabricates trading
    information that the source file does not provide.
    """
    if df.empty:
        return df

    cols = set(map(str, df.columns))
    if not SIERRA_TRADES_REQUIRED_COLUMNS.issubset(cols):
        # Existing BlueBlack format remains exactly as before.
        return df

    out = df.copy()
    out["SourceFormat"] = "SIERRA_TRADE_ACTIVITY_TRADES"

    # One Sierra Trades row is already a complete CLOSED trade.
    out["RowType"] = "EXIT"
    out["TradeAccount"] = safe_col(out, "Account", "").astype(str).str.strip()
    out["TradeID"] = out.apply(_stable_sierra_trade_id, axis=1)

    # Preserve physical CSV source references.
    if "CSVLine" not in out.columns:
        out["CSVLine"] = range(2, len(out) + 2)
    out["EntryCSVLine"] = out["CSVLine"]

    # Sierra Trade Activity Log supplies both entry and exit timestamps on the
    # same row. DateTime remains the dashboard's closed-trade timestamp.
    out["DateTime"] = safe_col(out, "Exit DateTime", "")
    out["SignalDateTime"] = safe_col(out, "Entry DateTime", "")
    out["EntryDateTime_Source"] = safe_col(out, "Entry DateTime", "")

    # Normalize only the cosmetic [Sim] symbol prefix. Contract identity remains.
    out["Symbol"] = (
        safe_col(out, "Symbol", "")
        .astype(str)
        .str.replace(r"^\[Sim\]", "", regex=True)
        .str.strip()
    )
    out["Direction"] = safe_col(out, "Trade Type", "").astype(str).str.upper().str.strip()

    out["EntryFillPrice"] = safe_col(out, "Entry Price", "")
    out["ExitPrice"] = safe_col(out, "Exit Price", "")
    out["Quantity"] = safe_col(out, "Trade Quantity", "")
    out["PNL_Ticks"] = safe_col(out, "Profit/Loss (T)", "")

    # The new file has tick P/L but no PNL_Currency column. Build the audit-side
    # source currency deterministically from the contract represented by Symbol.
    # Dashboard performance still uses its existing canonical PNL_Ticks logic.
    pnl_ticks = safe_col(out, "Profit/Loss (T)", "").map(normalize_number)
    source_contract = out["Symbol"].map(detect_contract_from_symbol)
    source_tick_value = source_contract.map(CONTRACT_TICK_VALUES).fillna(
        CONTRACT_TICK_VALUES[DEFAULT_DISPLAY_CONTRACT]
    )
    out["PNL_Currency"] = pnl_ticks * source_tick_value

    cumulative_ticks = safe_col(out, "Cumulative Profit/Loss (T)", "").map(normalize_number)
    out["CumPNL_Currency"] = cumulative_ticks * source_tick_value

    max_dd_ticks = safe_col(out, "Maximum Drawdown (T)", "").map(normalize_number)
    out["MaxDrawdown_Currency"] = max_dd_ticks * source_tick_value

    # MAE/MFE are available in the new Sierra source as Max Open Loss/Profit.
    out["MAE_Ticks"] = safe_col(out, "Max Open Loss (T)", "").map(
        lambda v: abs(normalize_number(v))
    )
    out["MFE_Ticks"] = safe_col(out, "Max Open Profit (T)", "").map(
        lambda v: max(0.0, normalize_number(v))
    )

    # Preserve the source note verbatim for module/strategy metadata parsing.
    out["Notes"] = safe_col(out, "Note", "").astype(str).str.strip()

    # The following legacy BlueBlack fields are not present in Sierra's Trades
    # list. Keep neutral values instead of inventing strategy parameters.
    out["CountColor"] = ""
    out["ExitReason"] = ""
    out["EntryLevel4"] = 0.0
    out["StopPrice"] = 0.0
    out["TargetPrice"] = 0.0
    out["RiskTicks"] = 0.0
    out["RewardTicks"] = 0.0

    return out


def _first_nonempty(row: pd.Series, candidates: Iterable[str]) -> str:
    for c in candidates:
        if c in row.index:
            value = clean_label(row.get(c), "")
            if value:
                return value
    return ""


def extract_module(notes: Any) -> str:
    text = clean_label(notes, "")
    if not text:
        return "Nicht angegeben"

    # Existing BlueBlack format.
    m = re.search(r"(?:^|;)\s*Module=([^;]+)", text, flags=re.IGNORECASE)
    if m:
        return clean_label(m.group(1), "Nicht angegeben")

    # New Sierra Trade Activity Log -> Trades Note formats.
    parts = [p.strip() for p in text.split("|") if p.strip()]
    if parts:
        head = parts[0].upper()
        if head.startswith("WTROUND") and len(parts) >= 3:
            return clean_label(parts[2], "Nicht angegeben")
        if head == "WT_P3_LIFECYCLE_ROBUST" and len(parts) >= 2:
            return clean_label(parts[1], "Nicht angegeben")
        if head in {"WT-HCRV", "WT_HCRV"} and len(parts) >= 2:
            return clean_label(parts[1], "Nicht angegeben")

    upper = text.upper()
    if upper == "WT_RTH_MAE200":
        return "RTH_MAE200"
    if upper == "WT_OVERNIGHT_MAE":
        return "OVERNIGHT_MAE"

    # Example: WT_HCRV_RTH_B_<id>_G1_TM_CHAMPION_V2_0
    m = re.match(
        r"WT_HCRV_(RTH_[AB])_\d+_(G1_TM_CHAMPION_V\d+_\d+)$",
        upper,
    )
    if m:
        return f"{m.group(1)}_{m.group(2)}"

    return "Nicht angegeben"


def extract_version(notes: Any) -> str:
    text = clean_label(notes, "")
    if not text:
        return ""
    head = text.split(";", 1)[0]
    m = re.search(r"\b(v?\d+(?:\.\d+)+(?:[-._A-Za-z0-9]+)?)\b", head)
    return m.group(1) if m else ""


def normalize_algo_from_notes(notes: Any) -> str:
    text = clean_label(notes, "")
    if not text:
        return ""
    head = text.split(";", 1)[0].strip()
    # Keep the human strategy family, strip build/version suffixes.
    version_match = re.search(r"\s+v?\d+(?:\.\d+)+(?:[-._A-Za-z0-9]+)?", head, flags=re.IGNORECASE)
    if version_match:
        head = head[: version_match.start()].strip()
    head = re.sub(r"\s+AutoTrader\s*$", "", head, flags=re.IGNORECASE).strip()
    head = re.sub(r"\s+[-–—]\s*$", "", head).strip()
    if head.lower() in {"portfolio", "mae-200", "unknown", "unbekannt"}:
        return ""
    return head


def derive_algo_name(row: pd.Series) -> str:
    # In this Sierra setup the TradeAccount (Sim1, Sim2, ... / later live account IDs)
    # is the authoritative algorithm identifier. CountColor is only a tag and Notes
    # describe strategy/module metadata; neither may replace an existing Sim account.
    account = clean_label(row.get("TradeAccount", ""), "")
    if account:
        return account

    # Fallbacks only matter for future/foreign exports that do not contain TradeAccount.
    explicit = _first_nonempty(
        row,
        ["Algo", "Algorithm", "AlgorithmName", "Strategy", "StrategyName", "System", "Model", "AlgoName"],
    )
    if explicit:
        return explicit

    from_notes = normalize_algo_from_notes(row.get("Notes", ""))
    if from_notes:
        return from_notes

    legacy = clean_label(row.get("CountColor", ""), "")
    if legacy and legacy.lower() not in {"portfolio", "blau", "schwarz", "blue", "black", "nicht angegeben"}:
        return legacy

    return "Unbekannter Algo"


def assign_trade_session(out: pd.DataFrame) -> pd.DataFrame:
    """Assign every closed trade to RTH or ETH using its entry/signal timestamp.

    Priority:
    1. If a future export contains an explicit RTH/ETH session field, use it.
    2. Otherwise use EntryDateTime from the matching ENTRY row when available.
    3. Fall back to SignalDateTime, then finally to the EXIT DateTime.

    Sierra timestamps in this project are interpreted as Central Time (CT).
    RTH is 08:30 inclusive to 15:00 exclusive. ETH is the remaining Globex time.
    "Globex" itself is a dashboard view (RTH + ETH), not a per-trade label.
    """
    result = out.copy()
    if result.empty:
        result["SessionDateTime"] = pd.Series(dtype="datetime64[ns]")
        result["TradeSession"] = pd.Series(dtype=str)
        return result

    entry_dt = result.get("EntryDateTime", pd.Series(pd.NaT, index=result.index))
    signal_dt = result.get("SignalDateTime", pd.Series(pd.NaT, index=result.index))
    exit_dt = result.get("DateTime", pd.Series(pd.NaT, index=result.index))
    result["SessionDateTime"] = entry_dt.where(entry_dt.notna(), signal_dt).where(
        entry_dt.notna() | signal_dt.notna(), exit_dt
    )

    session_dt = result["SessionDateTime"]
    minute_of_day = session_dt.dt.hour * 60 + session_dt.dt.minute
    valid_dt = session_dt.notna()
    is_rth = valid_dt & (minute_of_day >= RTH_START_MINUTE) & (minute_of_day < RTH_END_MINUTE)

    result["TradeSession"] = "UNKNOWN"
    result.loc[valid_dt & ~is_rth, "TradeSession"] = "ETH"
    result.loc[is_rth, "TradeSession"] = "RTH"

    # Honor an explicit per-trade session label if a future source file provides one.
    # Values such as Globex are intentionally ignored because Globex = RTH + ETH here.
    for source_col in ["Session", "SessionType", "TradingSession", "MarketSession"]:
        if source_col not in result.columns:
            continue
        explicit = result[source_col].astype(str).str.upper().str.strip()
        explicit = explicit.replace({
            "REGULAR": "RTH",
            "REGULAR HOURS": "RTH",
            "REGULAR TRADING HOURS": "RTH",
            "EXTENDED": "ETH",
            "EXTENDED HOURS": "ETH",
            "EXTENDED TRADING HOURS": "ETH",
        })
        mask = explicit.isin(["RTH", "ETH"])
        result.loc[mask, "TradeSession"] = explicit.loc[mask]

    return result


def deduplicate_closed_rows(out: pd.DataFrame) -> pd.DataFrame:
    """Preserve every closed EXIT row.

    IMPORTANT:
    In this Sierra setup an algo may legitimately place two or more limit orders
    at the same level. Those orders can be entered and exited with identical
    price/time/P&L fields. Therefore same-looking trades are NOT duplicates by
    definition and must never be removed automatically.

    The Data Quality tab may show repeated-looking ENTRY/EXIT groups as INFO for
    transparency, but all rows remain in the performance calculation.

    A future source-level duplicate-removal rule may only be enabled if the
    export provides a genuinely unique execution/order identifier that proves
    two rows are the same physical execution record.
    """
    return out.copy()


def prepare_trades(df: pd.DataFrame, display_contract: str = DEFAULT_DISPLAY_CONTRACT) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    if "RowType" not in out.columns:
        out["RowType"] = "EXIT"

    out["RowType"] = out["RowType"].astype(str).str.upper().str.strip()

    # Preserve the actual ENTRY timestamp before reducing the dataset to closed rows.
    # TradeID alone is not globally unique across Sim accounts, so the lookup key is
    # (TradeAccount, TradeID). This avoids assigning an entry from another algo/account.
    raw_trade_id = safe_col(out, "TradeID", "").astype(str).str.strip()
    raw_account = safe_col(out, "TradeAccount", "").astype(str).str.strip()
    raw_datetime = pd.to_datetime(safe_col(out, "DateTime", ""), errors="coerce")
    entry_mask = out["RowType"].isin(["ENTRY", "OPEN", "TRADE_ENTRY", "B"])
    raw_csv_line = pd.to_numeric(safe_col(out, "CSVLine", 0), errors="coerce").fillna(0).astype(int)
    entry_lookup_df = pd.DataFrame({
        "TradeAccount": raw_account[entry_mask],
        "TradeID": raw_trade_id[entry_mask],
        "EntryDateTime": raw_datetime[entry_mask],
        "EntryCSVLine": raw_csv_line[entry_mask],
    })
    entry_lookup_df = entry_lookup_df[
        (entry_lookup_df["TradeAccount"] != "")
        & (entry_lookup_df["TradeID"] != "")
        & entry_lookup_df["EntryDateTime"].notna()
    ]
    if not entry_lookup_df.empty:
        entry_lookup_df = (
            entry_lookup_df.sort_values("EntryDateTime")
            .drop_duplicates(["TradeAccount", "TradeID"], keep="first")
        )

    out = out[out["RowType"].isin(["EXIT", "CLOSE", "CLOSED", "TRADE_EXIT", "E"])]
    if out.empty:
        return out

    # Preserve every EXIT row. Same-looking rows may be legitimate executions
    # from multiple limit orders at the same level and must not be collapsed.
    out = deduplicate_closed_rows(out)

    numeric_cols = [
        "EntryLevel4", "EntryFillPrice", "ExitPrice", "StopPrice", "TargetPrice",
        "RiskTicks", "RewardTicks", "Quantity", "PNL_Ticks", "PNL_Currency",
        "MAE_Ticks", "MFE_Ticks", "CumPNL_Currency", "MaxDrawdown_Currency",
    ]
    for c in numeric_cols:
        out[c] = safe_col(out, c, 0).map(normalize_number)

    out["DateTime"] = pd.to_datetime(safe_col(out, "DateTime", ""), errors="coerce")
    out["SignalDateTime"] = pd.to_datetime(safe_col(out, "SignalDateTime", ""), errors="coerce")
    out["TradeID"] = safe_col(out, "TradeID", "").astype(str).str.strip()
    out["TradeAccount"] = safe_col(out, "TradeAccount", "").map(clean_label)
    if not entry_lookup_df.empty:
        out = out.merge(entry_lookup_df, on=["TradeAccount", "TradeID"], how="left")
    else:
        out["EntryDateTime"] = pd.NaT

    # New Sierra Trades schema already contains Entry DateTime on the same
    # closed-trade row. Use it when no separate legacy ENTRY row exists.
    direct_entry_dt = pd.to_datetime(
        safe_col(out, "EntryDateTime_Source", ""), errors="coerce"
    )
    out["EntryDateTime"] = out["EntryDateTime"].where(
        out["EntryDateTime"].notna(), direct_entry_dt
    )
    if "EntryCSVLine" not in out.columns:
        out["EntryCSVLine"] = pd.to_numeric(
            safe_col(out, "CSVLine", 0), errors="coerce"
        ).fillna(0).astype(int)

    out["Symbol"] = safe_col(out, "Symbol", "").map(clean_label)
    out["CountColor"] = (
        safe_col(out, "CountColor", "")
        .replace({"BLUE": "Blau", "BLACK": "Schwarz"})
        .map(clean_label)
    )
    out["Direction"] = safe_col(out, "Direction", "").astype(str).str.upper().map(clean_label)
    out["ExitReason"] = safe_col(out, "ExitReason", "").map(clean_label)
    out["Notes"] = safe_col(out, "Notes", "").astype(str)

    out["Algo"] = out.apply(derive_algo_name, axis=1)
    out["AlgoVersion"] = out["Notes"].map(extract_version)
    out["Module"] = out["Notes"].map(extract_module)

    # Classify session from the entry/signal timestamp before any dashboard filter is applied.
    out = assign_trade_session(out)

    # Reprice every closed trade to the selected ES/MES display basis before any
    # KPI, equity, drawdown or strategy aggregation is calculated.
    out = normalize_pnl_to_contract(out, display_contract)

    out["Win"] = out["PNL_Currency"] > 0
    out["Loss"] = out["PNL_Currency"] < 0
    out["Day"] = out["DateTime"].dt.date
    out["Week"] = out["DateTime"].dt.to_period("W").astype(str)
    out["Month"] = out["DateTime"].dt.to_period("M").astype(str)
    out["Year"] = out["DateTime"].dt.year.astype("Int64").astype(str).replace("<NA>", "")

    return out.sort_values(["DateTime", "TradeID"], na_position="last").reset_index(drop=True)


def recompute_curve(trades: pd.DataFrame, risk_limit_ticks: float) -> pd.DataFrame:
    """Rebuild all path-dependent metrics *after* filters are applied."""
    if trades.empty:
        return trades.copy()
    out = trades.sort_values(["DateTime", "TradeID"], na_position="last").copy().reset_index(drop=True)
    out["Equity"] = out["PNL_Currency"].cumsum()
    out["EquityHigh"] = out["Equity"].cummax().clip(lower=0.0)
    out["Drawdown"] = out["Equity"] - out["EquityHigh"]
    out["RiskViolation"] = out["RiskTicks"] > float(risk_limit_ticks)
    out["TradeNo"] = range(1, len(out) + 1)
    return out


def longest_streak(flags: pd.Series) -> int:
    best = cur = 0
    for v in flags.fillna(False).tolist():
        if bool(v):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def calc_summary(trades: pd.DataFrame) -> Dict[str, Any]:
    if trades.empty:
        return {
            "trades": 0, "net": 0.0, "wins": 0, "losses": 0, "winrate": 0.0,
            "gross_profit": 0.0, "gross_loss": 0.0, "profit_factor": 0.0,
            "max_dd": 0.0, "avg_trade": 0.0, "best": 0.0, "worst": 0.0,
            "risk_violations": 0, "win_streak": 0, "loss_streak": 0,
            "avg_win": 0.0, "avg_loss": 0.0, "payoff": 0.0, "recovery": 0.0,
            "profitable_days": 0.0, "days": 0,
        }

    pnl = trades["PNL_Currency"]
    wins_mask = pnl > 0
    losses_mask = pnl < 0
    wins = int(wins_mask.sum())
    losses = int(losses_mask.sum())
    gross_profit = float(pnl[wins_mask].sum())
    gross_loss = float(pnl[losses_mask].sum())
    avg_win = float(pnl[wins_mask].mean()) if wins else 0.0
    avg_loss = float(pnl[losses_mask].mean()) if losses else 0.0
    pf = gross_profit / abs(gross_loss) if gross_loss < 0 else (float("inf") if gross_profit > 0 else 0.0)
    max_dd = float(trades["Drawdown"].min()) if "Drawdown" in trades else 0.0
    net = float(pnl.sum())
    payoff = avg_win / abs(avg_loss) if avg_loss < 0 else (float("inf") if avg_win > 0 else 0.0)
    recovery = net / abs(max_dd) if max_dd < 0 else (float("inf") if net > 0 else 0.0)

    valid_days = trades.dropna(subset=["DateTime"]).copy()
    daily = valid_days.groupby(valid_days["DateTime"].dt.date)["PNL_Currency"].sum()
    profitable_days = float((daily > 0).mean() * 100) if len(daily) else 0.0

    return {
        "trades": int(len(trades)),
        "net": net,
        "wins": wins,
        "losses": losses,
        "winrate": float(wins / len(trades) * 100) if len(trades) else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": pf,
        "max_dd": max_dd,
        "avg_trade": float(pnl.mean()) if len(trades) else 0.0,
        "best": float(pnl.max()) if len(trades) else 0.0,
        "worst": float(pnl.min()) if len(trades) else 0.0,
        "risk_violations": int(trades["RiskViolation"].sum()) if "RiskViolation" in trades else 0,
        "win_streak": longest_streak(wins_mask),
        "loss_streak": longest_streak(losses_mask),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": payoff,
        "recovery": recovery,
        "profitable_days": profitable_days,
        "days": int(len(daily)),
    }


def money(x: float) -> str:
    return f"{x:,.2f} $".replace(",", "X").replace(".", ",").replace("X", ".")


def num(x: float, decimals: int = 2) -> str:
    if x == float("inf"):
        return "∞"
    if x == float("-inf"):
        return "-∞"
    return f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(x: float, decimals: int = 1) -> str:
    return f"{num(x, decimals)} %"


def pnl_css(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return ""


def period_label(trades: pd.DataFrame) -> str:
    dates = trades["DateTime"].dropna() if "DateTime" in trades else pd.Series(dtype="datetime64[ns]")
    if dates.empty:
        return "Ausgewählter Zeitraum"
    a, b = dates.min(), dates.max()
    if a.date() == b.date():
        return a.strftime("%d.%m.%Y")
    return f"{a.strftime('%d.%m.%Y')} – {b.strftime('%d.%m.%Y')}"


def execution_mode(trades: pd.DataFrame) -> str:
    if trades.empty:
        return "NO DATA"
    accounts = [str(x).strip().lower() for x in trades["TradeAccount"].dropna().tolist() if str(x).strip()]
    notes = " ".join(trades.get("Notes", pd.Series(dtype=str)).astype(str).tolist()).lower()
    if accounts and all(a.startswith("sim") for a in accounts):
        return "SIMULATION"
    if "simulation" in notes and "live" not in notes:
        return "SIMULATION"
    return "ACCOUNT DATA"


def latest_trade_label(trades: pd.DataFrame) -> str:
    if trades.empty or not trades["DateTime"].notna().any():
        return "–"
    return trades["DateTime"].max().strftime("%d.%m.%Y %H:%M:%S")


def show_header(trades: pd.DataFrame, info: Dict[str, Any], session_view: str = DEFAULT_SESSION_VIEW) -> None:
    mode = execution_mode(trades)
    dot_class = "sim" if mode == "SIMULATION" else ""
    display_contract = DEFAULT_DISPLAY_CONTRACT
    if not trades.empty and "DisplayContract" in trades.columns:
        display_contract = clean_label(trades["DisplayContract"].iloc[0], DEFAULT_DISPLAY_CONTRACT).upper()
    tick_value = CONTRACT_TICK_VALUES.get(display_contract, CONTRACT_TICK_VALUES[DEFAULT_DISPLAY_CONTRACT])
    basis_label = f"{display_contract} · {num(tick_value, 2)} $/Tick"
    session_badge = "GLOBEX" if session_view == "Globex" else session_view.upper()
    st.markdown(
        f"""
        <div class="wt-topline">
          <div>
            <div class="wt-brand">WellenTektonik Quant Systems</div>
            <div class="wt-title">Portfolio Analytics</div>
            <div class="wt-subtitle">Multi-Strategy · Realized P/L · Risk & Execution Audit</div>
          </div>
          <div class="wt-badge"><span class="wt-dot {dot_class}"></span>{html.escape(mode)} · {html.escape(session_badge)} · {html.escape(basis_label)} · v{APP_VERSION}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_hero(summary: Dict[str, Any], trades: pd.DataFrame) -> None:
    gross = summary["gross_profit"] + abs(summary["gross_loss"])
    gross_label = money(gross)
    st.markdown(
        f"""
        <div class="wt-hero">
          <div class="wt-card primary">
            <div class="wt-label">Realized Portfolio P/L · {html.escape(period_label(trades))}</div>
            <div class="wt-value {pnl_css(summary['net'])}">{money(summary['net'])}</div>
            <div class="wt-meta">
              <strong>{summary['trades']} geschlossene Trades</strong> · {summary['wins']} Gewinner / {summary['losses']} Verlierer ·
              Brutto-Umsatz P/L {gross_label}<br>
              Die Equity-Kurve wird exakt aus denselben gefilterten Trades neu berechnet.
            </div>
          </div>
          <div class="wt-card">
            <div class="wt-label">Max Drawdown</div>
            <div class="wt-value small negative">{money(summary['max_dd'])}</div>
            <div class="wt-meta">Recovery Factor <strong>{num(summary['recovery'], 2)}</strong><br>Profit Factor <strong>{num(summary['profit_factor'], 2)}</strong></div>
          </div>
          <div class="wt-card">
            <div class="wt-label">Trade Quality</div>
            <div class="wt-value small">{pct(summary['winrate'], 1)}</div>
            <div class="wt-meta">Ø Trade <strong>{money(summary['avg_trade'])}</strong><br>Payoff Ratio <strong>{num(summary['payoff'], 2)}</strong></div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_kpis(summary: Dict[str, Any]) -> None:
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Gross Profit", money(summary["gross_profit"]))
    c2.metric("Gross Loss", money(summary["gross_loss"]))
    c3.metric("Ø Gewinner", money(summary["avg_win"]))
    c4.metric("Ø Verlierer", money(summary["avg_loss"]))
    c5.metric("Bester Trade", money(summary["best"]))
    c6.metric("Schlechtester Trade", money(summary["worst"]))


def _base_layout(title: str = "") -> Dict[str, Any]:
    return dict(
        title=title,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT, family="Arial, sans-serif", size=12),
        margin=dict(l=18, r=18, t=38 if title else 12, b=18),
        hoverlabel=dict(bgcolor=PANEL_2, bordercolor=BORDER, font_color=TEXT),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=MUTED)),
    )


def make_equity_drawdown_chart(trades: pd.DataFrame) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        row_heights=[0.72, 0.28],
    )
    if trades.empty:
        return fig

    x = trades["DateTime"] if trades["DateTime"].notna().any() else trades["TradeNo"]
    fig.add_trace(
        go.Scatter(
            x=x,
            y=trades["Equity"],
            mode="lines",
            name="Realized P/L",
            line=dict(color=ACCENT, width=2.4),
            fill="tozeroy",
            fillcolor="rgba(106,167,255,0.06)",
            hovertemplate="%{x|%d.%m.%Y %H:%M}<br>Realized P/L: %{y:,.2f} $<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=trades["Drawdown"],
            mode="lines",
            name="Drawdown",
            line=dict(color=NEGATIVE, width=1.8),
            fill="tozeroy",
            fillcolor="rgba(255,95,114,0.12)",
            hovertemplate="%{x|%d.%m.%Y %H:%M}<br>Drawdown: %{y:,.2f} $<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig.add_hline(y=0, line_width=1, line_color=BORDER, row=1, col=1)
    fig.add_hline(y=0, line_width=1, line_color=BORDER, row=2, col=1)

    layout = _base_layout()
    layout.update(height=520, hovermode="x unified", showlegend=True)
    fig.update_layout(**layout)
    fig.update_yaxes(title_text="P/L ($)", gridcolor=GRID, zeroline=False, row=1, col=1)
    fig.update_yaxes(title_text="DD ($)", gridcolor=GRID, zeroline=False, row=2, col=1)
    fig.update_xaxes(gridcolor=GRID, showgrid=False, tickformat="%d.%m\n%H:%M", row=2, col=1)
    return fig


def make_daily_bar(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty or not trades["DateTime"].notna().any():
        return fig
    daily = trades.dropna(subset=["DateTime"]).copy()
    daily["DayDate"] = daily["DateTime"].dt.date
    daily = daily.groupby("DayDate", dropna=False)["PNL_Currency"].sum().reset_index()
    daily["DayLabel"] = pd.to_datetime(daily["DayDate"]).dt.strftime("%d.%m.%Y")
    colors = [POSITIVE if x >= 0 else NEGATIVE for x in daily["PNL_Currency"]]
    fig.add_trace(
        go.Bar(
            x=daily["DayLabel"],
            y=daily["PNL_Currency"],
            marker_color=colors,
            hovertemplate="%{x}<br>P/L: %{y:,.2f} $<extra></extra>",
            name="Tages P/L",
        )
    )
    layout = _base_layout()
    layout.update(height=300, showlegend=False)
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=False, tickangle=-20, type="category", title="Tag")
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=BORDER, title="P/L ($)")
    return fig


def make_algo_pnl_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty:
        return fig
    view = (
        trades.groupby("Algo", dropna=False)["PNL_Currency"]
        .sum()
        .sort_values()
        .reset_index()
    )
    colors = [POSITIVE if x >= 0 else NEGATIVE for x in view["PNL_Currency"]]
    fig.add_trace(
        go.Bar(
            x=view["PNL_Currency"],
            y=view["Algo"],
            orientation="h",
            marker_color=colors,
            hovertemplate="%{y}<br>P/L: %{x:,.2f} $<extra></extra>",
            name="Algo P/L",
        )
    )
    layout = _base_layout()
    layout.update(height=max(300, 52 * len(view) + 90), showlegend=False)
    fig.update_layout(**layout)
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=BORDER, title="Realized P/L ($)")
    fig.update_yaxes(showgrid=False, automargin=True)
    return fig


def _algo_path_frame(group: pd.DataFrame, value_kind: str, global_start: Any, global_end: Any) -> Tuple[list, list]:
    """Build a step-like algo path that stays visible across the selected time window."""
    g = group.sort_values(["DateTime", "TradeID"], na_position="last").copy()
    pnl = pd.to_numeric(g["PNL_Currency"], errors="coerce").fillna(0.0)
    equity = pnl.cumsum()
    if value_kind == "drawdown":
        high = equity.cummax().clip(lower=0.0)
        values = equity - high
    else:
        values = equity

    if g["DateTime"].notna().any():
        valid = g["DateTime"].notna()
        x = g.loc[valid, "DateTime"].tolist()
        y = values.loc[valid].astype(float).tolist()
        if not x:
            return [], []
        start = global_start if pd.notna(global_start) else x[0]
        end = global_end if pd.notna(global_end) else x[-1]
        # Start every algo at zero and carry its latest value to the end of the
        # selected window. This also makes one-trade algos visible as a line.
        append_end = bool(pd.notna(end) and end != x[-1])
        x = [start] + x + ([end] if append_end else [])
        y = [0.0] + y + ([y[-1]] if append_end else [])
        return x, y

    # Fallback for foreign exports without timestamps.
    y = values.astype(float).tolist()
    return list(range(0, len(y) + 1)), [0.0] + y


def make_multi_algo_pnl_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty:
        return fig

    cmap = algo_color_map(trades)
    algos = sorted(cmap, key=natural_algo_sort_key)
    valid_times = trades["DateTime"].dropna() if "DateTime" in trades else pd.Series(dtype="datetime64[ns]")
    global_start = valid_times.min() if len(valid_times) else pd.NaT
    global_end = valid_times.max() if len(valid_times) else pd.NaT

    for algo in algos:
        g = trades[trades["Algo"] == algo]
        x, y = _algo_path_frame(g, "equity", global_start, global_end)
        if not x:
            continue
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines+markers",
                name=algo,
                line=dict(color=cmap[algo], width=2.7, shape="hv"),
                marker=dict(color=cmap[algo], size=4, line=dict(width=0)),
                hovertemplate=f"<b>{html.escape(algo)}</b><br>%{{x|%d.%m.%Y %H:%M}}<br>Cum. P/L: %{{y:,.2f}} $<extra></extra>",
                connectgaps=False,
            )
        )

    layout = _base_layout()
    layout.update(
        height=max(460, min(700, 390 + 20 * len(algos))),
        hovermode="x unified",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
        margin=dict(l=55, r=30, t=80, b=55),
    )
    fig.update_layout(**layout)
    fig.add_hline(y=0, line_width=1.2, line_color=BORDER)
    fig.update_xaxes(showgrid=False, tickformat="%d.%m\n%H:%M", title="Zeit", rangeslider_visible=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, title="Kumuliertes P/L ($)")
    return fig


def make_multi_algo_drawdown_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty:
        return fig

    cmap = algo_color_map(trades)
    algos = sorted(cmap, key=natural_algo_sort_key)
    valid_times = trades["DateTime"].dropna() if "DateTime" in trades else pd.Series(dtype="datetime64[ns]")
    global_start = valid_times.min() if len(valid_times) else pd.NaT
    global_end = valid_times.max() if len(valid_times) else pd.NaT

    for algo in algos:
        g = trades[trades["Algo"] == algo]
        x, y = _algo_path_frame(g, "drawdown", global_start, global_end)
        if not x:
            continue
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                mode="lines+markers",
                name=algo,
                line=dict(color=cmap[algo], width=2.7, shape="hv"),
                marker=dict(color=cmap[algo], size=4, line=dict(width=0)),
                hovertemplate=f"<b>{html.escape(algo)}</b><br>%{{x|%d.%m.%Y %H:%M}}<br>Drawdown: %{{y:,.2f}} $<extra></extra>",
                connectgaps=False,
            )
        )

    layout = _base_layout()
    layout.update(
        height=max(460, min(700, 390 + 20 * len(algos))),
        hovermode="x unified",
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(size=11)),
        margin=dict(l=55, r=30, t=80, b=55),
    )
    fig.update_layout(**layout)
    fig.add_hline(y=0, line_width=1.2, line_color=BORDER)
    fig.update_xaxes(showgrid=False, tickformat="%d.%m\n%H:%M", title="Zeit", rangeslider_visible=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False, title="Drawdown ($)")
    return fig


def risk_reward_table(trades: pd.DataFrame) -> pd.DataFrame:
    """Per-algo planned Risk:Reward based on exported RiskTicks and RewardTicks."""
    if trades.empty:
        return pd.DataFrame()

    rows = []
    for algo, g in trades.groupby("Algo", dropna=False):
        risk = pd.to_numeric(g["RiskTicks"], errors="coerce")
        reward = pd.to_numeric(g["RewardTicks"], errors="coerce")
        valid = risk.gt(0) & reward.gt(0)
        vrisk = risk[valid]
        vreward = reward[valid]
        avg_risk = float(vrisk.mean()) if len(vrisk) else float("nan")
        avg_reward = float(vreward.mean()) if len(vreward) else float("nan")
        per_trade_rr = (vreward / vrisk).replace([float("inf"), float("-inf")], pd.NA).dropna()
        rr = float(per_trade_rr.mean()) if len(per_trade_rr) else float("nan")

        pnl = pd.to_numeric(g["PNL_Currency"], errors="coerce").fillna(0.0)
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        avg_win = float(wins.mean()) if len(wins) else float("nan")
        avg_loss = float(losses.mean()) if len(losses) else float("nan")
        realized = avg_win / abs(avg_loss) if pd.notna(avg_win) and pd.notna(avg_loss) and avg_loss < 0 else float("nan")

        rows.append({
            "Algo": algo,
            "Trades": int(len(g)),
            "R:R Samples": int(valid.sum()),
            "Ø Risk (Ticks)": avg_risk,
            "Ø Reward (Ticks)": avg_reward,
            "Ø Risk : Reward": f"1 : {rr:.2f}" if pd.notna(rr) else "–",
            "Realized Payoff": realized,
        })

    out = pd.DataFrame(rows)
    out["_sort"] = out["Algo"].map(natural_algo_sort_key)
    out = out.sort_values("_sort").drop(columns="_sort").reset_index(drop=True)
    return out


def style_risk_reward_table(df: pd.DataFrame):
    if df.empty:
        return df
    styled = df.style.format({
        "Ø Risk (Ticks)": lambda x: "–" if pd.isna(x) else f"{x:,.1f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Ø Reward (Ticks)": lambda x: "–" if pd.isna(x) else f"{x:,.1f}".replace(",", "X").replace(".", ",").replace("X", "."),
        "Realized Payoff": lambda x: "–" if pd.isna(x) else f"{x:.2f}",
    })
    return styled


def make_direction_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty:
        return fig
    view = trades.groupby("Direction", dropna=False)["PNL_Currency"].sum().reset_index()
    colors = [POSITIVE if x >= 0 else NEGATIVE for x in view["PNL_Currency"]]
    fig.add_trace(go.Bar(x=view["Direction"], y=view["PNL_Currency"], marker_color=colors))
    layout = _base_layout()
    layout.update(height=300, showlegend=False)
    fig.update_layout(**layout)
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=BORDER, title="P/L ($)")
    return fig


def make_exit_reason_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty:
        return fig
    view = (
        trades.groupby("ExitReason", dropna=False)["PNL_Currency"]
        .sum()
        .sort_values()
        .reset_index()
    )
    colors = [POSITIVE if x >= 0 else NEGATIVE for x in view["PNL_Currency"]]
    fig.add_trace(
        go.Bar(
            x=view["PNL_Currency"],
            y=view["ExitReason"],
            orientation="h",
            marker_color=colors,
            hovertemplate="%{y}<br>P/L: %{x:,.2f} $<extra></extra>",
        )
    )
    layout = _base_layout()
    layout.update(height=max(300, 42 * len(view) + 100), showlegend=False)
    fig.update_layout(**layout)
    fig.update_xaxes(gridcolor=GRID, zerolinecolor=BORDER, title="P/L ($)")
    fig.update_yaxes(showgrid=False, automargin=True)
    return fig


def make_mae_mfe_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty:
        return fig
    colors = [POSITIVE if p >= 0 else NEGATIVE for p in trades["PNL_Currency"]]
    fig.add_trace(
        go.Scatter(
            x=trades["MAE_Ticks"],
            y=trades["MFE_Ticks"],
            mode="markers",
            marker=dict(size=9, color=colors, line=dict(width=1, color=BORDER), opacity=0.85),
            text=trades["Algo"],
            customdata=trades[["PNL_Currency", "Direction"]].to_numpy(),
            hovertemplate=(
                "%{text}<br>MAE: %{x:.0f} T<br>MFE: %{y:.0f} T"
                "<br>P/L: %{customdata[0]:,.2f} $<br>%{customdata[1]}<extra></extra>"
            ),
        )
    )
    layout = _base_layout()
    layout.update(height=390, showlegend=False)
    fig.update_layout(**layout)
    fig.update_xaxes(gridcolor=GRID, title="MAE (Ticks)")
    fig.update_yaxes(gridcolor=GRID, title="MFE (Ticks)")
    return fig


def _group_curve_stats(group: pd.DataFrame) -> Tuple[float, float]:
    if group.empty:
        return 0.0, 0.0
    pnl = group.sort_values(["DateTime", "TradeID"])["PNL_Currency"]
    equity = pnl.cumsum()
    high = equity.cummax().clip(lower=0.0)
    dd = equity - high
    return float(equity.iloc[-1]), float(dd.min())


def strategy_table(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    for algo, g in trades.groupby("Algo", dropna=False):
        pnl = g["PNL_Currency"]
        gp = float(pnl[pnl > 0].sum())
        gl = float(pnl[pnl < 0].sum())
        pf = gp / abs(gl) if gl < 0 else (float("inf") if gp > 0 else 0.0)
        net, dd = _group_curve_stats(g)
        versions = sorted({v for v in g.get("AlgoVersion", pd.Series(dtype=str)).astype(str).tolist() if v.strip()})
        modules = sorted({v for v in g.get("Module", pd.Series(dtype=str)).astype(str).tolist() if v.strip() and v != "Nicht angegeben"})
        rows.append(
            {
                "Algo": algo,
                "Trades": len(g),
                "Net P/L": net,
                "Profit Factor": pf,
                "Winrate %": float((pnl > 0).mean() * 100),
                "Ø Trade": float(pnl.mean()),
                "Max DD": dd,
                "Best": float(pnl.max()),
                "Worst": float(pnl.min()),
                "First Trade": g["DateTime"].min(),
                "Last Trade": g["DateTime"].max(),
                "Versions": ", ".join(versions) if versions else "–",
                "Modules": ", ".join(modules[:6]) + (" …" if len(modules) > 6 else "") if modules else "–",
            }
        )
    return pd.DataFrame(rows).sort_values(["Net P/L", "Trades"], ascending=[False, False]).reset_index(drop=True)


def group_table(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    if trades.empty or by not in trades:
        return pd.DataFrame()
    rows = []
    for key, g in trades.groupby(by, dropna=False):
        pnl = g["PNL_Currency"]
        gp = float(pnl[pnl > 0].sum())
        gl = float(pnl[pnl < 0].sum())
        pf = gp / abs(gl) if gl < 0 else (float("inf") if gp > 0 else 0.0)
        _, dd = _group_curve_stats(g)
        rows.append(
            {
                by: key,
                "Trades": len(g),
                "Net P/L": float(pnl.sum()),
                "Ø Trade": float(pnl.mean()),
                "Winrate %": float((pnl > 0).mean() * 100),
                "Profit Factor": pf,
                "Max DD": dd,
            }
        )
    return pd.DataFrame(rows).sort_values(by, ascending=False).reset_index(drop=True)


def style_performance_table(df: pd.DataFrame):
    if df.empty:
        return df
    formatters: Dict[str, Any] = {}
    for c in ["Net P/L", "Ø Trade", "Max DD", "Best", "Worst"]:
        if c in df.columns:
            formatters[c] = money
    for c in ["Profit Factor"]:
        if c in df.columns:
            formatters[c] = lambda x: num(float(x), 2)
    for c in ["Winrate %"]:
        if c in df.columns:
            formatters[c] = lambda x: pct(float(x), 1)
    for c in ["First Trade", "Last Trade"]:
        if c in df.columns:
            formatters[c] = lambda x: x.strftime("%d.%m.%Y %H:%M") if pd.notna(x) else "–"

    styled = df.style.format(formatters)
    if "Net P/L" in df.columns:
        styled = styled.map(
            lambda v: f"color: {POSITIVE}; font-weight: 650" if isinstance(v, (int, float)) and v > 0
            else (f"color: {NEGATIVE}; font-weight: 650" if isinstance(v, (int, float)) and v < 0 else ""),
            subset=["Net P/L"],
        )
    return styled


def natural_algo_sort_key(value: str):
    """Natural sort so Sim2 is ordered before Sim10."""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(value))]


def algo_color_map(trades: pd.DataFrame) -> Dict[str, str]:
    """Stable color per algo; filtering other algos does not change its color."""
    algos = sorted(
        trades.get("Algo", pd.Series(dtype=str)).dropna().astype(str).unique().tolist(),
        key=natural_algo_sort_key,
    )
    colors: Dict[str, str] = {}
    for algo in algos:
        sim_match = re.fullmatch(r"\s*sim\s*(\d+)\s*", algo, flags=re.IGNORECASE)
        if sim_match:
            idx = max(0, int(sim_match.group(1)) - 1)
        else:
            idx = sum((i + 1) * ord(ch) for i, ch in enumerate(algo))
        colors[algo] = ALGO_COLORS[idx % len(ALGO_COLORS)]
    return colors


def sidebar_filters(trades: pd.DataFrame, session_view: str = DEFAULT_SESSION_VIEW) -> Tuple[pd.DataFrame, float]:
    """Apply dashboard filters without stale widget state across sessions/files.

    The dashboard keeps the same UI and filter functionality. The only additional
    behavior is an internal reset when the underlying CSV dataset changes.

    This is necessary because Streamlit persists widget values in session_state.
    If a previous CSV/date range contained only (for example) 21.08.2026, then
    uploading a new CSV with 04.08–21.08 could otherwise leave the old 21.08
    date filter active and make the dashboard appear to contain only one trade.
    """
    st.sidebar.markdown("### Portfolio Filter")
    st.sidebar.caption("Alle Filter wirken identisch auf KPI, Tabellen und Equity-Kurve.")

    # ------------------------------------------------------------------
    # Internal dataset fingerprint.
    # No dashboard functionality/layout changes: this only detects when a new
    # file/history has been loaded so old widget selections do not leak into it.
    # ------------------------------------------------------------------
    if trades.empty:
        dataset_signature = "EMPTY"
    else:
        dt = pd.to_datetime(trades.get("DateTime", pd.Series(dtype="datetime64[ns]")), errors="coerce")
        accounts = sorted(
            trades.get("TradeAccount", pd.Series(dtype=str))
            .fillna("").astype(str).unique().tolist(),
            key=natural_algo_sort_key,
        )
        trade_ids = trades.get("TradeID", pd.Series(dtype=str)).fillna("").astype(str)
        pnl_ticks = pd.to_numeric(
            trades.get("PNL_Ticks", pd.Series(dtype=float)), errors="coerce"
        ).fillna(0.0)

        fingerprint_source = "|".join([
            str(len(trades)),
            str(dt.min()) if dt.notna().any() else "",
            str(dt.max()) if dt.notna().any() else "",
            ",".join(accounts),
            str(len(set(trade_ids.tolist()))),
            f"{float(pnl_ticks.sum()):.8f}",
        ])
        dataset_signature = hashlib.sha1(
            fingerprint_source.encode("utf-8", errors="replace")
        ).hexdigest()

    previous_signature = st.session_state.get("_wt_filter_dataset_signature")
    if previous_signature != dataset_signature:
        # Remove only dashboard filter-widget state. Password/authentication,
        # display contract and all other dashboard/session functionality remain.
        keys_to_clear = []
        for key in list(st.session_state.keys()):
            if (
                key.startswith("algorithms_")
                or key.startswith("date_range_")
                or key.startswith("filter_globex_")
                or key.startswith("filter_rth_")
                or key.startswith("filter_eth_")
            ):
                keys_to_clear.append(key)

        for key in keys_to_clear:
            st.session_state.pop(key, None)

        st.session_state["_wt_filter_dataset_signature"] = dataset_signature

    risk_default = normalize_number(_secret("RISK_LIMIT_TICKS", "15")) or 15.0
    risk_limit_ticks = st.sidebar.number_input(
        "Globales Risk-Limit (Ticks)",
        min_value=0.0,
        max_value=10000.0,
        value=float(risk_default),
        step=1.0,
        help="Nur für den Risk-Monitor. Die Strategie-Logik in Sierra Chart wird dadurch nicht verändert.",
        key="global_risk_limit_ticks",
    )

    out = trades.copy()

    # Apply the selected global session first.
    if session_view in {"RTH", "ETH"} and "TradeSession" in out.columns:
        out = out[out["TradeSession"] == session_view]

    # Each session keeps independent user-selected filters, as before.
    session_key = str(session_view).lower()

    algos = sorted(out["Algo"].dropna().astype(str).unique().tolist(), key=natural_algo_sort_key)
    selected_algos = st.sidebar.multiselect(
        "Algorithmen",
        algos,
        default=algos,
        key=f"algorithms_{session_key}",
    )
    if selected_algos and len(selected_algos) != len(algos):
        out = out[out["Algo"].isin(selected_algos)]
    elif not selected_algos and algos:
        out = out.iloc[0:0]

    if "DateTime" in out and out["DateTime"].notna().any():
        min_date = out["DateTime"].min().date()
        max_date = out["DateTime"].max().date()

        date_key = f"date_range_{session_key}"

        # Defensive clamp for a persisted value from an older history. Usually
        # the dataset reset above already clears it, but this also protects
        # against browser/session edge cases.
        old_range = st.session_state.get(date_key)
        if old_range is not None:
            try:
                values = list(old_range) if isinstance(old_range, (tuple, list)) else [old_range]
                invalid = any(v < min_date or v > max_date for v in values if v is not None)
                if invalid:
                    st.session_state.pop(date_key, None)
            except Exception:
                st.session_state.pop(date_key, None)

        dr = st.sidebar.date_input(
            "Zeitraum",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
            key=date_key,
        )
        if isinstance(dr, tuple) and len(dr) == 2:
            start_date, end_date = dr
            out = out[
                (out["DateTime"].dt.date >= start_date)
                & (out["DateTime"].dt.date <= end_date)
            ]

    with st.sidebar.expander("Technische Filter", expanded=False):
        for col, label in [
            ("TradeAccount", "Trade Account"),
            ("Symbol", "Symbol"),
            ("Direction", "Richtung"),
            ("CountColor", "CountColor / Legacy Tag"),
            ("Module", "Modul"),
        ]:
            options = sorted(out[col].dropna().astype(str).unique().tolist()) if col in out else []
            selected = st.multiselect(
                label,
                options,
                default=options,
                key=f"filter_{session_key}_{col}",
            )
            if selected and len(selected) != len(options):
                out = out[out[col].isin(selected)]
            elif not selected and options:
                out = out.iloc[0:0]

    return recompute_curve(out, risk_limit_ticks), float(risk_limit_ticks)


def load_data() -> Tuple[pd.DataFrame, Dict[str, Any], str]:
    gh = get_github_config()
    uploaded = st.sidebar.file_uploader("CSV manuell testen", type=["csv", "txt"])
    if uploaded is not None:
        uploaded_text = uploaded.getvalue().decode("utf-8-sig", errors="replace")
        return (
            parse_csv(uploaded_text),
            {"source": "upload", "path": uploaded.name, "loaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            uploaded_text,
        )

    if gh.owner and gh.repo:
        content, info = fetch_from_github(gh.owner, gh.repo, gh.branch, gh.data_path, gh.token)
        return parse_csv(content), info, content

    content, info = read_local_csv(LOCAL_CSV)
    return parse_csv(content), info, content


def source_status(info: Dict[str, Any], raw_df: pd.DataFrame, trades: pd.DataFrame) -> None:
    source = clean_label(info.get("source", ""), "–")
    path = clean_label(info.get("path", ""), "–")
    loaded = clean_label(info.get("loaded_at", ""), "–")
    latest = latest_trade_label(trades)
    

def executive_tab(filtered: pd.DataFrame, summary: Dict[str, Any]) -> None:
    st.markdown('<div class="wt-section-title">Portfolio Performance</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Realized closed-trade P/L. Chart und KPI basieren auf exakt derselben Filtermenge.</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(make_equity_drawdown_chart(filtered), use_container_width=True, config={"displayModeBar": False}, key="executive_equity_drawdown")

    c1, c2 = st.columns([1.05, 1], gap="large")
    with c1:
        st.markdown('<div class="wt-section-title">Tages P/L</div>', unsafe_allow_html=True)
        st.plotly_chart(make_daily_bar(filtered), use_container_width=True, config={"displayModeBar": False}, key="executive_daily_pnl")
    with c2:
        st.markdown('<div class="wt-section-title">P/L Contribution nach Algo</div>', unsafe_allow_html=True)
        st.plotly_chart(make_algo_pnl_chart(filtered), use_container_width=True, config={"displayModeBar": False}, key="executive_algo_contribution")

    st.markdown('<div class="wt-section-title">Strategy Leaderboard</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Das Algo-Universum wird aus den Daten entdeckt und ist nicht im Code fest verdrahtet.</div>',
        unsafe_allow_html=True,
    )
    s = strategy_table(filtered)
    if s.empty:
        st.info("Keine Strategien in der aktuellen Filterung.")
    else:
        st.dataframe(
            style_performance_table(s[["Algo", "Trades", "Net P/L", "Profit Factor", "Winrate %", "Ø Trade", "Max DD", "Last Trade"]]),
            use_container_width=True,
            hide_index=True,
            height=min(480, 70 + 36 * len(s)),
        )


def algos_tab(filtered: pd.DataFrame) -> None:
    st.markdown('<div class="wt-section-title">Algorithmus-Universum</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Neue Strategien erscheinen automatisch, sobald ihre geschlossenen Trades in der CSV enthalten sind. Historische Strategien bleiben für Rückblicke erhalten.</div>',
        unsafe_allow_html=True,
    )
    table = strategy_table(filtered)
    if table.empty:
        st.info("Keine Algos in der aktuellen Filterung.")
        return

    st.dataframe(style_performance_table(table), use_container_width=True, hide_index=True, height=min(650, 80 + 38 * len(table)))

    st.markdown('<div class="wt-section-title">Algo Contribution</div>', unsafe_allow_html=True)
    st.plotly_chart(make_algo_pnl_chart(filtered), use_container_width=True, config={"displayModeBar": False}, key="algos_algo_contribution")

    st.markdown('<div class="wt-section-title">Kumuliertes P/L je Algorithmus</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Jeder Algo besitzt eine feste Farbe. Die Step-Linien zeigen ausschließlich realisierte Closed-Trade-P/L und bleiben auch bei wenigen Trades klar sichtbar.</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        make_multi_algo_pnl_chart(filtered),
        use_container_width=True,
        config={"displayModeBar": False},
        key="algos_multi_equity",
    )

    st.markdown('<div class="wt-section-title">Drawdown je Algorithmus</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Drawdown wird für jeden Algo separat aus seiner eigenen kumulierten Equity-Kurve berechnet. Die Farben entsprechen exakt dem P/L-Chart.</div>',
        unsafe_allow_html=True,
    )
    st.plotly_chart(
        make_multi_algo_drawdown_chart(filtered),
        use_container_width=True,
        config={"displayModeBar": False},
        key="algos_multi_drawdown",
    )

    st.markdown('<div class="wt-section-title">Risk-to-Reward je Algorithmus</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Ø Risk : Reward basiert auf dem durchschnittlichen RewardTicks/RiskTicks-Verhältnis der gültigen Trades. Beispiel 1 : 3,00 bedeutet 1 Einheit geplantes Risiko zu 3 Einheiten geplantem Reward. Realized Payoff = Ø Gewinner / |Ø Verlierer|.</div>',
        unsafe_allow_html=True,
    )
    rr_table = risk_reward_table(filtered)
    st.dataframe(
        style_risk_reward_table(rr_table),
        use_container_width=True,
        hide_index=True,
        height=min(520, 80 + 38 * len(rr_table)),
    )

    algo = st.selectbox("Algo Detail", table["Algo"].tolist(), index=0)
    detail = recompute_curve(filtered[filtered["Algo"] == algo], risk_limit_ticks=10**9)
    sm = calc_summary(detail)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Net P/L", money(sm["net"]))
    c2.metric("Trades", sm["trades"])
    c3.metric("Profit Factor", num(sm["profit_factor"], 2))
    c4.metric("Winrate", pct(sm["winrate"], 1))
    c5.metric("Max DD", money(sm["max_dd"]))
    st.plotly_chart(make_equity_drawdown_chart(detail), use_container_width=True, config={"displayModeBar": False}, key="algos_detail_equity_drawdown")


def performance_tab(filtered: pd.DataFrame) -> None:
    c1, c2 = st.columns([1.15, 0.85], gap="large")
    with c1:
        st.markdown('<div class="wt-section-title">Perioden-Analyse</div>', unsafe_allow_html=True)
        level = st.radio("Aggregation", ["Day", "Week", "Month", "Year"], horizontal=True, label_visibility="collapsed")
        tbl = group_table(filtered, level)
        st.dataframe(style_performance_table(tbl), use_container_width=True, hide_index=True, height=450)
    with c2:
        st.markdown('<div class="wt-section-title">Long / Short Contribution</div>', unsafe_allow_html=True)
        st.plotly_chart(make_direction_chart(filtered), use_container_width=True, config={"displayModeBar": False}, key="performance_direction_contribution")

        st.markdown('<div class="wt-section-title">Streaks & Trading Days</div>', unsafe_allow_html=True)
        sm = calc_summary(filtered)
        a, b = st.columns(2)
        a.metric("Max Win Streak", sm["win_streak"])
        b.metric("Max Loss Streak", sm["loss_streak"])
        a.metric("Profitable Tage", pct(sm["profitable_days"], 1))
        b.metric("Handelstage", sm["days"])


def risk_tab(filtered: pd.DataFrame, risk_limit_ticks: float) -> None:
    st.markdown('<div class="wt-section-title">Risk & Trade Quality</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="wt-section-sub">Globaler Dashboard-Monitor: RiskTicks &gt; {num(risk_limit_ticks, 0)}. Dieser Wert ändert keine Sierra-Chart-Orderlogik und kann je nach Strategie bewusst abweichen.</div>',
        unsafe_allow_html=True,
    )

    violations = filtered[filtered["RiskViolation"] == True] if "RiskViolation" in filtered else pd.DataFrame()
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Risk-Verstöße", int(len(violations)))
    c2.metric("Max RiskTicks", num(float(filtered["RiskTicks"].max()) if len(filtered) else 0, 1))
    c3.metric("Ø RiskTicks", num(float(filtered["RiskTicks"].mean()) if len(filtered) else 0, 1))
    c4.metric("Ø MAE", num(float(filtered["MAE_Ticks"].mean()) if len(filtered) else 0, 1))
    c5.metric("Ø MFE", num(float(filtered["MFE_Ticks"].mean()) if len(filtered) else 0, 1))

    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        st.markdown('<div class="wt-section-title">MAE / MFE Map</div>', unsafe_allow_html=True)
        st.plotly_chart(make_mae_mfe_chart(filtered), use_container_width=True, config={"displayModeBar": False}, key="risk_mae_mfe")
    with c2:
        st.markdown('<div class="wt-section-title">P/L nach Exit Reason</div>', unsafe_allow_html=True)
        st.plotly_chart(make_exit_reason_chart(filtered), use_container_width=True, config={"displayModeBar": False}, key="risk_exit_reason")

    exit_stats = filtered.groupby("ExitReason", dropna=False).agg(
        Trades=("PNL_Currency", "size"),
        Net_PL=("PNL_Currency", "sum"),
        Avg_PL=("PNL_Currency", "mean"),
        Winrate=("Win", lambda s: float(s.mean() * 100) if len(s) else 0),
    ).reset_index().sort_values("Trades", ascending=False)
    exit_stats = exit_stats.rename(columns={"Net_PL": "Net P/L", "Avg_PL": "Ø Trade", "Winrate": "Winrate %"})
    st.dataframe(style_performance_table(exit_stats), use_container_width=True, hide_index=True)

    if len(violations):
        with st.expander(f"{len(violations)} Risk-Monitor Treffer anzeigen", expanded=False):
            show = [c for c in ["DateTime", "EntryDateTime", "SessionDateTime", "TradeSession", "Algo", "Module", "TradeAccount", "Direction", "RiskTicks", "PNL_Currency", "ExitReason", "TradeID"] if c in violations.columns]
            st.dataframe(violations[show].sort_values("DateTime", ascending=False), use_container_width=True, hide_index=True)


def require_trades_audit_access() -> bool:
    """Gate the Trades & Audit area behind the same fixed password.

    Authentication is remembered only for the current Streamlit browser session.
    This is intentionally separate from the Data Quality unlock state, so each
    protected tab can be locked/unlocked independently.
    """
    auth_key = "trades_audit_auth_ok"

    if st.session_state.get(auth_key) is True:
        c1, c2 = st.columns([5, 1])
        with c1:
            st.success("Trades & Audit ist entsperrt.")
        with c2:
            if st.button("Bereich sperren", key="lock_trades_audit", use_container_width=True):
                st.session_state[auth_key] = False
                st.rerun()
        return True

    st.markdown(
        '<div class="wt-section-title">🔒 Trades & Audit</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="wt-section-sub">Dieser Bereich ist geschützt. Bitte Passwort eingeben, '
        'um den vollständigen Trade Audit Trail und die technischen Detailfelder anzuzeigen.</div>',
        unsafe_allow_html=True,
    )

    with st.form("trades_audit_login_form", clear_on_submit=True):
        entered = st.text_input(
            "Passwort",
            type="password",
            placeholder="Passwort eingeben",
            key="trades_audit_password_input",
        )
        submitted = st.form_submit_button(
            "Trades & Audit entsperren",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if entered == DATA_QUALITY_PASSWORD:
            st.session_state[auth_key] = True
            st.rerun()
        else:
            st.error("Passwort falsch.")

    return False


def trades_tab(filtered: pd.DataFrame) -> None:
    if not require_trades_audit_access():
        return

    st.markdown('<div class="wt-section-title">Trade Audit Trail</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Nur geschlossene Trades. Zusätzlich werden Originalwerte vor der Dashboard-Korrektur '
        'und die tatsächlich verwendeten Werte nach der Korrektur getrennt dargestellt.</div>',
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------------
    # Tabelle 1: dieselben gefilterten Closed Trades mit den unveränderten
    # Dollar-P/L-Werten aus der BlueBlack-Quelldatei.
    # ------------------------------------------------------------------
    source_view = filtered.copy()
    source_view["PNL vor Korrektur"] = pd.to_numeric(
        source_view.get("PNL_Currency_Source", 0.0), errors="coerce"
    ).fillna(0.0)

    source_cols = [
        "CSVLine", "EntryCSVLine", "DateTime", "EntryDateTime", "SessionDateTime",
        "TradeSession", "TradeID", "Algo", "AlgoVersion", "Module", "TradeAccount",
        "Symbol", "CountColor", "Direction", "EntryFillPrice", "ExitPrice",
        "StopPrice", "TargetPrice", "RiskTicks", "RewardTicks", "Quantity",
        "PNL_Ticks", "PNL vor Korrektur", "MAE_Ticks", "MFE_Ticks",
        "ExitReason", "Notes",
    ]
    source_cols = [c for c in source_cols if c in source_view.columns]

    st.markdown(
        '<div class="wt-section-title">Alle Trades · Originalwerte ohne Dashboard-Korrektur</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="wt-section-sub">Diese Tabelle zeigt das Dollar-P/L so, wie es in der BlueBlack-Quelle exportiert wurde. '
        'Es findet in dieser Ansicht keine ES/MES-P/L-Korrektur statt.</div>',
        unsafe_allow_html=True,
    )
    st.dataframe(
        source_view[source_cols].sort_values("DateTime", ascending=False),
        use_container_width=True,
        hide_index=True,
        height=520,
    )

    # ------------------------------------------------------------------
    # Tabelle 2: vor/nach Korrektur. Die Performance-Basis des Dashboards
    # bleibt unverändert: PNL_Ticks × gewählte ES/MES-Tickbasis.
    # Nur tatsächlich veränderte Zeilen werden farblich hervorgehoben.
    # ------------------------------------------------------------------
    corrected_view = filtered.copy()
    corrected_view["PNL vor Korrektur"] = pd.to_numeric(
        corrected_view.get("PNL_Currency_Source", 0.0), errors="coerce"
    ).fillna(0.0)
    corrected_view["PNL nach Korrektur"] = pd.to_numeric(
        corrected_view.get("PNL_Currency", 0.0), errors="coerce"
    ).fillna(0.0)
    corrected_view["Korrektur Δ"] = (
        corrected_view["PNL nach Korrektur"] - corrected_view["PNL vor Korrektur"]
    )
    corrected_view["_corrected"] = corrected_view["Korrektur Δ"].abs() > 0.005

    def correction_reason(row: pd.Series) -> str:
        if not bool(row.get("_corrected", False)):
            return "Keine Änderung"

        ticks = normalize_number(row.get("PNL_Ticks", 0.0))
        source_pnl = normalize_number(row.get("PNL vor Korrektur", 0.0))
        source_contract = clean_label(row.get("SourceContract", ""), "UNKNOWN").upper()
        display_contract = clean_label(row.get("DisplayContract", ""), DEFAULT_DISPLAY_CONTRACT).upper()

        source_tick_value = CONTRACT_TICK_VALUES.get(source_contract)
        source_mismatch = False
        if ticks != 0 and source_tick_value is not None:
            expected_source = ticks * float(source_tick_value)
            source_mismatch = abs(source_pnl - expected_source) > 0.01

        if ticks == 0 and source_pnl != 0:
            return "Fallback: PNL_Ticks = 0"
        if source_mismatch and source_contract != display_contract:
            return "P/L-Quellabweichung + ES/MES-Normalisierung"
        if source_mismatch:
            return "P/L-Quellabweichung korrigiert"
        if source_contract != display_contract:
            return f"ES/MES-Normalisierung auf {display_contract}"
        return "Dashboard-P/L aus PNL_Ticks neu berechnet"

    corrected_view["Korrekturstatus"] = corrected_view["_corrected"].map(
        {True: "KORRIGIERT", False: "UNVERÄNDERT"}
    )
    corrected_view["Korrekturgrund"] = corrected_view.apply(correction_reason, axis=1)

    corrected_cols = [
        "CSVLine", "EntryCSVLine", "DateTime", "EntryDateTime", "SessionDateTime",
        "TradeSession", "TradeID", "Algo", "AlgoVersion", "Module", "TradeAccount",
        "Symbol", "CountColor", "Direction", "EntryFillPrice", "ExitPrice",
        "StopPrice", "TargetPrice", "RiskTicks", "RewardTicks", "Quantity",
        "PNL_Ticks", "PNL vor Korrektur", "PNL nach Korrektur", "Korrektur Δ",
        "Korrekturstatus", "Korrekturgrund", "SourceContract", "DisplayContract",
        "DisplayTickValue", "MAE_Ticks", "MFE_Ticks", "ExitReason", "Notes",
    ]
    corrected_cols = [c for c in corrected_cols if c in corrected_view.columns]

    corrected_display = corrected_view[corrected_cols + ["_corrected"]].sort_values(
        "DateTime", ascending=False
    )

    def highlight_corrected_rows(row: pd.Series):
        changed = bool(row.get("_corrected", False))
        if changed:
            return [
                "background-color: #3b3218; color: #ffd86b; font-weight: 600;"
                if col != "_corrected" else ""
                for col in row.index
            ]
        return ["" for _ in row.index]

    styled_corrected = (
        corrected_display.style
        .apply(highlight_corrected_rows, axis=1)
        .format({
            "PNL vor Korrektur": money,
            "PNL nach Korrektur": money,
            "Korrektur Δ": money,
        })
        .hide(axis="columns", subset=["_corrected"])
    )

    st.markdown(
        '<div class="wt-section-title">Alle Trades · Dashboard-Werte mit Korrektur</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="wt-section-sub">Vorher/Nachher-Vergleich. Gelb hervorgehobene Zeilen wurden für die Dashboard-Berechnung verändert. '
        'Unveränderte Trades bleiben ohne Hervorhebung.</div>',
        unsafe_allow_html=True,
    )
    st.dataframe(
        styled_corrected,
        use_container_width=True,
        hide_index=True,
        height=620,
    )

    # Bestehender vollständiger technischer Audit bleibt unverändert erhalten.
    st.markdown(
        '<div class="wt-section-title">Technischer Trade Audit Trail</div>',
        unsafe_allow_html=True,
    )
    cols = [
        "CSVLine", "EntryCSVLine", "DateTime", "EntryDateTime", "SessionDateTime", "TradeSession", "TradeID", "Algo", "AlgoVersion", "Module", "TradeAccount", "Symbol", "CountColor", "Direction",
        "EntryFillPrice", "ExitPrice", "StopPrice", "TargetPrice", "RiskTicks", "RewardTicks", "Quantity",
        "PNL_Ticks", "PNL_Currency", "PNL_Currency_Source", "PNL_Adjustment_Source", "ExportTickValue",
        "SourceContract", "DisplayContract", "DisplayTickValue",
        "MAE_Ticks", "MFE_Ticks", "ExitReason", "Equity", "Drawdown", "Notes",
    ]
    show_cols = [c for c in cols if c in filtered.columns]
    st.dataframe(
        filtered[show_cols].sort_values("DateTime", ascending=False),
        use_container_width=True,
        hide_index=True,
        height=620,
    )

    csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "Gefilterte Closed Trades als CSV herunterladen",
        csv_bytes,
        file_name="WT_Quant_Systems_filtered_closed_trades.csv",
        mime="text/csv",
    )



def _quality_money(value: Any) -> str:
    try:
        if pd.isna(value):
            return "–"
        return money(float(value))
    except Exception:
        return "–"


def _quality_float(value: Any, decimals: int = 2) -> str:
    try:
        if pd.isna(value):
            return "–"
        return num(float(value), decimals)
    except Exception:
        return "–"


def build_data_quality_report(raw_df: pd.DataFrame) -> Dict[str, Any]:
    """Audit the unmodified BlueBlack source and retain exact CSV line references.

    The audit deliberately runs on raw rows *before* EXIT deduplication so the
    operator can see exactly which Sim/account and source line caused an issue.

    Severity model:
    - FEHLER: source inconsistency that should be corrected in the exporter/file.
    - WARNUNG: plausible small monetary residual or unknown/ambiguous metadata.
    - INFO: operationally valid pattern worth showing for transparency.
    - OK: no hard issue detected for that Sim in the checks below.

    Important Sierra behavior:
    Multiple ENTRY and EXIT rows can be fully valid because an algo may place
    and fill more than one limit order at the same level. Therefore repeated-looking
    ENTRY/EXIT core fields are informational only and NEVER lower the Data Quality
    status by themselves. The dashboard keeps every closed EXIT in performance.

    Only a future source record with a truly unique execution/order identifier
    proving that two rows are the same physical execution may be auto-deduplicated.
    """
    empty = {
        "summary": pd.DataFrame(),
        "issues": pd.DataFrame(),
        "duplicate_entries": pd.DataFrame(),
        "duplicate_exits": pd.DataFrame(),
        "pnl_checks": pd.DataFrame(),
        "missing_exits": pd.DataFrame(),
        "missing_entries": pd.DataFrame(),
        "structural": pd.DataFrame(),
        "metrics": {
            "source_rows": 0, "entries": 0, "exits": 0,
            "duplicate_entry_groups": 0, "duplicate_entry_extra_rows": 0,
            "duplicate_exit_groups": 0, "duplicate_exit_extra_rows": 0,
            "pnl_errors": 0, "pnl_warnings": 0,
            "missing_exit": 0, "missing_entry": 0, "structural_errors": 0,
            "status": "NO DATA",
        },
    }
    if raw_df.empty:
        return empty

    df = raw_df.copy()
    if "CSVLine" not in df.columns:
        df["CSVLine"] = range(2, len(df) + 2)
    df["CSVLine"] = pd.to_numeric(df["CSVLine"], errors="coerce").fillna(0).astype(int)

    if "RowType" not in df.columns:
        df["RowType"] = "EXIT"
    df["_RowType"] = safe_col(df, "RowType", "").astype(str).str.upper().str.strip()
    df["_Account"] = safe_col(df, "TradeAccount", "").astype(str).str.strip()
    df["_TradeID"] = safe_col(df, "TradeID", "").astype(str).str.strip()
    df["_Symbol"] = safe_col(df, "Symbol", "").astype(str).str.strip()
    df["_DateTimeParsed"] = pd.to_datetime(safe_col(df, "DateTime", ""), errors="coerce")
    df["_PNL_Ticks"] = pd.to_numeric(safe_col(df, "PNL_Ticks", ""), errors="coerce")
    df["_PNL_Currency"] = pd.to_numeric(safe_col(df, "PNL_Currency", ""), errors="coerce")
    df["_ExitPrice"] = pd.to_numeric(safe_col(df, "ExitPrice", ""), errors="coerce")
    df["_EntryFillPrice"] = pd.to_numeric(safe_col(df, "EntryFillPrice", ""), errors="coerce")
    df["_Quantity"] = pd.to_numeric(safe_col(df, "Quantity", ""), errors="coerce")

    entry_types = ["ENTRY", "OPEN", "TRADE_ENTRY", "B"]
    exit_types = ["EXIT", "CLOSE", "CLOSED", "TRADE_EXIT", "E"]
    entries = df[df["_RowType"].isin(entry_types)].copy()
    exits = df[df["_RowType"].isin(exit_types)].copy()

    issue_rows = []
    duplicate_entry_rows = []
    duplicate_exit_rows = []
    pnl_rows = []
    missing_exit_rows = []
    missing_entry_rows = []
    structural_rows = []

    def account_label(value: Any) -> str:
        return clean_label(value, "Ohne Account")

    def add_issue(
        severity: str,
        category: str,
        sim: str,
        csv_lines: str,
        trade_id: str = "",
        symbol: str = "",
        dt: str = "",
        details: str = "",
    ) -> None:
        issue_rows.append({
            "Schweregrad": severity,
            "Kategorie": category,
            "Sim / Account": sim,
            "CSV-Zeile(n)": csv_lines,
            "TradeID": trade_id,
            "Symbol": symbol,
            "DateTime": dt,
            "Details": details,
        })

    # ------------------------------------------------------------------
    # 1) Same-looking ENTRY / EXIT groups (INFO only; possible Multi-Limit execution)
    # ------------------------------------------------------------------
    entry_dup_key = [
        "_Account", "_TradeID", "DateTime", "Symbol", "Direction",
        "EntryFillPrice", "Quantity",
    ]
    exit_dup_key = [
        "_Account", "_TradeID", "DateTime", "Symbol", "Direction", "Quantity",
        "ExitReason", "PNL_Ticks", "PNL_Currency", "ExitPrice",
    ]

    def duplicate_groups(frame: pd.DataFrame, key_cols: list, kind: str) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame()
        work = frame.copy()
        for col in key_cols:
            if col not in work.columns:
                work[col] = ""
        mask = work.duplicated(key_cols, keep=False)
        if not mask.any():
            return pd.DataFrame()

        rows = []
        for _, group in work[mask].groupby(key_cols, dropna=False, sort=False):
            group = group.sort_values("CSVLine", kind="stable")
            lines = [int(x) for x in group["CSVLine"].tolist()]
            sim = account_label(group["_Account"].iloc[0])
            trade_id = clean_label(group["_TradeID"].iloc[0], "")
            symbol = clean_label(group["_Symbol"].iloc[0], "")
            dt = clean_label(group.get("DateTime", pd.Series([""])).iloc[0], "")
            notes_len = safe_col(group, "Notes", "").astype(str).str.len()
            keep_idx = group.assign(__notes_len=notes_len).sort_values(
                ["__notes_len", "CSVLine"], kind="stable"
            ).index[-1]
            keep_line = int(group.loc[keep_idx, "CSVLine"])
            removed = [line for line in lines if line != keep_line]

            row = {
                "Sim / Account": sim,
                "TradeID": trade_id,
                "Symbol": symbol,
                "DateTime": dt,
                "CSV-Zeilen": ", ".join(map(str, lines)),
                "Anzahl": int(len(group)),
            }
            if kind == "EXIT":
                row.update({
                    "PNL_Ticks": normalize_number(group.get("PNL_Ticks", pd.Series([0])).iloc[0]),
                    "PNL_Currency_Source": normalize_number(group.get("PNL_Currency", pd.Series([0])).iloc[0]),
                    "ExitPrice": normalize_number(group.get("ExitPrice", pd.Series([0])).iloc[0]),
                    "Bewertung": "INFO – zulässiges Multi-Exit / Multi-Limit-Muster",
                    "Dashboard-Korrektur": "KEINE – alle EXIT-Zeilen bleiben als eigene geschlossene Trades erhalten",
                })
                add_issue(
                    "INFO",
                    "Mehrfach-EXIT (zulässig)",
                    sim,
                    ", ".join(map(str, lines)),
                    trade_id,
                    symbol,
                    dt,
                    f"{len(group)} EXIT-Zeilen besitzen gleiche Kernfelder. Das ist kein Fehler: "
                    "mehrere Limit-Orders können am selben Level ausgeführt und später mit identischen "
                    "Exitdaten geschlossen werden. Keine Zeile wird entfernt; alle Trades bleiben im P/L.",
                )
            else:
                row.update({
                    "EntryFillPrice": normalize_number(group.get("EntryFillPrice", pd.Series([0])).iloc[0]),
                    "Quantity": normalize_number(group.get("Quantity", pd.Series([0])).iloc[0]),
                    "Bewertung": "INFO – zulässiges Multi-Entry / Multi-Limit-Muster",
                })
                add_issue(
                    "INFO",
                    "Mehrfach-ENTRY (zulässig)",
                    sim,
                    ", ".join(map(str, lines)),
                    trade_id,
                    symbol,
                    dt,
                    f"{len(group)} ENTRY-Zeilen besitzen gleiche Kernfelder. Das ist kein Fehler: "
                    "mehrere Limits können am selben Level liegen bzw. gefüllt werden. "
                    "Die Zeilen werden im Audit nur zur Transparenz angezeigt und nicht als Data-Quality-Fehler gewertet.",
                )
            rows.append(row)
        return pd.DataFrame(rows)

    duplicate_entries = duplicate_groups(entries, entry_dup_key, "ENTRY")
    duplicate_exits = duplicate_groups(exits, exit_dup_key, "EXIT")
    duplicate_entry_rows = duplicate_entries.to_dict("records") if not duplicate_entries.empty else []
    duplicate_exit_rows = duplicate_exits.to_dict("records") if not duplicate_exits.empty else []

    # ------------------------------------------------------------------
    # 2) ENTRY without EXIT / EXIT without ENTRY
    # Pairing is account + TradeID, because TradeID alone is not global.
    # ------------------------------------------------------------------
    valid_entries = entries[(entries["_Account"] != "") & (entries["_TradeID"] != "")].copy()
    valid_exits = exits[(exits["_Account"] != "") & (exits["_TradeID"] != "")].copy()

    entry_keys = set(zip(valid_entries["_Account"], valid_entries["_TradeID"]))
    exit_keys = set(zip(valid_exits["_Account"], valid_exits["_TradeID"]))

    # Sierra Trade Activity Log -> Trades is a one-row-per-closed-trade format.
    # Its Entry DateTime is embedded in the EXIT/closed row, so separate
    # ENTRY/EXIT pairing checks are not applicable to that source schema.
    self_contained_closed_source = bool(
        "SourceFormat" in df.columns
        and len(df) > 0
        and df["SourceFormat"].astype(str).eq("SIERRA_TRADE_ACTIVITY_TRADES").all()
    )

    if not self_contained_closed_source:
        for account, trade_id in sorted(entry_keys - exit_keys, key=lambda x: (natural_algo_sort_key(x[0]), x[1])):
            g = valid_entries[(valid_entries["_Account"] == account) & (valid_entries["_TradeID"] == trade_id)]
            lines = ", ".join(map(str, sorted(g["CSVLine"].astype(int).tolist())))
            row = {
                "Sim / Account": account_label(account),
                "TradeID": trade_id,
                "ENTRY CSV-Zeile(n)": lines,
                "ENTRY DateTime": clean_label(g.get("DateTime", pd.Series([""])).iloc[0], ""),
                "Symbol": clean_label(g["_Symbol"].iloc[0], ""),
                "Fehler": "ENTRY vorhanden, EXIT fehlt",
            }
            missing_exit_rows.append(row)
            add_issue(
                "FEHLER", "ENTRY ohne EXIT", account_label(account), lines, trade_id,
                row["Symbol"], row["ENTRY DateTime"],
                "Trade wurde eröffnet/exportiert, aber für dieselbe Kombination aus TradeAccount + TradeID existiert kein EXIT.",
            )

        for account, trade_id in sorted(exit_keys - entry_keys, key=lambda x: (natural_algo_sort_key(x[0]), x[1])):
            g = valid_exits[(valid_exits["_Account"] == account) & (valid_exits["_TradeID"] == trade_id)]
            lines = ", ".join(map(str, sorted(g["CSVLine"].astype(int).tolist())))
            row = {
                "Sim / Account": account_label(account),
                "TradeID": trade_id,
                "EXIT CSV-Zeile(n)": lines,
                "EXIT DateTime": clean_label(g.get("DateTime", pd.Series([""])).iloc[0], ""),
                "Symbol": clean_label(g["_Symbol"].iloc[0], ""),
                "Fehler": "EXIT vorhanden, ENTRY fehlt",
            }
            missing_entry_rows.append(row)
            add_issue(
                "FEHLER", "EXIT ohne ENTRY", account_label(account), lines, trade_id,
                row["Symbol"], row["EXIT DateTime"],
                "Geschlossener Trade wurde exportiert, aber für dieselbe Kombination aus TradeAccount + TradeID existiert keine ENTRY-Zeile.",
            )

    # ------------------------------------------------------------------
    # 3) P/L plausibility: source dollars vs symbol contract tick value.
    # The dashboard performance still uses canonical PNL_Ticks, but source
    # inconsistencies are surfaced here rather than silently trusted.
    # ------------------------------------------------------------------
    for _, row in exits.iterrows():
        sim = account_label(row["_Account"])
        trade_id = clean_label(row["_TradeID"], "")
        symbol = clean_label(row["_Symbol"], "")
        csv_line = int(row["CSVLine"])
        dt = clean_label(row.get("DateTime", ""), "")
        ticks = row["_PNL_Ticks"]
        source_pnl = row["_PNL_Currency"]
        source_contract = detect_contract_from_symbol(symbol)
        expected_tick = CONTRACT_TICK_VALUES.get(source_contract)

        if pd.isna(ticks) or pd.isna(source_pnl):
            continue

        if float(ticks) == 0.0 and abs(float(source_pnl)) > 0.01:
            pnl_row = {
                "Schweregrad": "FEHLER",
                "Sim / Account": sim,
                "CSV-Zeile": csv_line,
                "TradeID": trade_id,
                "Symbol": symbol,
                "Kontrakt laut Symbol": source_contract,
                "PNL_Ticks": float(ticks),
                "PNL_Currency_Source": float(source_pnl),
                "Erwartet laut Symbol": 0.0,
                "Implizit $/Tick": pd.NA,
                "Abweichung $": float(source_pnl),
                "Diagnose": "Dollar-P/L ungleich 0 bei PNL_Ticks = 0.",
                "Dashboard MES P/L": float(source_pnl) if source_contract == "UNKNOWN" else 0.0,
                "Dashboard ES P/L": float(source_pnl) if source_contract == "UNKNOWN" else 0.0,
                "Dashboard-Korrektur": "FALLBACK – PNL_Ticks ist 0; Quellwert bleibt nur in diesem Sonderfall Basis",
            }
            pnl_rows.append(pnl_row)
            add_issue("FEHLER", "P/L-Plausibilität", sim, str(csv_line), trade_id, symbol, dt, pnl_row["Diagnose"])
            continue

        if float(ticks) == 0.0 or expected_tick is None:
            continue

        expected_pnl = float(ticks) * float(expected_tick)
        diff = float(source_pnl) - expected_pnl
        implied_tick = abs(float(source_pnl) / float(ticks))
        rel_error = abs(implied_tick - float(expected_tick)) / float(expected_tick)

        # Require both a material tick-value discrepancy and a material dollar
        # difference. This avoids treating small fixed fees/rounding deltas as a
        # 10x contract-basis error.
        severe_abs_threshold = max(2.0, 2.0 * float(expected_tick))
        if rel_error > 0.20 and abs(diff) >= severe_abs_threshold:
            severity = "FEHLER"
            diagnosis = (
                f"{source_contract}-Symbol erwartet {expected_tick:.2f} $/Tick, "
                f"der Export impliziert {implied_tick:.2f} $/Tick."
            )
        elif abs(diff) > 0.01:
            severity = "WARNUNG"
            diagnosis = (
                f"Kleine Dollarabweichung zur {source_contract}-Tickbasis ({diff:+.2f} $). "
                "Mögliche Gebühr/Rundung oder Quellkorrektur."
            )
        else:
            continue

        pnl_row = {
            "Schweregrad": severity,
            "Sim / Account": sim,
            "CSV-Zeile": csv_line,
            "TradeID": trade_id,
            "Symbol": symbol,
            "Kontrakt laut Symbol": source_contract,
            "PNL_Ticks": float(ticks),
            "PNL_Currency_Source": float(source_pnl),
            "Erwartet laut Symbol": expected_pnl,
            "Implizit $/Tick": implied_tick,
            "Abweichung $": diff,
            "Diagnose": diagnosis,
            "Dashboard MES P/L": float(ticks) * CONTRACT_TICK_VALUES["MES"],
            "Dashboard ES P/L": float(ticks) * CONTRACT_TICK_VALUES["ES"],
            "Dashboard-Korrektur": "AUTO-KORRIGIERT – Source-Dollarwert wird nicht für Performance verwendet; PNL_Ticks × gewählte Tickbasis",
        }
        pnl_rows.append(pnl_row)
        add_issue(severity, "P/L-Plausibilität", sim, str(csv_line), trade_id, symbol, dt, diagnosis)

    # ------------------------------------------------------------------
    # 4) Structural / parse checks
    # ------------------------------------------------------------------
    for _, row in df.iterrows():
        sim = account_label(row["_Account"])
        line = int(row["CSVLine"])
        trade_id = clean_label(row["_TradeID"], "")
        symbol = clean_label(row["_Symbol"], "")
        dt = clean_label(row.get("DateTime", ""), "")
        problems = []

        if row["_Account"] == "":
            problems.append("TradeAccount/Sim fehlt")
        if row["_TradeID"] == "":
            problems.append("TradeID fehlt")
        if row["_Symbol"] == "":
            problems.append("Symbol fehlt")
        elif detect_contract_from_symbol(row["_Symbol"]) == "UNKNOWN":
            problems.append("Symbol kann nicht als ES/MES erkannt werden")
        if pd.isna(row["_DateTimeParsed"]):
            problems.append("DateTime ungültig/leer")
        if row["_RowType"] not in entry_types + exit_types:
            problems.append(f"Unbekannter RowType: {clean_label(row['_RowType'], 'leer')}")

        if problems:
            detail = "; ".join(problems)
            structural_rows.append({
                "Sim / Account": sim,
                "CSV-Zeile": line,
                "RowType": clean_label(row["_RowType"], "–"),
                "TradeID": trade_id,
                "Symbol": symbol,
                "DateTime": dt,
                "Fehler": detail,
            })
            add_issue("FEHLER", "Struktur / Format", sim, str(line), trade_id, symbol, dt, detail)

    duplicate_entries_df = pd.DataFrame(duplicate_entry_rows)
    duplicate_exits_df = pd.DataFrame(duplicate_exit_rows)
    pnl_df = pd.DataFrame(pnl_rows)
    missing_exits_df = pd.DataFrame(missing_exit_rows)
    missing_entries_df = pd.DataFrame(missing_entry_rows)
    structural_df = pd.DataFrame(structural_rows)
    issues_df = pd.DataFrame(issue_rows)

    # ------------------------------------------------------------------
    # Dashboard correction ledger. Source errors remain visible, but known
    # failure modes are prevented from corrupting dashboard performance.
    # ------------------------------------------------------------------
    if not issues_df.empty:
        def correction_for_issue(row: pd.Series) -> pd.Series:
            category = clean_label(row.get("Kategorie", ""), "")
            severity = clean_label(row.get("Schweregrad", ""), "")
            if category == "Mehrfach-EXIT (zulässig)":
                return pd.Series({
                    "Korrekturstatus": "KEINE KORREKTUR NÖTIG",
                    "Dashboard-Maßnahme": "Nur Info. Alle EXIT-Zeilen bleiben als eigenständige Closed Trades in Tradezahl, P/L, PF, Equity und Drawdown enthalten.",
                    "Quellfix erforderlich": "NEIN",
                })
            if category == "P/L-Plausibilität":
                return pd.Series({
                    "Korrekturstatus": "AUTO-KORRIGIERT",
                    "Dashboard-Maßnahme": "PNL_Currency_Source bleibt Auditwert; Performance wird aus PNL_Ticks × gewählter ES/MES-Tickbasis berechnet.",
                    "Quellfix erforderlich": "JA" if severity == "FEHLER" else "PRÜFEN",
                })
            if category == "ENTRY ohne EXIT":
                return pd.Series({
                    "Korrekturstatus": "SICHER BEHANDELT",
                    "Dashboard-Maßnahme": "ENTRY wird nicht als geschlossener Trade/P&L gewertet. Es wird kein EXIT oder Gewinn/Verlust erfunden.",
                    "Quellfix erforderlich": "JA",
                })
            if category == "EXIT ohne ENTRY":
                return pd.Series({
                    "Korrekturstatus": "SICHER BEHANDELT",
                    "Dashboard-Maßnahme": "Closed EXIT bleibt anhand PNL_Ticks im P/L; Session nutzt SignalDateTime und danach Exit-DateTime als Fallback.",
                    "Quellfix erforderlich": "JA",
                })
            if category == "Struktur / Format":
                return pd.Series({
                    "Korrekturstatus": "SICHER BEHANDELT",
                    "Dashboard-Maßnahme": "Fehlende Daten werden nicht erfunden; verfügbare Felder/Fallbacks werden genutzt, nicht verwertbare Zeilen fließen nicht falsch ein.",
                    "Quellfix erforderlich": "JA",
                })
            if category == "Mehrfach-ENTRY (zulässig)":
                return pd.Series({
                    "Korrekturstatus": "KEINE KORREKTUR NÖTIG",
                    "Dashboard-Maßnahme": "Nur Info. Multi-Limit/Multi-Entry bleibt vollständig erhalten.",
                    "Quellfix erforderlich": "NEIN",
                })
            return pd.Series({
                "Korrekturstatus": "GEPRÜFT",
                "Dashboard-Maßnahme": "Auffälligkeit wird angezeigt; keine unsichere automatische Veränderung.",
                "Quellfix erforderlich": "PRÜFEN",
            })

        corrections = issues_df.apply(correction_for_issue, axis=1)
        issues_df = pd.concat([issues_df, corrections], axis=1)

    # ------------------------------------------------------------------
    # 5) Per-Sim control summary. Include clean Sims as explicit OK rows.
    # ------------------------------------------------------------------
    all_accounts = sorted(
        {account_label(x) for x in df["_Account"].tolist()},
        key=natural_algo_sort_key,
    )
    summary_rows = []
    for sim in all_accounts:
        dup_entry_groups = int((duplicate_entries_df.get("Sim / Account", pd.Series(dtype=str)) == sim).sum()) if not duplicate_entries_df.empty else 0
        dup_exit_groups = int((duplicate_exits_df.get("Sim / Account", pd.Series(dtype=str)) == sim).sum()) if not duplicate_exits_df.empty else 0
        pnl_errors = int(((pnl_df.get("Sim / Account", pd.Series(dtype=str)) == sim) & (pnl_df.get("Schweregrad", pd.Series(dtype=str)) == "FEHLER")).sum()) if not pnl_df.empty else 0
        pnl_warnings = int(((pnl_df.get("Sim / Account", pd.Series(dtype=str)) == sim) & (pnl_df.get("Schweregrad", pd.Series(dtype=str)) == "WARNUNG")).sum()) if not pnl_df.empty else 0
        entry_no_exit = int((missing_exits_df.get("Sim / Account", pd.Series(dtype=str)) == sim).sum()) if not missing_exits_df.empty else 0
        exit_no_entry = int((missing_entries_df.get("Sim / Account", pd.Series(dtype=str)) == sim).sum()) if not missing_entries_df.empty else 0
        struct = int((structural_df.get("Sim / Account", pd.Series(dtype=str)) == sim).sum()) if not structural_df.empty else 0

        # Mehrfach-ENTRY ist in diesem Sierra-Setup zulässig (z. B. mehrere Limits
        # am selben Level) und darf den Qualitätsstatus NICHT verschlechtern.
        # Mehrfach-ENTRY und Mehrfach-EXIT sind in diesem Multi-Limit-Setup
        # zulässig und beeinflussen den Data-Quality-Status nicht.
        hard = pnl_errors + entry_no_exit + exit_no_entry + struct
        if hard > 0:
            status = "FEHLER"
        elif pnl_warnings > 0:
            status = "WARNUNG"
        else:
            status = "OK"

        if hard == 0 and pnl_warnings == 0:
            dashboard_status = "OK"
        elif entry_no_exit + exit_no_entry + struct > 0:
            dashboard_status = "SICHER BEHANDELT"
        else:
            dashboard_status = "AUTO-KORRIGIERT"

        summary_rows.append({
            "Sim / Account": sim,
            "Quellstatus": status,
            "Dashboard-Status": dashboard_status,
            "Mehrfach-ENTRY (Info)": dup_entry_groups,
            "Mehrfach-EXIT (Info)": dup_exit_groups,
            "P/L Fehler": pnl_errors,
            "P/L Warnungen": pnl_warnings,
            "ENTRY ohne EXIT": entry_no_exit,
            "EXIT ohne ENTRY": exit_no_entry,
            "Strukturfehler": struct,
            "Fehler gesamt": hard,
        })

    summary_df = pd.DataFrame(summary_rows)

    dup_entry_extra = int((duplicate_entries_df["Anzahl"] - 1).sum()) if not duplicate_entries_df.empty else 0
    dup_exit_extra = int((duplicate_exits_df["Anzahl"] - 1).sum()) if not duplicate_exits_df.empty else 0
    pnl_errors_total = int((pnl_df["Schweregrad"] == "FEHLER").sum()) if not pnl_df.empty else 0
    pnl_warnings_total = int((pnl_df["Schweregrad"] == "WARNUNG").sum()) if not pnl_df.empty else 0
    structural_total = int(len(structural_df))

    # Multiple ENTRY rows are informational only. They can represent valid
    # multi-limit / multi-entry execution and therefore are excluded from the
    # hard Data Quality error count.
    # Repeated-looking ENTRY/EXIT groups are informational only and can be
    # legitimate multi-limit executions. They are excluded from hard errors.
    hard_total = (
        pnl_errors_total
        + len(missing_exits_df) + len(missing_entries_df) + structural_total
    )
    if hard_total > 0:
        status = "FEHLER"
    elif pnl_warnings_total > 0:
        status = "WARNUNG"
    else:
        status = "OK"

    # Only actual P/L source inconsistencies are auto-corrected here.
    # Multi-ENTRY / Multi-EXIT rows are never removed or altered.
    auto_corrected = int(len(pnl_df))
    safely_handled = int(len(missing_exits_df) + len(missing_entries_df) + structural_total)
    dashboard_status = "OK" if (auto_corrected + safely_handled) == 0 else (
        "BEREINIGT" if safely_handled == 0 else "BEREINIGT / QUELLFIX NÖTIG"
    )

    metrics = {
        "source_rows": int(len(df)),
        "entries": int(len(entries)),
        "exits": int(len(exits)),
        "duplicate_entry_groups": int(len(duplicate_entries_df)),
        "duplicate_entry_extra_rows": dup_entry_extra,
        "duplicate_exit_groups": int(len(duplicate_exits_df)),
        "duplicate_exit_extra_rows": dup_exit_extra,
        "pnl_errors": pnl_errors_total,
        "pnl_warnings": pnl_warnings_total,
        "missing_exit": int(len(missing_exits_df)),
        "missing_entry": int(len(missing_entries_df)),
        "structural_errors": structural_total,
        "status": status,
        "dashboard_status": dashboard_status,
        "auto_corrected": auto_corrected,
        "safely_handled": safely_handled,
    }

    return {
        "summary": summary_df,
        "issues": issues_df,
        "duplicate_entries": duplicate_entries_df,
        "duplicate_exits": duplicate_exits_df,
        "pnl_checks": pnl_df,
        "missing_exits": missing_exits_df,
        "missing_entries": missing_entries_df,
        "structural": structural_df,
        "metrics": metrics,
    }


def style_quality_summary(df: pd.DataFrame):
    if df.empty:
        return df
    styled = df.style
    if "Quellstatus" in df.columns:
        styled = styled.map(
            lambda v: (
                f"color: {NEGATIVE}; font-weight: 700" if v == "FEHLER"
                else (f"color: {WARNING}; font-weight: 700" if v == "WARNUNG"
                      else f"color: {POSITIVE}; font-weight: 700")
            ),
            subset=["Quellstatus"],
        )
    if "Dashboard-Status" in df.columns:
        styled = styled.map(
            lambda v: f"color: {POSITIVE}; font-weight: 700" if v in {"OK", "AUTO-KORRIGIERT", "SICHER BEHANDELT"} else "",
            subset=["Dashboard-Status"],
        )
    return styled


def style_pnl_quality_table(df: pd.DataFrame):
    if df.empty:
        return df
    formatters = {
        "PNL_Ticks": lambda x: _quality_float(x, 2),
        "PNL_Currency_Source": _quality_money,
        "Erwartet laut Symbol": _quality_money,
        "Implizit $/Tick": lambda x: _quality_float(x, 2),
        "Abweichung $": _quality_money,
        "Dashboard MES P/L": _quality_money,
        "Dashboard ES P/L": _quality_money,
    }
    styled = df.style.format({k: v for k, v in formatters.items() if k in df.columns})
    if "Schweregrad" in df.columns:
        styled = styled.map(
            lambda v: f"color: {NEGATIVE}; font-weight: 700" if v == "FEHLER"
            else (f"color: {WARNING}; font-weight: 700" if v == "WARNUNG" else ""),
            subset=["Schweregrad"],
        )
    return styled


def require_data_quality_access() -> bool:
    """Gate the Data Quality & Korrektur area behind its own password.

    Authentication is remembered only in the current Streamlit browser session.
    The user can explicitly lock the area again at any time.
    """
    auth_key = "data_quality_auth_ok"

    if st.session_state.get(auth_key) is True:
        c1, c2 = st.columns([5, 1])
        with c1:
            st.success("Data Quality & Korrektur ist entsperrt.")
        with c2:
            if st.button("Bereich sperren", key="lock_data_quality", use_container_width=True):
                st.session_state[auth_key] = False
                st.rerun()
        return True

    st.markdown(
        '<div class="wt-section-title">🔒 Data Quality & Korrektur</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="wt-section-sub">Dieser Bereich ist geschützt. Bitte Passwort eingeben, um die detaillierten '
        'BlueBlack-Prüfungen, Sim-Fehler und CSV-Zeilen anzuzeigen.</div>',
        unsafe_allow_html=True,
    )

    with st.form("data_quality_login_form", clear_on_submit=True):
        entered = st.text_input(
            "Passwort",
            type="password",
            placeholder="Passwort eingeben",
            key="data_quality_password_input",
        )
        submitted = st.form_submit_button(
            "Data Quality entsperren",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        if entered == DATA_QUALITY_PASSWORD:
            st.session_state[auth_key] = True
            st.rerun()
        else:
            st.error("Passwort falsch.")

    return False


def data_quality_tab(raw_df: pd.DataFrame) -> None:
    if not require_data_quality_access():
        return
    report = build_data_quality_report(raw_df)
    metrics = report["metrics"]

    st.markdown('<div class="wt-section-title">Data Quality & Auto-Correction · BlueBlack</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Die Prüfung läuft direkt auf der Quelldatei vor Bereinigung und Performance-Berechnung. '
        'CSV-Zeile 1 ist die Kopfzeile; Datenzeilen beginnen bei Zeile 2. Mehrere ENTRY- und EXIT-Zeilen können bei Multi-Limit-/Multi-Entry-Setups '
        'korrekt sein und werden deshalb nur als INFO gezeigt. Sie werden nicht automatisch entfernt und beeinflussen den Qualitätsstatus nicht.</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    status = metrics.get("status", "NO DATA")
    c1.metric("Quellstatus", status)
    c2.metric("Dashboard", metrics.get("dashboard_status", "–"))
    c3.metric("Auto-korrigiert", metrics.get("auto_corrected", 0), help="Automatisch korrigierte P/L-Quellabweichungen. Mehrfach-ENTRY/EXIT werden nicht verändert.")
    c4.metric("Mehrfach-EXIT", f"{metrics.get('duplicate_exit_groups', 0)} Gruppen", delta=f"{metrics.get('duplicate_exit_extra_rows', 0)} zusätzliche Zeilen", delta_color="off", help="Nur Info: mögliche legitime Multi-Limit-Ausführungen; keine Zeile wird entfernt.")
    c5.metric("P/L Quellfehler", metrics.get("pnl_errors", 0))
    c6.metric("Quellfix/Fallback", metrics.get("safely_handled", 0))

    if status == "OK":
        st.success("Keine der aktuell geprüften Dateninkonsistenzen wurde gefunden.")
    elif status == "WARNUNG":
        st.warning("Keine harten Datenfehler erkannt, aber mindestens eine Quellwarnung sollte geprüft werden.")
    else:
        st.error(
            "Die BlueBlack-QUELLE enthält Fehler/Auffälligkeiten. Das Dashboard schützt seine Berechnung davor: "
            "P/L-Abweichungen werden aus PNL_Ticks neu bewertet. Mehrfach-ENTRYs und Mehrfach-EXITs bleiben vollständig erhalten, "
            "weil sie legitime Multi-Limit-Ausführungen sein können. Nicht rekonstruierbare Quelldaten werden nicht erfunden und bleiben als Quellfix sichtbar."
        )

    st.markdown('<div class="wt-section-title">Fehlerübersicht nach Sim</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Hier siehst du sofort, welches Sim betroffen ist und welche Fehlerart dort vorkommt. '
        'Mehrfach-ENTRY und Mehrfach-EXIT werden als Info mitgezählt, beeinflussen den Status aber nicht. '
        'Sims ohne harte Treffer bleiben bewusst mit Status OK sichtbar.</div>',
        unsafe_allow_html=True,
    )
    summary = report["summary"]
    if summary.empty:
        st.info("Keine Sim-/Account-Daten gefunden.")
    else:
        st.dataframe(style_quality_summary(summary), use_container_width=True, hide_index=True, height=min(560, 80 + 38 * len(summary)))

    st.markdown('<div class="wt-section-title">Dashboard-Korrekturprotokoll</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Jeder erkannte Punkt zeigt nicht nur den Quellfehler, sondern auch exakt, wie das Dashboard ihn behandelt. '
        'Die BlueBlack-Datei selbst wird niemals stillschweigend verändert.</div>',
        unsafe_allow_html=True,
    )
    issues = report["issues"]
    if issues.empty:
        st.success("Keine Korrekturen nötig.")
    else:
        ledger_cols = [c for c in [
            "Schweregrad", "Kategorie", "Sim / Account", "CSV-Zeile(n)", "TradeID", "Symbol",
            "Korrekturstatus", "Dashboard-Maßnahme", "Quellfix erforderlich", "Details"
        ] if c in issues.columns]
        st.dataframe(issues[ledger_cols], use_container_width=True, hide_index=True, height=min(760, 110 + 34 * len(issues)))

    st.markdown('<div class="wt-section-title">Alle Auffälligkeiten mit CSV-Zeile</div>', unsafe_allow_html=True)
    if issues.empty:
        st.success("Keine Auffälligkeiten.")
    else:
        severity_order = {"FEHLER": 0, "WARNUNG": 1, "INFO": 2}
        issue_view = issues.copy()
        issue_view["_order"] = issue_view["Schweregrad"].map(severity_order).fillna(9)
        issue_view = issue_view.sort_values(["_order", "Sim / Account", "Kategorie", "CSV-Zeile(n)"]).drop(columns="_order")
        st.dataframe(issue_view, use_container_width=True, hide_index=True, height=min(700, 100 + 34 * len(issue_view)))

    st.markdown('<div class="wt-section-title">Multi-Limit / Mehrfach-Trade-Struktur</div>', unsafe_allow_html=True)
    d1, d2 = st.columns(2, gap="large")
    with d1:
        st.markdown("**Mehrfach-ENTRY (Info · zulässig)**")
        st.caption(
            "Mehrere ENTRY-Zeilen mit gleichen Kernfeldern sind nicht automatisch ein Fehler. "
            "In deinem Setup können mehrere Limits am selben Level liegen bzw. gefüllt werden. "
            "Die Tabelle dient nur der Nachvollziehbarkeit."
        )
        dup_entry = report["duplicate_entries"]
        if dup_entry.empty:
            st.success("Keine Mehrfach-ENTRY-Gruppen erkannt.")
        else:
            st.dataframe(dup_entry, use_container_width=True, hide_index=True, height=min(420, 90 + 38 * len(dup_entry)))
    with d2:
        st.markdown("**Mehrfach-EXIT (Info · zulässig)**")
        st.caption(
            "Gleich aussehende EXIT-Zeilen sind kein Fehler. Zwei oder mehr Limit-Orders können am selben Level "
            "eingestiegen und später mit identischen Exitdaten geschlossen werden. Alle EXIT-Zeilen bleiben erhalten."
        )
        dup_exit = report["duplicate_exits"]
        if dup_exit.empty:
            st.success("Keine Mehrfach-EXIT-Gruppen mit gleichen Kernfeldern erkannt.")
        else:
            st.dataframe(dup_exit, use_container_width=True, hide_index=True, height=min(420, 90 + 38 * len(dup_exit)))

    st.markdown('<div class="wt-section-title">P/L-Plausibilität je Quellzeile</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Vergleich von PNL_Ticks und dem exportierten PNL_Currency mit der Tickbasis des Symbols. '
        'Beispiel: MES +7 Ticks müssen 8,75 $ entsprechen; 87,50 $ implizieren stattdessen 12,50 $/Tick und werden als Fehler markiert. '
        'Kleine Restabweichungen werden separat als Warnung ausgewiesen. In den Performance-KPIs wird der Quell-Dollarwert bei vorhandenen '
        'PNL_Ticks automatisch durch die kanonische Tick-Berechnung ersetzt; beide Werte bleiben im Audit sichtbar.</div>',
        unsafe_allow_html=True,
    )
    pnl_checks = report["pnl_checks"]
    if pnl_checks.empty:
        st.success("Keine P/L-Plausibilitätsabweichungen.")
    else:
        st.dataframe(
            style_pnl_quality_table(pnl_checks),
            use_container_width=True,
            hide_index=True,
            height=min(760, 110 + 34 * len(pnl_checks)),
        )

    st.markdown('<div class="wt-section-title">ENTRY / EXIT Vollständigkeit</div>', unsafe_allow_html=True)
    p1, p2 = st.columns(2, gap="large")
    with p1:
        st.markdown("**ENTRY vorhanden, EXIT fehlt**")
        miss_exit = report["missing_exits"]
        if miss_exit.empty:
            st.success("Keine fehlenden EXITs erkannt.")
        else:
            st.dataframe(miss_exit, use_container_width=True, hide_index=True, height=min(420, 90 + 38 * len(miss_exit)))
    with p2:
        st.markdown("**EXIT vorhanden, ENTRY fehlt**")
        miss_entry = report["missing_entries"]
        if miss_entry.empty:
            st.success("Keine EXITs ohne passende ENTRY-Zeile erkannt.")
        else:
            st.dataframe(miss_entry, use_container_width=True, hide_index=True, height=min(420, 90 + 38 * len(miss_entry)))

    st.markdown('<div class="wt-section-title">Struktur- und Formatfehler</div>', unsafe_allow_html=True)
    structural = report["structural"]
    if structural.empty:
        st.success("Keine fehlenden Kernfelder, ungültigen DateTimes oder unbekannten ES/MES-Symbole erkannt.")
    else:
        st.dataframe(structural, use_container_width=True, hide_index=True, height=min(520, 90 + 38 * len(structural)))

    # Downloadable audit report for fixing the exporter/source file.
    if not issues.empty:
        audit_csv = issues.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Data-Quality-Fehlerliste als CSV herunterladen",
            audit_csv,
            file_name="WT_Data_Quality_Audit.csv",
            mime="text/csv",
            key="download_data_quality_audit",
        )



def system_tab(raw_df: pd.DataFrame, trades: pd.DataFrame, filtered: pd.DataFrame, info: Dict[str, Any], session_view: str = DEFAULT_SESSION_VIEW) -> None:
    st.markdown('<div class="wt-section-title">Data & System Status</div>', unsafe_allow_html=True)
    source_status(info, raw_df, trades)

    st.markdown('<div class="wt-section-title">Dynamische Algo-Erkennung</div>', unsafe_allow_html=True)
    st.markdown(
        """
Das Dashboard hat **keine fest codierte Liste von Strategien**. Für diese Sierra-Chart-Daten gilt:

1. `TradeAccount` ist die **verbindliche Algo-ID** (`Sim1`, `Sim2`, …).
2. `Notes` liefert Strategie-/Versions-/Modul-Metadaten, ersetzt aber niemals eine vorhandene Sim-ID.
3. `CountColor` ist nur ein Legacy-/Kategorie-Tag (z. B. `MAE-200`, `Gelb`) und **kein Algorithmusname**.
4. Nur wenn `TradeAccount` fehlt, werden explizite Algo-Felder, `Notes` und zuletzt `CountColor` als Fallback verwendet.

Damit erscheinen neue Sim-/Algo-Accounts automatisch und entfernte Accounts verschwinden automatisch, ohne eine fest codierte Liste.
"""
    )


    st.markdown('<div class="wt-section-title">Session-Zuordnung</div>', unsafe_allow_html=True)
    session_counts = trades.get("TradeSession", pd.Series(dtype=str)).value_counts().to_dict() if not trades.empty else {}
    st.markdown(
        f"""
Aktive Session-Ansicht: **{session_view}**. Standard beim Start ist **Globex**.

- **Globex** = RTH + ETH (keine Trades werden aufgrund der Session ausgeschlossen).
- **RTH** = tatsächlicher Trade-Entry von **08:30 bis vor 15:00 CT**.
- **ETH** = alle übrigen gültigen Globex-Zeiten außerhalb dieses RTH-Fensters.
- Für die Zuordnung wird primär der tatsächliche `EntryDateTime` aus der passenden ENTRY-Zeile verwendet. Falls dieser fehlt, folgen `SignalDateTime` und zuletzt `DateTime`.
- Falls ein zukünftiger Export explizit `RTH` oder `ETH` liefert, hat diese Quellangabe Vorrang.

Erkannt: **RTH {int(session_counts.get('RTH', 0))} · ETH {int(session_counts.get('ETH', 0))} · UNKNOWN {int(session_counts.get('UNKNOWN', 0))}**.
"""
    )

    st.markdown('<div class="wt-section-title">P/L Normalisierung ES / MES</div>', unsafe_allow_html=True)
    display_contract = DEFAULT_DISPLAY_CONTRACT
    if not trades.empty and "DisplayContract" in trades.columns:
        display_contract = clean_label(trades["DisplayContract"].iloc[0], DEFAULT_DISPLAY_CONTRACT).upper()
    tick_value = CONTRACT_TICK_VALUES.get(display_contract, CONTRACT_TICK_VALUES[DEFAULT_DISPLAY_CONTRACT])
    source_counts = trades.get("SourceContract", pd.Series(dtype=str)).value_counts().to_dict() if not trades.empty else {}
    st.markdown(
        f"""
Aktuelle Anzeige-Basis: **{display_contract} = {num(tick_value, 2)} $ pro Tick**. Standard beim Start ist **ES**.

- **PNL_Ticks ist die verbindliche Berechnungsbasis** für die Performance-Anzeige.
- MES-Darstellung = `PNL_Ticks × 1,25 $`.
- ES-Darstellung = `PNL_Ticks × 12,50 $`.
- Dadurch können gemischte oder falsch monetarisierte Quellzeilen die Portfolio-Kennzahlen nicht mehr verfälschen.
- Das ursprünglich exportierte Dollar-P/L bleibt unverändert in `PNL_Currency_Source`.
- `ExportTickValue` und `PNL_Adjustment_Source` dienen nur dem Audit der Quelldatei und werden **nicht** in das standardisierte Dashboard-P/L eingerechnet.
- Mehrere gleich aussehende ENTRY- oder EXIT-Zeilen werden **nicht** automatisch entfernt. Sie können echte Multi-Limit-/Multi-Entry-Trades sein und bleiben vollständig in den Kennzahlen enthalten.

Erkannte Quellkontrakte: **ES {int(source_counts.get('ES', 0))} · MES {int(source_counts.get('MES', 0))} · UNKNOWN {int(source_counts.get('UNKNOWN', 0))}**.
"""
    )

    st.markdown('<div class="wt-section-title">Datenintegrität</div>', unsafe_allow_html=True)
    session_counts = trades.get("TradeSession", pd.Series(dtype=str)).value_counts().to_dict() if not trades.empty else {}

    source_exit_rows = 0
    if not raw_df.empty and "RowType" in raw_df.columns:
        source_types = raw_df["RowType"].astype(str).str.upper().str.strip()
        source_exit_rows = int(source_types.isin(["EXIT", "CLOSE", "CLOSED", "TRADE_EXIT", "E"]).sum())
    elif not raw_df.empty:
        source_exit_rows = len(raw_df)
    rows_removed = max(0, int(source_exit_rows - len(trades)))

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("Closed EXIT Trades", len(trades))
    c2.metric("EXIT-Zeilen entfernt", rows_removed, help="Soll 0 sein: gleich aussehende Multi-Limit-Trades werden nicht automatisch entfernt.")
    c3.metric("RTH Trades", int(session_counts.get("RTH", 0)))
    c4.metric("ETH Trades", int(session_counts.get("ETH", 0)))
    c5.metric("Algos erkannt", trades["Algo"].nunique() if not trades.empty else 0)
    c6.metric("Accounts", trades["TradeAccount"].nunique() if not trades.empty else 0)
    c7.metric("Gefilterte Trades", len(filtered))

    missing_dt = int(trades["DateTime"].isna().sum()) if not trades.empty else 0
    unknown_algo = int((trades["Algo"] == "Unbekannter Algo").sum()) if not trades.empty else 0
    blank_notes = int((trades["Notes"].astype(str).str.strip() == "").sum()) if not trades.empty else 0
    quality = pd.DataFrame(
        [
            ["EXIT-Zeilen automatisch entfernt", rows_removed],
            ["Ungültige DateTime", missing_dt],
            ["Unbekannter Algo", unknown_algo],
            ["Closed Trades ohne Notes", blank_notes],
        ],
        columns=["Check", "Rows"],
    )
    st.dataframe(quality, use_container_width=True, hide_index=True)

    with st.expander("Rohdaten-Spalten", expanded=False):
        st.code(", ".join(raw_df.columns.tolist()))
        if info.get("sha"):
            st.code(f"GitHub SHA: {info.get('sha')}")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide", initial_sidebar_state="expanded")
    inject_css()
    require_password()

    refresh_default_index = 3
    refresh_sec = st.sidebar.selectbox(
        "Auto-Refresh",
        [0, 15, 30, 60, 120, 300],
        index=refresh_default_index,
        format_func=lambda x: "Aus" if x == 0 else f"{x} Sek.",
    )
    if refresh_sec > 0:
        components.html(
            f"<script>setTimeout(function(){{window.parent.location.reload();}}, {int(refresh_sec) * 1000});</script>",
            height=0,
        )

    display_contract = st.sidebar.selectbox(
        "P/L Darstellung",
        ["ES", "MES"],
        index=0,
        format_func=lambda x: f"{x} · {num(CONTRACT_TICK_VALUES[x], 2)} $/Tick",
        help=(
            "Standard ist ES. Das Dashboard normalisiert alle realisierten Dollar-P/L-Werte auf die gewählte "
            "Tick-Basis. MES = 1,25 $/Tick, ES = 12,50 $/Tick."
        ),
        key="display_contract_basis",
    )
    st.sidebar.caption(
        f"Aktive P/L-Basis: {display_contract} · {num(CONTRACT_TICK_VALUES[display_contract], 2)} $ pro Tick"
    )

    session_view = st.sidebar.selectbox(
        "Session",
        SESSION_CHOICES,
        index=0,
        format_func=lambda x: "Globex · RTH + ETH" if x == "Globex" else x,
        help=(
            "Standard ist Globex und zeigt RTH + ETH zusammen. "
            "RTH = tatsächlicher Entry 08:30 bis vor 15:00 CT. "
            "ETH = alle übrigen Globex-Zeiten außerhalb des RTH-Fensters."
        ),
        key="session_view_filter",
    )
    st.sidebar.caption(
        "Aktive Session: Globex (RTH + ETH)" if session_view == "Globex" else f"Aktive Session: {session_view}"
    )

    try:
        raw_df, info, _ = load_data()
    except Exception as exc:
        st.error(f"Daten konnten nicht geladen werden: {exc}")
        st.info("Prüfe GitHub-Secrets, Repo-Name, Branch, data_path und ob das Upload-Script läuft.")
        st.stop()

    trades = prepare_trades(raw_df, display_contract)
    show_header(trades, info, session_view)

    if raw_df.empty:
        st.warning("CSV ist noch leer. Sobald geschlossene Trades synchronisiert sind, erscheinen die Auswertungen hier.")
        source_status(info, raw_df, trades)
        st.stop()

    if trades.empty:
        st.warning("Es wurden keine geschlossenen Trades gefunden. Erwartet wird RowType = EXIT/CLOSED.")
        st.dataframe(raw_df, use_container_width=True)
        st.stop()

    filtered, risk_limit_ticks = sidebar_filters(trades, session_view)
    summary = calc_summary(filtered)

    if filtered.empty:
        st.warning(
            "Die aktuelle Filterkombination enthält keine geschlossenen Trades. "
            "Die Session-Umschaltung selbst ist aktiv; prüfe ggf. bewusst gesetzte Algo-, Zeitraum- oder technische Filter."
        )
        st.stop()

    show_hero(summary, filtered)
    show_kpis(summary)

    tabs = st.tabs([
        "Executive",
        "Algorithmen",
        "Performance",
        "Risk & Qualität",
        "Trades & Audit",
        "Data Quality & Korrektur",
        "Data / System",
    ])

    with tabs[0]:
        executive_tab(filtered, summary)
    with tabs[1]:
        algos_tab(filtered)
    with tabs[2]:
        performance_tab(filtered)
    with tabs[3]:
        risk_tab(filtered, risk_limit_ticks)
    with tabs[4]:
        trades_tab(filtered)
    with tabs[5]:
        data_quality_tab(raw_df)
    with tabs[6]:
        system_tab(raw_df, trades, filtered, info, session_view)

    mode = execution_mode(filtered)
    st.markdown(
        f"""
        <div class="wt-disclosure">
          <strong>Performance disclosure:</strong> Angezeigt wird ausschließlich realisiertes P/L aus importierten geschlossenen Trades,
          normalisiert auf <strong>{html.escape(display_contract)} · {num(CONTRACT_TICK_VALUES[display_contract], 2)} $/Tick</strong>.
          Die Performance wird verbindlich aus <code>PNL_Ticks × gewähltem Tickwert</code> berechnet.
          Original-Dollarwerte bleiben im Audit als <code>PNL_Currency_Source</code> erhalten; <code>PNL_Adjustment_Source</code> ist ausschließlich ein Quellen-Auditfeld
          und verändert die standardisierte Performance nicht. Gleich aussehende ENTRY-/EXIT-Zeilen werden nicht automatisch dedupliziert,
          weil sie legitime Multi-Limit-/Multi-Entry-Ausführungen darstellen können.
          Aktive Session: <strong>{html.escape(session_view)}</strong> (Globex = RTH + ETH; RTH 08:30–15:00 CT).
          Die aktuelle Datenmenge ist als <strong>{html.escape(mode)}</strong> erkannt.
          Simulations-/Backtest-Ergebnisse sind keine Garantie für zukünftige Ergebnisse. Dashboard v{APP_VERSION}.
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
