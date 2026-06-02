# WT Blau/Schwarz Level4 AutoTrader – Streamlit + GitHub Dashboard

Dieses Paket baut ein technisches Konstrukt wie ein online erreichbares Trading-Dashboard:

```text
Sierra Chart AutoTrader CSV
        ↓
sync_trades_to_github.py auf deinem Trading-PC
        ↓
GitHub Repo: data/trades.csv
        ↓
Streamlit Dashboard App
        ↓
Handy / Laptop außerhalb WLAN
```

## Enthaltene Dateien

```text
app.py                                      Streamlit Dashboard
sync_trades_to_github.py                    Sync-Script Trading-PC → GitHub
requirements.txt                            Python-Abhängigkeiten
config.example.env                          Vorlage für GitHub-Upload-Konfiguration
.streamlit/secrets.example.toml             Vorlage für Streamlit Secrets
data/trades.csv                             leere Start-CSV mit AutoTrader-Spalten
START_SYNC_WT_TRADES_TO_GITHUB.bat          Windows-Startdatei für Dauersync
TEST_SYNC_ONCE.bat                          Windows-Testdatei für einmaligen Upload
START_STREAMLIT_LOCAL.bat                   Lokaler Dashboard-Test
```

## Sicherheitsprinzip

- `GITHUB_TOKEN` niemals in GitHub hochladen.
- `config.env` bleibt nur auf deinem Trading-PC.
- `.streamlit/secrets.toml` nicht committen.
- Wenn dein GitHub-Repo öffentlich ist, ist `data/trades.csv` öffentlich sichtbar. Das Dashboard-Passwort schützt dann nur die App, nicht die Rohdatei im Repo.
- Für echte Privatsphäre: privates GitHub-Repo + Streamlit-Secrets mit Token oder eine andere private Datenquelle.

## Lokaler Test

1. Ordner entpacken.
2. `START_STREAMLIT_LOCAL.bat` starten.
3. Browser öffnet Streamlit lokal.
4. Solange GitHub-Secrets fehlen, liest das Dashboard `data/trades.csv` lokal.

## Sync konfigurieren

1. `config.example.env` kopieren und als `config.env` speichern.
2. Werte eintragen:

```env
GITHUB_TOKEN=ghp_DEIN_TOKEN_HIER
GITHUB_OWNER=DEIN_GITHUB_USERNAME
GITHUB_REPO=wt-trading-dashboard
GITHUB_BRANCH=main
GITHUB_TARGET_PATH=data/trades.csv
LOCAL_CSV_PATH=C:\SierraChart\Data\WT_BlueBlack_Level4_AutoTrader_Trades.csv
SYNC_INTERVAL_SECONDS=60
```

3. Einmal testen:

```text
TEST_SYNC_ONCE.bat
```

4. Danach dauerhaft laufen lassen:

```text
START_SYNC_WT_TRADES_TO_GITHUB.bat
```

Das Script erstellt nur einen neuen Commit, wenn sich die CSV wirklich geändert hat.

## Streamlit Cloud Secrets

In Streamlit Cloud unter App Settings / Secrets diese Vorlage einfügen und anpassen:

```toml
DASHBOARD_PASSWORD = "bitte_aendern"

[github]
owner = "DEIN_GITHUB_USERNAME"
repo = "wt-trading-dashboard"
branch = "main"
data_path = "data/trades.csv"
token = ""
```

Bei privatem Repo muss `token` gesetzt werden. Bei öffentlichem Repo kann `token` leer bleiben.

## Dashboard-Auswertungen

- Netto P/L
- Trades
- Winrate
- Profit Factor
- Max Drawdown
- Ø Trade
- bester/schlechtester Trade
- Risk-Verstöße über 15 Ticks
- Equity Curve
- Drawdown Chart
- Tages P/L
- P/L nach Blau/Schwarz und Long/Short
- Jahres-/Monats-/Wochen-/Tagesauswertung
- Trade-Liste mit CSV-Download
- Risk-Check und ExitReason-Auswertung

## Erwartete CSV-Spalten

```text
RowType,TradeID,ChartNumber,Symbol,TradeAccount,DateTime,CountColor,Direction,SignalBarIndex,SignalDateTime,EntryLevel4,EntryFillPrice,ExitPrice,StopPrice,TargetPrice,RiskTicks,RewardTicks,Quantity,PNL_Ticks,PNL_Currency,MAE_Ticks,MFE_Ticks,ExitReason,CumPNL_Currency,MaxDrawdown_Currency,Notes
```

Das Dashboard wertet geschlossene Trades über `RowType = EXIT` aus.
