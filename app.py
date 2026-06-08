"""
WT Blau/Schwarz Level4 AutoTrader - Streamlit GitHub Dashboard

Datenquelle:
1) GitHub Contents API, wenn Streamlit-Secrets oder Umgebungsvariablen gesetzt sind
2) lokaler Fallback: data/trades.csv

CSV-Spalten passend zum AutoTrader:
RowType, TradeID, ChartNumber, Symbol, TradeAccount, DateTime, CountColor,
Direction, SignalBarIndex, SignalDateTime, EntryLevel4, EntryFillPrice, ExitPrice,
StopPrice, TargetPrice, RiskTicks, RewardTicks, Quantity, PNL_Ticks, PNL_Currency,
MAE_Ticks, MFE_Ticks, ExitReason, CumPNL_Currency, MaxDrawdown_Currency, Notes
"""
from __future__ import annotations

import base64
import csv
import io
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import streamlit.components.v1 as components

APP_TITLE = "WT Blau/Schwarz Level4 AutoTrader"
LOCAL_CSV = os.path.join("data", "trades.csv")
DEFAULT_REFRESH_SECONDS = 60


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


def require_password() -> None:
    password = _secret("DASHBOARD_PASSWORD", "").strip()
    if not password:
        st.sidebar.warning("Kein Dashboard-Passwort gesetzt. Bei öffentlicher App kann jeder die Auswertung sehen.")
        return

    if st.session_state.get("auth_ok") is True:
        return

    st.title("Login")
    entered = st.text_input("Dashboard-Passwort", type="password")
    if st.button("Einloggen"):
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


def prepare_trades(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    # Falls RowType fehlt, wird alles als geschlossener Trade behandelt.
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
    out["CountColor"] = safe_col(out, "CountColor", "").replace({"BLUE": "Blau", "BLACK": "Schwarz"})
    out["Direction"] = safe_col(out, "Direction", "").astype(str).str.upper()
    out["TradeAccount"] = safe_col(out, "TradeAccount", "").astype(str)
    out["ExitReason"] = safe_col(out, "ExitReason", "").astype(str)

    out = out.sort_values(["DateTime", "TradeID"], na_position="last").reset_index(drop=True)

    # Equity berechnen. Falls CumPNL fehlt/0 bleibt, wird aus PNL_Currency kumuliert.
    if out["CumPNL_Currency"].abs().sum() == 0 and out["PNL_Currency"].abs().sum() != 0:
        out["Equity"] = out["PNL_Currency"].cumsum()
    else:
        out["Equity"] = out["CumPNL_Currency"]
        # Wenn einzelne Nullwerte vorkommen, trotzdem mit eigener Kurve auffüllen.
        if out["Equity"].abs().sum() == 0:
            out["Equity"] = out["PNL_Currency"].cumsum()

    out["EquityHigh"] = out["Equity"].cummax()
    out["Drawdown"] = out["Equity"] - out["EquityHigh"]
    out["Win"] = out["PNL_Currency"] > 0
    out["Loss"] = out["PNL_Currency"] < 0
    out["RiskViolation"] = out["RiskTicks"] > 15.0001
    out["Day"] = out["DateTime"].dt.date
    out["Week"] = out["DateTime"].dt.to_period("W").astype(str)
    out["Month"] = out["DateTime"].dt.to_period("M").astype(str)
    out["Year"] = out["DateTime"].dt.year.astype("Int64").astype(str).replace("<NA>", "")

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
        }
    pnl = trades["PNL_Currency"]
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(pnl[pnl < 0].sum())
    pf = gross_profit / abs(gross_loss) if gross_loss < 0 else (float("inf") if gross_profit > 0 else 0.0)
    return {
        "trades": int(len(trades)),
        "net": float(pnl.sum()),
        "wins": wins,
        "losses": losses,
        "winrate": float(wins / len(trades) * 100) if len(trades) else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": pf,
        "max_dd": float(trades["Drawdown"].min()) if "Drawdown" in trades else 0.0,
        "avg_trade": float(pnl.mean()) if len(trades) else 0.0,
        "best": float(pnl.max()) if len(trades) else 0.0,
        "worst": float(pnl.min()) if len(trades) else 0.0,
        "risk_violations": int(trades["RiskViolation"].sum()) if "RiskViolation" in trades else 0,
        "win_streak": longest_streak(pnl > 0),
        "loss_streak": longest_streak(pnl < 0),
    }


def money(x: float) -> str:
    return f"{x:,.2f} $".replace(",", "X").replace(".", ",").replace("X", ".")


def num(x: float, decimals: int = 2) -> str:
    if x == float("inf"):
        return "∞"
    return f"{x:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def color_class(value: float) -> str:
    if value > 0:
        return "normal"
    if value < 0:
        return "inverse"
    return "off"


def show_kpis(summary: Dict[str, Any]) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Netto P/L", money(summary["net"]))
    c2.metric("Trades", f"{summary['trades']}", f"Winrate {num(summary['winrate'], 1)} %")
    c3.metric("Max Drawdown", money(summary["max_dd"]))
    c4.metric("Profit Factor", num(summary["profit_factor"], 2))

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Ø Trade", money(summary["avg_trade"]))
    c6.metric("Bester Trade", money(summary["best"]))
    c7.metric("Schlechtester Trade", money(summary["worst"]))
    c8.metric("Risk-Verstöße >15T", f"{summary['risk_violations']}")


def group_table(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    if trades.empty or by not in trades:
        return pd.DataFrame()
    g = trades.groupby(by, dropna=False).agg(
        Trades=("PNL_Currency", "size"),
        Netto_PL=("PNL_Currency", "sum"),
        Avg_PL=("PNL_Currency", "mean"),
        Wins=("Win", "sum"),
        Losses=("Loss", "sum"),
        Max_DD=("Drawdown", "min"),
        Avg_RiskTicks=("RiskTicks", "mean"),
        Risk_Verstöße=("RiskViolation", "sum"),
    ).reset_index()
    g["Winrate_%"] = (g["Wins"] / g["Trades"] * 100).round(1)
    g = g.sort_values(by, ascending=False)
    return g


def make_equity_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty:
        return fig
    x = trades["DateTime"] if trades["DateTime"].notna().any() else trades.index
    fig.add_trace(go.Scatter(x=x, y=trades["Equity"], mode="lines+markers", name="Equity"))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), template="plotly_dark")
    fig.update_yaxes(title_text="Kumulierte P/L")
    return fig


def make_drawdown_chart(trades: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if trades.empty:
        return fig
    x = trades["DateTime"] if trades["DateTime"].notna().any() else trades.index
    fig.add_trace(go.Scatter(x=x, y=trades["Drawdown"], mode="lines", name="Drawdown", fill="tozeroy"))
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), template="plotly_dark")
    fig.update_yaxes(title_text="Drawdown")
    return fig


def make_daily_bar(trades: pd.DataFrame) -> go.Figure:
    if trades.empty or "Day" not in trades:
        return go.Figure()
    daily = trades.groupby("Day", dropna=False)["PNL_Currency"].sum().reset_index()
    daily["Day"] = daily["Day"].astype(str)
    fig = px.bar(daily, x="Day", y="PNL_Currency", title="Tages P/L", template="plotly_dark")
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def make_color_direction_chart(trades: pd.DataFrame) -> go.Figure:
    if trades.empty:
        return go.Figure()
    view = trades.groupby(["CountColor", "Direction"], dropna=False)["PNL_Currency"].sum().reset_index()
    fig = px.bar(view, x="CountColor", y="PNL_Currency", color="Direction", barmode="group", title="P/L nach CountColor und Richtung", template="plotly_dark")
    fig.update_layout(height=330, margin=dict(l=10, r=10, t=40, b=10))
    return fig


def sidebar_filters(trades: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Filter")
    out = trades.copy()

    accounts = sorted([x for x in out.get("TradeAccount", pd.Series(dtype=str)).dropna().astype(str).unique() if x])
    account = st.sidebar.multiselect("Trade Account", accounts, default=accounts)
    if account:
        out = out[out["TradeAccount"].isin(account)]

    colors = sorted([x for x in out.get("CountColor", pd.Series(dtype=str)).dropna().astype(str).unique() if x])
    selected_colors = st.sidebar.multiselect("CountColor", colors, default=colors)
    if selected_colors:
        out = out[out["CountColor"].isin(selected_colors)]

    directions = sorted([x for x in out.get("Direction", pd.Series(dtype=str)).dropna().astype(str).unique() if x])
    selected_dirs = st.sidebar.multiselect("Richtung", directions, default=directions)
    if selected_dirs:
        out = out[out["Direction"].isin(selected_dirs)]

    if "DateTime" in out and out["DateTime"].notna().any():
        min_date = out["DateTime"].min().date()
        max_date = out["DateTime"].max().date()
        dr = st.sidebar.date_input("Zeitraum", value=(min_date, max_date), min_value=min_date, max_value=max_date)
        if isinstance(dr, tuple) and len(dr) == 2:
            start, end = dr
            out = out[(out["DateTime"].dt.date >= start) & (out["DateTime"].dt.date <= end)]

    return out


def load_data() -> Tuple[pd.DataFrame, Dict[str, Any], str]:
    gh = get_github_config()
    uploaded_text = None
    uploaded = st.sidebar.file_uploader("CSV manuell testen", type=["csv", "txt"])
    if uploaded is not None:
        uploaded_text = uploaded.getvalue().decode("utf-8-sig", errors="replace")
        return parse_csv(uploaded_text), {"source": "upload", "path": uploaded.name, "loaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, uploaded_text

    if gh.owner and gh.repo:
        content, info = fetch_from_github(gh.owner, gh.repo, gh.branch, gh.data_path, gh.token)
        return parse_csv(content), info, content

    content, info = read_local_csv(LOCAL_CSV)
    return parse_csv(content), info, content


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide")
    require_password()

    st.title("📈 WT Blau/Schwarz Level4 AutoTrader Dashboard")
    st.caption("Streamlit + GitHub Live-Auswertung für AutoTrader-CSV. Grundlage: geschlossene Trades mit RowType = EXIT.")

    refresh_sec = st.sidebar.selectbox("Auto-Refresh", [0, 15, 30, 60, 120, 300], index=3, format_func=lambda x: "Aus" if x == 0 else f"{x} Sek.")
    if refresh_sec > 0:
        components.html(f"<script>setTimeout(function(){{window.parent.location.reload();}}, {int(refresh_sec)*1000});</script>", height=0)

    try:
        raw_df, info, _ = load_data()
    except Exception as exc:
        st.error(f"Daten konnten nicht geladen werden: {exc}")
        st.info("Prüfe GitHub-Secrets, Repo-Name, Branch, data_path und ob das Upload-Script läuft.")
        st.stop()

    trades = prepare_trades(raw_df)

    with st.expander("Datenquelle / Status", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.write(f"**Quelle:** {info.get('source', '-')}")
        c2.write(f"**Pfad:** {info.get('path', '-')}")
        c3.write(f"**Geladen:** {info.get('loaded_at', '-')}")
        c4.write(f"**CSV-Zeilen:** {len(raw_df)} / **Closed Trades:** {len(trades)}")
        if info.get("sha"):
            st.code(f"GitHub SHA: {info.get('sha')}")

    if raw_df.empty:
        st.warning("CSV ist noch leer. Sobald der AutoTrader Trades in die CSV schreibt und das Sync-Script hochlädt, erscheinen hier Daten.")
        st.stop()

    if trades.empty:
        st.warning("Es wurden keine geschlossenen Trades gefunden. Erwartet wird RowType = EXIT.")
        st.dataframe(raw_df, use_container_width=True)
        st.stop()

    filtered = sidebar_filters(trades)
    summary = calc_summary(filtered)
    show_kpis(summary)

    tab_overview, tab_periods, tab_trades, tab_risk, tab_setup = st.tabs([
        "Übersicht", "Jahr / Monat / Woche / Tag", "Trade-Liste", "Risk & Qualität", "Setup"
    ])

    with tab_overview:
        c1, c2 = st.columns([2, 1])
        with c1:
            st.plotly_chart(make_equity_chart(filtered), use_container_width=True)
            st.plotly_chart(make_drawdown_chart(filtered), use_container_width=True)
        with c2:
            st.plotly_chart(make_daily_bar(filtered), use_container_width=True)
            st.plotly_chart(make_color_direction_chart(filtered), use_container_width=True)

    with tab_periods:
        st.subheader("Jahresauswertung")
        st.dataframe(group_table(filtered, "Year"), use_container_width=True, hide_index=True)
        st.subheader("Monatsauswertung")
        st.dataframe(group_table(filtered, "Month"), use_container_width=True, hide_index=True)
        st.subheader("Wochenauswertung")
        st.dataframe(group_table(filtered, "Week"), use_container_width=True, hide_index=True)
        st.subheader("Tagesauswertung")
        st.dataframe(group_table(filtered, "Day"), use_container_width=True, hide_index=True)

    with tab_trades:
        cols = [
            "DateTime", "TradeID", "TradeAccount", "Symbol", "CountColor", "Direction",
            "EntryFillPrice", "ExitPrice", "StopPrice", "TargetPrice", "RiskTicks",
            "RewardTicks", "Quantity", "PNL_Ticks", "PNL_Currency", "MAE_Ticks",
            "MFE_Ticks", "ExitReason", "Equity", "Drawdown", "Notes"
        ]
        show_cols = [c for c in cols if c in filtered.columns]
        st.dataframe(filtered[show_cols].sort_values("DateTime", ascending=False), use_container_width=True, hide_index=True)
        csv_bytes = filtered.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Gefilterte Trades als CSV herunterladen", csv_bytes, file_name="WT_gefilterte_trades.csv", mime="text/csv")

    with tab_risk:
        st.subheader("Risk-Check nach deinen Regeln")
        st.write("Regel: RiskTicks maximal 15. Jeder Trade mit RiskTicks > 15 wird als Verstoß markiert.")
        violations = filtered[filtered.get("RiskViolation", False) == True] if "RiskViolation" in filtered else pd.DataFrame()
        c1, c2, c3 = st.columns(3)
        c1.metric("Risk-Verstöße", int(len(violations)))
        c2.metric("Max RiskTicks", num(float(filtered["RiskTicks"].max()) if len(filtered) else 0, 2))
        c3.metric("Ø MAE Ticks", num(float(filtered["MAE_Ticks"].mean()) if len(filtered) else 0, 2))
        if len(violations):
            st.error("Es gibt Trades mit RiskTicks > 15.")
            st.dataframe(violations, use_container_width=True, hide_index=True)
        else:
            st.success("Keine RiskTicks-Verstöße in der aktuellen Filterung.")

        st.subheader("ExitReason")
        exit_stats = filtered.groupby("ExitReason", dropna=False).agg(
            Trades=("PNL_Currency", "size"),
            Netto_PL=("PNL_Currency", "sum"),
            Avg_PL=("PNL_Currency", "mean"),
            Winrate=("Win", lambda s: round(float(s.mean() * 100), 1) if len(s) else 0),
        ).reset_index().sort_values("Trades", ascending=False)
        st.dataframe(exit_stats, use_container_width=True, hide_index=True)

    with tab_setup:
        st.subheader("Aktive Entry-Regeln")
        st.markdown(
            """
Der AutoTrader soll nur handeln, wenn diese Bedingungen im WellenTektonik-Indikator erfüllt sind:
Regel A:

Chart = 377 Tick
CountColor = Gelb oder Schwarz oder Blau
Rot ausschließen
HarmonicStrongImpulse = Ja
RiskTicks <= 70
Entry Level4
Stop hinter Level4Plus +/- 1 Punkt
Target5

Das heißt:

Regel A handelt Gelb, Schwarz und Blau.
Regel A braucht keine Gelb+Schwarz-Bestätigung.
Regel A braucht HarmonicStrongImpulse = Ja.
Regel A erlaubt RiskTicks bis 70.

Handelbar sind mehrere Kontrakte in einer Richtung

"""
        )
        st.info("Dieses Dashboard wertet nur die exportierte CSV aus. Die eigentliche Entry- und Orderlogik liegt im ACSIL AutoTrader in Sierra Chart.")


if __name__ == "__main__":
    main()
