#!/usr/bin/env python3
"""Install a daily macOS launchd job for the Taiwan tech revenue dashboard."""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.baggyeongmin.taiwan-tech-revenue"
SERVER_LABEL = "com.baggyeongmin.taiwan-tech-revenue.server"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
SERVER_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{SERVER_LABEL}.plist"
PYTHON = "/usr/bin/python3"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "TaiwanTechRevenue"
RUNTIME_REPO = APP_SUPPORT / "repo"
LAUNCHD_UPDATE_SCRIPT = RUNTIME_REPO / "scripts" / "update_from_yahoo.py"
LAUNCHD_SCHEDULED_UPDATE_SCRIPT = RUNTIME_REPO / "scripts" / "run_scheduled_update.py"
LAUNCHD_SERVER_SCRIPT = RUNTIME_REPO / "scripts" / "dashboard_server.py"
LAUNCHD_HTML = RUNTIME_REPO / "index.html"
CUSTOM_COMPANIES_JSON = RUNTIME_REPO / "custom_companies.json"
LOG_DIR = APP_SUPPORT / "logs"
INFO_HUB_ENV_FILE = Path.home() / "Desktop" / "info-hub" / ".env.local"
UPDATE_SCHEDULE = [{"Hour": hour, "Minute": 30} for hour in range(7, 19)]


def run(command: list[str], check: bool = True, cwd: Path = PROJECT_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, check=check, text=True, capture_output=True)


def telegram_environment() -> dict[str, str]:
    if not INFO_HUB_ENV_FILE.exists():
        return {}
    return {
        "TAIWAN_REVENUE_TELEGRAM_PROVIDER": "info-hub",
        "TAIWAN_REVENUE_INFO_HUB_ENV_FILE": str(INFO_HUB_ENV_FILE),
    }


def git(command: list[str], cwd: Path = PROJECT_ROOT, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *command], check=check, cwd=cwd)


def current_branch() -> str:
    branch = run(["git", "branch", "--show-current"]).stdout.strip()
    return branch or "main"


def origin_url() -> str:
    return run(["git", "config", "--get", "remote.origin.url"]).stdout.strip()


def sync_runtime_repo(branch: str) -> None:
    if not (RUNTIME_REPO / ".git").exists():
        if RUNTIME_REPO.exists() and any(RUNTIME_REPO.iterdir()):
            raise RuntimeError(f"{RUNTIME_REPO} exists but is not a git checkout")
        run(["git", "clone", origin_url(), str(RUNTIME_REPO)])
    git(["fetch", "origin"], cwd=RUNTIME_REPO)
    git(["checkout", branch], cwd=RUNTIME_REPO)
    git(["pull", "--ff-only", "origin", branch], cwd=RUNTIME_REPO)


def main() -> int:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(exist_ok=True)
    PLIST_PATH.parent.mkdir(exist_ok=True)
    branch = current_branch()
    sync_runtime_repo(branch)
    legacy_workbook = APP_SUPPORT / "source.xlsx"
    if legacy_workbook.exists():
        legacy_workbook.unlink()

    plist = {
        "Label": LABEL,
        "ProgramArguments": [
            PYTHON,
            str(LAUNCHD_SCHEDULED_UPDATE_SCRIPT),
            "--repo",
            str(RUNTIME_REPO),
            "--update-script",
            str(LAUNCHD_UPDATE_SCRIPT),
            "--html",
            str(LAUNCHD_HTML),
            "--companies-json",
            str(CUSTOM_COMPANIES_JSON),
            "--workers",
            "3",
            "--timeout",
            "30",
            "--retries",
            "3",
            "--quiet-update",
        ],
        "WorkingDirectory": str(RUNTIME_REPO),
        "StartCalendarInterval": UPDATE_SCHEDULE,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_DIR / "daily-update.log"),
        "StandardErrorPath": str(LOG_DIR / "daily-update.err.log"),
    }
    telegram_env = telegram_environment()
    if telegram_env:
        plist["EnvironmentVariables"] = telegram_env
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
    print("Schedule: every hour from 07:30 to 18:30")
    print("Source: Yahoo Taiwan revenue pages")
    print(f"Telegram: {'enabled' if telegram_env else 'not configured'}")
    print(f"Runtime repo: {RUNTIME_REPO}")
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
