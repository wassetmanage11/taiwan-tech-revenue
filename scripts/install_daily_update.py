#!/usr/bin/env python3
"""Install a daily macOS launchd job for the Taiwan tech revenue dashboard."""

from __future__ import annotations

import plistlib
import subprocess
import sys
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.baggyeongmin.taiwan-tech-revenue"
SERVER_LABEL = "com.baggyeongmin.taiwan-tech-revenue.server"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
SERVER_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{SERVER_LABEL}.plist"
PYTHON = "/usr/bin/python3"
UPDATE_SCRIPT = PROJECT_ROOT / "scripts" / "update_from_yahoo.py"
SERVER_SCRIPT = PROJECT_ROOT / "scripts" / "dashboard_server.py"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "TaiwanTechRevenue"
LAUNCHD_UPDATE_SCRIPT = APP_SUPPORT / "update_from_yahoo.py"
LAUNCHD_SERVER_SCRIPT = APP_SUPPORT / "dashboard_server.py"
LAUNCHD_HTML = APP_SUPPORT / "index.html"
PROJECT_CUSTOM_COMPANIES = PROJECT_ROOT / "custom_companies.json"
CUSTOM_COMPANIES_JSON = APP_SUPPORT / "custom_companies.json"
LOG_DIR = APP_SUPPORT / "logs"


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def main() -> int:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    PLIST_PATH.parent.mkdir(exist_ok=True)
    LAUNCHD_UPDATE_SCRIPT.write_bytes(UPDATE_SCRIPT.read_bytes())
    LAUNCHD_UPDATE_SCRIPT.chmod(0o755)
    LAUNCHD_SERVER_SCRIPT.write_bytes(SERVER_SCRIPT.read_bytes())
    LAUNCHD_SERVER_SCRIPT.chmod(0o755)
    if not PROJECT_CUSTOM_COMPANIES.exists():
        PROJECT_CUSTOM_COMPANIES.write_text('{"companies":[]}\n')
    if CUSTOM_COMPANIES_JSON.exists():
        CUSTOM_COMPANIES_JSON.unlink()
    os.link(PROJECT_CUSTOM_COMPANIES, CUSTOM_COMPANIES_JSON)
    if LAUNCHD_HTML.exists():
        LAUNCHD_HTML.unlink()
    os.link(PROJECT_ROOT / "index.html", LAUNCHD_HTML)
    legacy_workbook = APP_SUPPORT / "source.xlsx"
    if legacy_workbook.exists():
        legacy_workbook.unlink()

    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            PYTHON,
            str(LAUNCHD_UPDATE_SCRIPT),
            "--html",
            str(LAUNCHD_HTML),
            "--companies-json",
            str(CUSTOM_COMPANIES_JSON),
            "--quiet",
        ],
        "WorkingDirectory": str(APP_SUPPORT),
        "StartCalendarInterval": {"Hour": 8, "Minute": 30},
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_DIR / "daily-update.log"),
        "StandardErrorPath": str(LOG_DIR / "daily-update.err.log"),
    }
    PLIST_PATH.write_bytes(plistlib.dumps(plist, sort_keys=False))

    server_plist = {
        "Label": SERVER_LABEL,
        "ProgramArguments": [
            PYTHON,
            str(LAUNCHD_SERVER_SCRIPT),
            "--html",
            str(LAUNCHD_HTML),
            "--companies-json",
            str(CUSTOM_COMPANIES_JSON),
            "--update-script",
            str(LAUNCHD_UPDATE_SCRIPT),
            "--port",
            "8765",
        ],
        "WorkingDirectory": str(APP_SUPPORT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG_DIR / "dashboard-server.log"),
        "StandardErrorPath": str(LOG_DIR / "dashboard-server.err.log"),
    }
    SERVER_PLIST_PATH.write_bytes(plistlib.dumps(server_plist, sort_keys=False))

    domain = f"gui/{run(['id', '-u']).stdout.strip()}"
    run(["launchctl", "bootout", domain, str(PLIST_PATH)], check=False)
    run(["launchctl", "bootout", domain, str(SERVER_PLIST_PATH)], check=False)
    run(["launchctl", "bootstrap", domain, str(PLIST_PATH)])
    run(["launchctl", "bootstrap", domain, str(SERVER_PLIST_PATH)])
    run(["launchctl", "enable", f"{domain}/{LABEL}"], check=False)
    run(["launchctl", "enable", f"{domain}/{SERVER_LABEL}"], check=False)
    run(["launchctl", "kickstart", "-k", f"{domain}/{LABEL}"], check=False)
    run(["launchctl", "kickstart", "-k", f"{domain}/{SERVER_LABEL}"], check=False)

    print(f"Installed {LABEL}")
    print(f"Installed {SERVER_LABEL}")
    print(f"Schedule: every day at 08:30")
    print("Source: Yahoo Taiwan revenue pages")
    print(f"Plist: {PLIST_PATH}")
    print(f"Server plist: {SERVER_PLIST_PATH}")
    print("Dashboard API: http://127.0.0.1:8765")
    print(f"Logs: {LOG_DIR}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(exc.stderr or exc.stdout or str(exc), file=sys.stderr)
        raise SystemExit(exc.returncode or 1)
