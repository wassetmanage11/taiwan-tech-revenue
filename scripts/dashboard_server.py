#!/usr/bin/env python3
"""Local API used by the dashboard to add custom Taiwan revenue tickers."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote


APP_SUPPORT = Path.home() / "Library" / "Application Support" / "TaiwanTechRevenue"
DEFAULT_HTML = APP_SUPPORT / "index.html"
DEFAULT_COMPANIES_JSON = APP_SUPPORT / "custom_companies.json"
DEFAULT_UPDATE_SCRIPT = APP_SUPPORT / "update_from_yahoo.py"
PYTHON = "/usr/bin/python3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML)
    parser.add_argument("--companies-json", type=Path, default=DEFAULT_COMPANIES_JSON)
    parser.add_argument("--update-script", type=Path, default=DEFAULT_UPDATE_SCRIPT)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--retries", type=int, default=1)
    return parser.parse_args()


class DashboardState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        sys.path.insert(0, str(args.update_script.parent))
        import update_from_yahoo

        self.updater = update_from_yahoo
        args.companies_json.parent.mkdir(parents=True, exist_ok=True)
        if not args.companies_json.exists():
            self.write_companies([])

    def read_companies(self) -> list[dict[str, str]]:
        if not self.args.companies_json.exists():
            return []
        raw = json.loads(self.args.companies_json.read_text())
        entries = raw.get("companies", raw) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            return []
        companies: list[dict[str, str]] = []
        for item in entries:
            companies.append(self.updater.normalize_company_entry(item))
        return companies

    def write_companies(self, companies: list[dict[str, str]]) -> None:
        payload = {"companies": companies}
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp_path = self.args.companies_json.with_suffix(".tmp")
        tmp_path.write_text(text)
        tmp_path.replace(self.args.companies_json)

    def validate_payload(self, payload: dict, entry: dict[str, str]) -> dict[str, str]:
        if not payload.get("name"):
            inferred = self.updater.fetch_exchange_english_name(entry["ticker"], self.args.timeout)
            if inferred:
                entry["name"] = inferred
        self.updater.fetch_company_revenue(
            entry["name"],
            entry["ticker"],
            self.args.timeout,
            self.args.retries,
        )
        return entry

    def refresh_dashboard(self) -> None:
        subprocess.run(
            [
                PYTHON,
                str(self.args.update_script),
                "--html",
                str(self.args.html),
                "--companies-json",
                str(self.args.companies_json),
                "--quiet",
            ],
            check=True,
            text=True,
            capture_output=True,
        )

    def add_company(self, payload: dict) -> dict:
        with self.lock:
            entry = self.updater.normalize_company_entry(payload)
            companies = self.read_companies()
            base_names = set(self.updater.TICKERS)
            base_tickers = set(self.updater.TICKERS.values())
            names = {company["name"] for company in companies}
            tickers = {company["ticker"] for company in companies}

            if entry["name"] in base_names or entry["ticker"] in base_tickers:
                return {"ok": False, "status": 409, "error": "이미 대시보드에 있는 회사입니다."}
            if entry["name"] in names or entry["ticker"] in tickers:
                return {"ok": False, "status": 409, "error": "이미 추가된 회사입니다."}

            entry = self.validate_payload(payload, entry)
            if entry["name"] in names:
                return {"ok": False, "status": 409, "error": "이미 추가된 회사입니다."}
            companies.append(entry)
            self.write_companies(companies)
            self.refresh_dashboard()
            return {"ok": True, "company": entry, "companies": companies}

    def remove_company(self, ticker: str) -> dict:
        with self.lock:
            normalized = self.updater.normalize_ticker(ticker)
            companies = self.read_companies()
            next_companies = [company for company in companies if company["ticker"] != normalized]
            if len(next_companies) == len(companies):
                return {"ok": False, "status": 404, "error": "추가된 회사를 찾을 수 없습니다."}
            self.write_companies(next_companies)
            self.refresh_dashboard()
            return {"ok": True, "companies": next_companies}


def build_handler(state: DashboardState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "TaiwanTechRevenueLocal/1.0"

        def end_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            super().end_headers()

        def write_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self) -> None:
            self.write_json(204, {})

        def do_GET(self) -> None:
            if self.path != "/api/companies":
                self.write_json(404, {"ok": False, "error": "not found"})
                return
            try:
                self.write_json(200, {"ok": True, "companies": state.read_companies()})
            except Exception as exc:
                self.write_json(500, {"ok": False, "error": str(exc)})

        def do_POST(self) -> None:
            if self.path != "/api/companies":
                self.write_json(404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                result = state.add_company(payload)
                self.write_json(result.get("status", 200), result)
            except subprocess.CalledProcessError as exc:
                error = exc.stderr or exc.stdout or str(exc)
                self.write_json(500, {"ok": False, "error": error.strip()})
            except Exception as exc:
                self.write_json(400, {"ok": False, "error": str(exc)})

        def do_DELETE(self) -> None:
            prefix = "/api/companies/"
            if not self.path.startswith(prefix):
                self.write_json(404, {"ok": False, "error": "not found"})
                return
            try:
                ticker = unquote(self.path[len(prefix) :])
                result = state.remove_company(ticker)
                self.write_json(result.get("status", 200), result)
            except subprocess.CalledProcessError as exc:
                error = exc.stderr or exc.stdout or str(exc)
                self.write_json(500, {"ok": False, "error": error.strip()})
            except Exception as exc:
                self.write_json(400, {"ok": False, "error": str(exc)})

        def log_message(self, format: str, *args: object) -> None:
            print(f"{self.address_string()} - {format % args}")

    return Handler


def main() -> int:
    args = parse_args()
    state = DashboardState(args)
    server = ThreadingHTTPServer((args.host, args.port), build_handler(state))
    print(f"Serving dashboard API on http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
