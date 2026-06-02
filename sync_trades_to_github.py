#!/usr/bin/env python3
"""
WT Trades -> GitHub Sync

Liest die AutoTrader-CSV vom Trading-PC und schreibt sie per GitHub Contents API
in ein GitHub-Repository, z.B. data/trades.csv.

Wichtig:
- Token niemals in GitHub committen.
- Das Script lädt nur hoch, wenn sich der Dateiinhalt wirklich geändert hat.
- Der Update-Endpunkt erzeugt pro Upload einen GitHub-Commit.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

API_VERSION = "2022-11-28"
STATUS_FILE = "sync_status.json"


@dataclass
class Config:
    token: str
    owner: str
    repo: str
    branch: str
    target_path: str
    local_csv_path: str
    interval_seconds: int
    commit_prefix: str


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def read_env(config_file: str) -> Config:
    if config_file:
        load_dotenv(config_file)
    else:
        load_dotenv()

    def env(name: str, default: str = "") -> str:
        return os.getenv(name, default).strip()

    missing = []
    for key in ["GITHUB_TOKEN", "GITHUB_OWNER", "GITHUB_REPO", "LOCAL_CSV_PATH"]:
        if not env(key):
            missing.append(key)
    if missing:
        raise SystemExit(f"Fehlende Konfiguration in config.env/Umgebung: {', '.join(missing)}")

    try:
        interval = int(env("SYNC_INTERVAL_SECONDS", "60"))
    except Exception:
        interval = 60
    interval = max(interval, 15)

    return Config(
        token=env("GITHUB_TOKEN"),
        owner=env("GITHUB_OWNER"),
        repo=env("GITHUB_REPO"),
        branch=env("GITHUB_BRANCH", "main") or "main",
        target_path=env("GITHUB_TARGET_PATH", "data/trades.csv") or "data/trades.csv",
        local_csv_path=env("LOCAL_CSV_PATH"),
        interval_seconds=interval,
        commit_prefix=env("COMMIT_MESSAGE_PREFIX", "auto: update WT trades") or "auto: update WT trades",
    )


def headers(cfg: Config) -> Dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {cfg.token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "WT-TradeSync/1.0",
    }


def contents_url(cfg: Config) -> str:
    return f"https://api.github.com/repos/{cfg.owner}/{cfg.repo}/contents/{cfg.target_path}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_remote_sha(cfg: Config) -> Optional[str]:
    r = requests.get(contents_url(cfg), headers=headers(cfg), params={"ref": cfg.branch}, timeout=30)
    if r.status_code == 404:
        return None
    if r.status_code == 401:
        raise RuntimeError("GitHub 401: Token ungültig oder keine Berechtigung.")
    if r.status_code == 403:
        raise RuntimeError(f"GitHub 403: Keine Berechtigung oder Rate-Limit. Antwort: {r.text[:300]}")
    r.raise_for_status()
    return r.json().get("sha")


def put_file(cfg: Config, content: bytes, remote_sha: Optional[str]) -> Tuple[bool, str]:
    message = f"{cfg.commit_prefix} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    payload = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": cfg.branch,
    }
    if remote_sha:
        payload["sha"] = remote_sha

    r = requests.put(contents_url(cfg), headers=headers(cfg), json=payload, timeout=60)
    if r.status_code == 409:
        # Repo hat sich zwischen GET und PUT geändert. Einmal sauber wiederholen.
        new_sha = get_remote_sha(cfg)
        payload["sha"] = new_sha
        r = requests.put(contents_url(cfg), headers=headers(cfg), json=payload, timeout=60)
    if r.status_code >= 400:
        return False, f"GitHub Upload Fehler {r.status_code}: {r.text[:600]}"
    data = r.json()
    commit_sha = data.get("commit", {}).get("sha", "")
    return True, commit_sha


def write_status(status: Dict[str, object]) -> None:
    try:
        Path(STATUS_FILE).write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_last_hash() -> str:
    try:
        data = json.loads(Path(STATUS_FILE).read_text(encoding="utf-8"))
        return str(data.get("last_hash", ""))
    except Exception:
        return ""


def sync_once(cfg: Config, force: bool = False) -> bool:
    csv_path = Path(cfg.local_csv_path)
    if not csv_path.exists():
        msg = f"CSV nicht gefunden: {csv_path}"
        log(msg)
        write_status({"ok": False, "time": now(), "message": msg, "path": str(csv_path)})
        return False

    content = csv_path.read_bytes()
    if not content.strip():
        msg = "CSV ist leer. Upload übersprungen."
        log(msg)
        write_status({"ok": False, "time": now(), "message": msg, "path": str(csv_path)})
        return False

    digest = sha256_bytes(content)
    last_digest = load_last_hash()
    if digest == last_digest and not force:
        log("Keine Änderung in der CSV. Kein GitHub-Commit nötig.")
        write_status({
            "ok": True,
            "time": now(),
            "message": "unchanged",
            "last_hash": digest,
            "local_csv_path": str(csv_path),
            "github_target": f"{cfg.owner}/{cfg.repo}/{cfg.target_path}",
        })
        return True

    remote_sha = get_remote_sha(cfg)
    ok, result = put_file(cfg, content, remote_sha)
    if ok:
        msg = f"Upload OK. Commit: {result[:12]}"
        log(msg)
        write_status({
            "ok": True,
            "time": now(),
            "message": msg,
            "commit": result,
            "last_hash": digest,
            "local_csv_path": str(csv_path),
            "github_target": f"{cfg.owner}/{cfg.repo}/{cfg.target_path}",
            "bytes": len(content),
        })
        return True

    log(result)
    write_status({"ok": False, "time": now(), "message": result, "last_hash": last_digest})
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="WT AutoTrader CSV automatisch nach GitHub synchronisieren")
    parser.add_argument("--config", default="config.env", help="Pfad zu config.env")
    parser.add_argument("--once", action="store_true", help="Einmal synchronisieren und beenden")
    parser.add_argument("--watch", action="store_true", help="Dauerhaft synchronisieren")
    parser.add_argument("--force", action="store_true", help="Upload erzwingen, auch wenn Hash unverändert ist")
    args = parser.parse_args()

    cfg = read_env(args.config)
    log(f"Quelle: {cfg.local_csv_path}")
    log(f"Ziel: {cfg.owner}/{cfg.repo}/{cfg.target_path} Branch {cfg.branch}")

    if args.once or not args.watch:
        ok = sync_once(cfg, force=args.force)
        sys.exit(0 if ok else 1)

    while True:
        try:
            sync_once(cfg, force=False)
        except KeyboardInterrupt:
            log("Beendet durch Benutzer.")
            break
        except Exception as exc:
            log(f"Fehler: {exc}")
            write_status({"ok": False, "time": now(), "message": str(exc)})
        time.sleep(cfg.interval_seconds)


if __name__ == "__main__":
    main()
