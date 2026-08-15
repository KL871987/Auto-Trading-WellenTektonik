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
"""
from __future__ import annotations

import base64
import csv
import html
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
APP_VERSION = "2.0.3"
LOCAL_CSV = os.path.join("data", "trades.csv")
DEFAULT_REFRESH_SECONDS = 60

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
    df.columns = [str(c).strip() for c in df.columns]
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()
    return df


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
    m = re.search(r"(?:^|;)\s*Module=([^;]+)", text, flags=re.IGNORECASE)
    return clean_label(m.group(1) if m else "", "Nicht angegeben")


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


def prepare_trades(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    if "RowType" not in out.columns:
        out["RowType"] = "EXIT"

    out["RowType"] = out["RowType"].astype(str).str.upper().str.strip()
    out = out[out["RowType"].isin(["EXIT", "CLOSE", "CLOSED", "TRADE_EXIT", "E"])]
    if out.empty:
        return out

    numeric_cols = [
        "EntryLevel4", "EntryFillPrice", "ExitPrice", "StopPrice", "TargetPrice",
        "RiskTicks", "RewardTicks", "Quantity", "PNL_Ticks", "PNL_Currency",
        "MAE_Ticks", "MFE_Ticks", "CumPNL_Currency", "MaxDrawdown_Currency",
    ]
    for c in numeric_cols:
        out[c] = safe_col(out, c, 0).map(normalize_number)

    out["DateTime"] = pd.to_datetime(safe_col(out, "DateTime", ""), errors="coerce")
    out["SignalDateTime"] = pd.to_datetime(safe_col(out, "SignalDateTime", ""), errors="coerce")
    out["TradeID"] = safe_col(out, "TradeID", "").astype(str)
    out["TradeAccount"] = safe_col(out, "TradeAccount", "").map(clean_label)
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


def show_header(trades: pd.DataFrame, info: Dict[str, Any]) -> None:
    mode = execution_mode(trades)
    dot_class = "sim" if mode == "SIMULATION" else ""
    st.markdown(
        f"""
        <div class="wt-topline">
          <div>
            <div class="wt-brand">WellenTektonik Quant Systems</div>
            <div class="wt-title">Portfolio Analytics</div>
            <div class="wt-subtitle">Multi-Strategy · Realized P/L · Risk & Execution Audit</div>
          </div>
          <div class="wt-badge"><span class="wt-dot {dot_class}"></span>{html.escape(mode)} · v{APP_VERSION}</div>
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


def sidebar_filters(trades: pd.DataFrame) -> Tuple[pd.DataFrame, float]:
    st.sidebar.markdown("### Portfolio Filter")
    st.sidebar.caption("Alle Filter wirken identisch auf KPI, Tabellen und Equity-Kurve.")

    risk_default = normalize_number(_secret("RISK_LIMIT_TICKS", "15")) or 15.0
    risk_limit_ticks = st.sidebar.number_input(
        "Globales Risk-Limit (Ticks)",
        min_value=0.0,
        max_value=10000.0,
        value=float(risk_default),
        step=1.0,
        help="Nur für den Risk-Monitor. Die Strategie-Logik in Sierra Chart wird dadurch nicht verändert.",
    )

    out = trades.copy()

    algos = sorted(out["Algo"].dropna().astype(str).unique().tolist(), key=natural_algo_sort_key)
    selected_algos = st.sidebar.multiselect("Algorithmen", algos, default=algos)
    if selected_algos and len(selected_algos) != len(algos):
        out = out[out["Algo"].isin(selected_algos)]
    elif not selected_algos and algos:
        out = out.iloc[0:0]

    if "DateTime" in out and out["DateTime"].notna().any():
        min_date = out["DateTime"].min().date()
        max_date = out["DateTime"].max().date()
        dr = st.sidebar.date_input(
            "Zeitraum",
            value=(min_date, max_date),
            min_value=min_date,
            max_value=max_date,
        )
        if isinstance(dr, tuple) and len(dr) == 2:
            start, end = dr
            out = out[(out["DateTime"].dt.date >= start) & (out["DateTime"].dt.date <= end)]

    with st.sidebar.expander("Technische Filter", expanded=False):
        for col, label in [
            ("TradeAccount", "Trade Account"),
            ("Symbol", "Symbol"),
            ("Direction", "Richtung"),
            ("CountColor", "CountColor / Legacy Tag"),
            ("Module", "Modul"),
        ]:
            options = sorted(out[col].dropna().astype(str).unique().tolist()) if col in out else []
            selected = st.multiselect(label, options, default=options, key=f"filter_{col}")
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
    st.markdown(
        f"""
        <div class="wt-status-grid">
          <div class="wt-status-item"><div class="wt-status-k">Datenquelle</div><div class="wt-status-v">{html.escape(source.upper())}</div></div>
          <div class="wt-status-item"><div class="wt-status-k">Letzter Closed Trade</div><div class="wt-status-v">{html.escape(latest)}</div></div>
          <div class="wt-status-item"><div class="wt-status-k">CSV / Closed Rows</div><div class="wt-status-v">{len(raw_df):,} / {len(trades):,}</div></div>
          <div class="wt-status-item"><div class="wt-status-k">Geladen</div><div class="wt-status-v">{html.escape(loaded)}</div></div>
        </div>
        <div class="wt-meta" style="margin-top:8px">Pfad: {html.escape(path)}</div>
        """,
        unsafe_allow_html=True,
    )


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
            show = [c for c in ["DateTime", "Algo", "Module", "TradeAccount", "Direction", "RiskTicks", "PNL_Currency", "ExitReason", "TradeID"] if c in violations.columns]
            st.dataframe(violations[show].sort_values("DateTime", ascending=False), use_container_width=True, hide_index=True)


def trades_tab(filtered: pd.DataFrame) -> None:
    st.markdown('<div class="wt-section-title">Trade Audit Trail</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="wt-section-sub">Nur geschlossene Trades. Technische Detailfelder bleiben für Nachvollziehbarkeit und Export erhalten.</div>',
        unsafe_allow_html=True,
    )
    cols = [
        "DateTime", "TradeID", "Algo", "AlgoVersion", "Module", "TradeAccount", "Symbol", "CountColor", "Direction",
        "EntryFillPrice", "ExitPrice", "StopPrice", "TargetPrice", "RiskTicks", "RewardTicks", "Quantity",
        "PNL_Ticks", "PNL_Currency", "MAE_Ticks", "MFE_Ticks", "ExitReason", "Equity", "Drawdown", "Notes",
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


def system_tab(raw_df: pd.DataFrame, trades: pd.DataFrame, filtered: pd.DataFrame, info: Dict[str, Any]) -> None:
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

    st.markdown('<div class="wt-section-title">Datenintegrität</div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Closed Trades gesamt", len(trades))
    c2.metric("Algos erkannt", trades["Algo"].nunique() if not trades.empty else 0)
    c3.metric("Accounts", trades["TradeAccount"].nunique() if not trades.empty else 0)
    c4.metric("Gefilterte Trades", len(filtered))

    missing_dt = int(trades["DateTime"].isna().sum()) if not trades.empty else 0
    unknown_algo = int((trades["Algo"] == "Unbekannter Algo").sum()) if not trades.empty else 0
    blank_notes = int((trades["Notes"].astype(str).str.strip() == "").sum()) if not trades.empty else 0
    quality = pd.DataFrame(
        [
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

    try:
        raw_df, info, _ = load_data()
    except Exception as exc:
        st.error(f"Daten konnten nicht geladen werden: {exc}")
        st.info("Prüfe GitHub-Secrets, Repo-Name, Branch, data_path und ob das Upload-Script läuft.")
        st.stop()

    trades = prepare_trades(raw_df)
    show_header(trades, info)

    if raw_df.empty:
        st.warning("CSV ist noch leer. Sobald geschlossene Trades synchronisiert sind, erscheinen die Auswertungen hier.")
        source_status(info, raw_df, trades)
        st.stop()

    if trades.empty:
        st.warning("Es wurden keine geschlossenen Trades gefunden. Erwartet wird RowType = EXIT/CLOSED.")
        st.dataframe(raw_df, use_container_width=True)
        st.stop()

    filtered, risk_limit_ticks = sidebar_filters(trades)
    summary = calc_summary(filtered)

    if filtered.empty:
        st.warning("Die aktuelle Filterkombination enthält keine geschlossenen Trades.")
        st.stop()

    show_hero(summary, filtered)
    show_kpis(summary)

    tabs = st.tabs([
        "Executive",
        "Algorithmen",
        "Performance",
        "Risk & Qualität",
        "Trades & Audit",
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
        system_tab(raw_df, trades, filtered, info)

    mode = execution_mode(filtered)
    st.markdown(
        f"""
        <div class="wt-disclosure">
          <strong>Performance disclosure:</strong> Angezeigt wird ausschließlich realisiertes P/L aus importierten geschlossenen Trades.
          Gebühren, Slippage, Funding, Abgaben oder andere Kosten sind nur enthalten, wenn sie bereits in <code>PNL_Currency</code> des Exports enthalten sind.
          Die aktuelle Datenmenge ist als <strong>{html.escape(mode)}</strong> erkannt. Simulations-/Backtest-Ergebnisse sind keine Garantie für zukünftige Ergebnisse.
          Dashboard v{APP_VERSION}.
        </div>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
