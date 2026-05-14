#!/usr/bin/env python3
"""Add a custom company request from a GitHub issue event."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import update_from_yahoo


MARKER = "taiwan-tech-revenue:add-company"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-json", type=Path, required=True)
    parser.add_argument("--companies-json", type=Path, default=Path("custom_companies.json"))
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=2)
    return parser.parse_args()


def issue_body(path: Path) -> str:
    event = json.loads(path.read_text())
    return str(event.get("issue", {}).get("body") or "")


def read_field(body: str, *names: str) -> str:
    for name in names:
        pattern = rf"(?im)^\s*(?:[-*]\s*)?{re.escape(name)}\s*:\s*(.*?)\s*$"
        match = re.search(pattern, body)
        if match:
            return match.group(1).strip().strip("`")
    return ""


def reject_unsafe_text(entry: dict[str, str]) -> None:
    for label in ("name", "category"):
        if any(char in entry[label] for char in "<>"):
            raise ValueError(f"{label} must not contain angle brackets")


def is_duplicate(entry: dict[str, str], companies: list[dict[str, str]]) -> str | None:
    base_names = set(update_from_yahoo.TICKERS)
    base_tickers = set(update_from_yahoo.TICKERS.values())
    if entry["name"] in base_names or entry["ticker"] in base_tickers:
        return "dashboard"

    existing_names = {company["name"] for company in companies}
    existing_tickers = {company["ticker"] for company in companies}
    if entry["name"] in existing_names or entry["ticker"] in existing_tickers:
        return "custom_companies.json"

    return None


def main() -> int:
    args = parse_args()
    body = issue_body(args.event_json)
    if MARKER not in body:
        raise ValueError("issue is missing add-company marker")

    ticker = read_field(body, "ticker", "티커")
    name = read_field(body, "name", "company", "회사명")
    category = read_field(body, "category", "카테고리") or update_from_yahoo.CUSTOM_CATEGORY

    entry = update_from_yahoo.normalize_company_entry(
        {"ticker": ticker, "name": name or ticker, "category": category}
    )
    reject_unsafe_text(entry)

    companies = update_from_yahoo.load_custom_companies(args.companies_json)
    duplicate = is_duplicate(entry, companies)
    if duplicate:
        print(f"{entry['ticker']} is already in {duplicate}.")
        return 0

    if not name:
        try:
            inferred = update_from_yahoo.fetch_exchange_english_name(entry["ticker"], args.timeout)
        except Exception as exc:
            print(f"warning: company name lookup failed: {exc}")
        else:
            if inferred:
                entry["name"] = inferred
                reject_unsafe_text(entry)

    duplicate = is_duplicate(entry, companies)
    if duplicate:
        print(f"{entry['ticker']} is already in {duplicate}.")
        return 0

    update_from_yahoo.fetch_company_revenue(entry["name"], entry["ticker"], args.timeout, args.retries)
    companies.append(entry)
    update_from_yahoo.write_custom_companies(args.companies_json, companies)
    print(f"Added {entry['ticker']} to custom_companies.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
