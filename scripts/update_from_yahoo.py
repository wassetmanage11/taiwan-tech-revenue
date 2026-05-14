#!/usr/bin/env python3
"""Sync the Taiwan tech revenue dashboard from Yahoo Taiwan monthly revenue pages."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HTML = PROJECT_ROOT / "index.html"
YAHOO_URL = "https://tw.stock.yahoo.com/quote/{ticker}.TW/revenue"
CUSTOM_CATEGORY = "관심 Company"
EXCHANGE_COMPANY_APIS = [
    {
        "url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
        "code_key": "公司代號",
        "english_key": "英文簡稱",
    },
    {
        "url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O",
        "code_key": "SecuritiesCompanyCode",
        "english_key": "Symbol",
    },
    {
        "url": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_R",
        "code_key": "SecuritiesCompanyCode",
        "english_key": "Symbol",
    },
]
EXCHANGE_COMPANY_CACHE: dict[str, list[dict]] = {}
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8,ko;q=0.7",
}


TICKERS = {
    "TSMC": "2330",
    "Hon Hai(Foxconn)": "2317",
    "Inventec": "2356",
    "Nanya": "2408",
    "Innodisk": "5289",
    "ADATA": "3260",
    "Apacer": "8271",
    "Transcend": "2451",
    "UMC": "2303",
    "TUC": "6274",
    "ITEQ": "6213",
    "EMC": "2383",
    "Topoint": "8021",
    "Aspeed": "5274",
    "Accton": "2345",
    "Kinsus": "3189",
    "Unimicron": "3037",
    "Gold Circuit": "2368",
    "Dynamic Electronics": "3715",
    "Nanya PCB": "8046",
    "Asia Vital Components": "3017",
    "Auras Technology": "3324",
    "Kaori Heat": "8996",
    "King Slide Works": "2059",
    "Quanta": "2382",
    "Wiwynn": "6669",
    "Wistron": "3231",
    "Yageo": "2327",
    "Winway": "6515",
    "Fositek": "6805",
    "King Yuan": "2449",
    "Fulltech": "1815",
    "Co-Tech": "8358",
    "Jentech": "3653",
    "Delta Electronics": "2308",
    "Winbond": "2344",
    "Phison": "8299",
    "Landmark Opto": "3081",
    "VPEC": "2455",
    "Browave": "3163",
    "Win Semiconductor": "3105",
    "MPI": "6223",
    "SunoWealth": "2421",
    "Grand Process Tech": "3131",
}

AGGREGATES = {
    "Server ODM Total": ["Hon Hai(Foxconn)", "Wistron", "Wiwynn", "Quanta", "Inventec"],
    "CCL Total": ["TUC", "ITEQ", "EMC"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="Path to index.html.")
    parser.add_argument("--companies-json", type=Path, help="Optional custom company list JSON.")
    parser.add_argument("--workers", type=int, default=4, help="Number of concurrent Yahoo fetches.")
    parser.add_argument("--timeout", type=float, default=20, help="Per-request timeout in seconds.")
    parser.add_argument("--retries", type=int, default=2, help="Retries per ticker after the first attempt.")
    parser.add_argument("--quiet", action="store_true", help="Only print errors and warnings.")
    return parser.parse_args()


def period_key(period: str) -> tuple[int, int]:
    year, month = period.split("/")
    return int(year), int(month)


def yahoo_period_to_dashboard_period(period: str) -> str:
    year, month = period.split("/")
    return f"{int(year) % 100:02d}/{month}"


def parse_revenue_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value) / 1000, 1)
    text = str(value).replace(",", "").strip()
    if not text or text in {"-", "--", "N/A"}:
        return None
    return round(float(text) / 1000, 1)


def normalize_ticker(value: object) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"\.(TW|TWO)$", "", text)
    if not re.fullmatch(r"\d{4,6}", text):
        raise ValueError("ticker must be 4-6 digits, optionally ending in .TW")
    return text


def normalize_company_entry(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError("custom company entry must be an object")
    ticker = normalize_ticker(raw.get("ticker"))
    name = str(raw.get("name") or ticker).strip()
    category = str(raw.get("category") or CUSTOM_CATEGORY).strip()
    if not name:
        name = ticker
    if len(name) > 80:
        raise ValueError("company name is too long")
    if len(category) > 40:
        raise ValueError("category name is too long")
    return {"name": name, "ticker": ticker, "category": category}


def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def should_infer_company_name(entry: dict[str, str]) -> bool:
    return entry["name"] == entry["ticker"] or has_cjk(entry["name"])


def load_custom_companies(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists():
        return []
    raw = json.loads(path.read_text())
    entries = raw.get("companies", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(f"{path} must contain a companies list")

    return dedupe_custom_companies(normalize_company_entry(item) for item in entries)


def dedupe_custom_companies(entries: object) -> list[dict[str, str]]:
    custom: list[dict[str, str]] = []
    seen_names = set(TICKERS)
    seen_tickers = set(TICKERS.values())
    for item in entries:
        entry = normalize_company_entry(item)
        if entry["name"] in seen_names or entry["ticker"] in seen_tickers:
            continue
        seen_names.add(entry["name"])
        seen_tickers.add(entry["ticker"])
        custom.append(entry)
    return custom


def company_tickers(custom_companies: list[dict[str, str]]) -> dict[str, str]:
    tickers = dict(TICKERS)
    for entry in custom_companies:
        tickers[entry["name"]] = entry["ticker"]
    return tickers


def load_blob(html: str) -> tuple[dict, int, int]:
    marker = "var BLOB="
    start = html.index(marker) + len(marker)
    end = html.index(";\nvar D=BLOB.data", start)
    return json.loads(html[start:end]), start, end


def bracket_extract(text: str, start: int) -> str:
    if text[start] != "[":
        raise ValueError("monthly revenue data array not found")
    in_string = False
    escaped = False
    depth = 0

    for index, char in enumerate(text[start:], start):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("monthly revenue data array was not closed")


def fetch_ticker_page(ticker: str, timeout: float) -> str:
    request = Request(YAHOO_URL.format(ticker=normalize_ticker(ticker)), headers=HEADERS)
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def clean_english_company_name(value: object) -> str | None:
    name = re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip(" -\t\r\n")
    if not name or name in {"-", "--", "N/A", "None"}:
        return None
    return name


def fetch_exchange_company_rows(api: dict[str, str], timeout: float) -> list[dict]:
    if api["url"] not in EXCHANGE_COMPANY_CACHE:
        request = Request(api["url"], headers={"User-Agent": HEADERS["User-Agent"], "Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            EXCHANGE_COMPANY_CACHE[api["url"]] = json.loads(response.read().decode("utf-8-sig", "replace"))
    return EXCHANGE_COMPANY_CACHE[api["url"]]


def fetch_exchange_english_name(ticker: str, timeout: float) -> str | None:
    normalized = normalize_ticker(ticker)
    for api in EXCHANGE_COMPANY_APIS:
        rows = fetch_exchange_company_rows(api, timeout)
        for row in rows:
            if str(row.get(api["code_key"], "")).strip() == normalized:
                return clean_english_company_name(row.get(api["english_key"]))
    return None


def parse_yahoo_symbol_name(page_html: str) -> str | None:
    match = re.search(r'"symbolName"\s*:\s*"([^"]+)"', page_html)
    if match:
        value = match.group(1)
        if "\\u" in value:
            value = value.encode("utf-8").decode("unicode_escape")
        return value.strip()
    match = re.search(r"<title>([^<]+)</title>", page_html)
    if match:
        title = match.group(1).strip()
        name_match = re.match(r"(.+?)\(\d+\.TW\)", title)
        if name_match:
            return name_match.group(1).strip()
    return None


def resolve_custom_company_names(
    custom_companies: list[dict[str, str]],
    timeout: float,
    retries: int,
) -> list[dict[str, str]]:
    resolved: list[dict[str, str]] = []
    for entry in custom_companies:
        if not should_infer_company_name(entry):
            resolved.append(entry)
            continue

        current = dict(entry)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                inferred = fetch_exchange_english_name(entry["ticker"], timeout)
                if inferred:
                    current["name"] = inferred
                elif has_cjk(current["name"]):
                    current["name"] = current["ticker"]
                break
            except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
        else:
            print(f"warning: {entry['ticker']} company name lookup failed: {last_error}", file=sys.stderr)
            if has_cjk(current["name"]):
                current["name"] = current["ticker"]
        resolved.append(current)

    return dedupe_custom_companies(resolved)


def write_custom_companies(path: Path | None, custom_companies: list[dict[str, str]]) -> bool:
    if path is None or (not custom_companies and not path.exists()):
        return False
    payload = {"companies": custom_companies}
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def parse_yahoo_monthly_revenue(page_html: str) -> dict[str, float]:
    match = re.search(r'"revenueTable-[^"]+-month"\s*:\s*\{\s*"data"\s*:\s*', page_html)
    if not match:
        raise ValueError("monthly revenue data key not found")

    array_json = bracket_extract(page_html, match.end())
    rows = json.loads(array_json)
    parsed: dict[str, float] = {}
    for row in rows:
        period = row.get("date")
        revenue = parse_revenue_number(row.get("revenue"))
        if isinstance(period, str) and re.fullmatch(r"\d{4}/\d{2}", period) and revenue is not None:
            parsed[yahoo_period_to_dashboard_period(period)] = revenue
    if not parsed:
        raise ValueError("monthly revenue rows were empty")
    return parsed


def fetch_company_revenue(company: str, ticker: str, timeout: float, retries: int) -> tuple[str, dict[str, float]]:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            page = fetch_ticker_page(ticker, timeout)
            return company, parse_yahoo_monthly_revenue(page)
        except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"{company} ({ticker}) fetch failed: {last_error}") from last_error


def old_company_map(company_data: dict) -> dict[str, float]:
    return {
        period: revenue
        for period, revenue in zip(company_data["p"], company_data["r"])
        if revenue is not None
    }


def ordered_periods(revenue_maps: dict[str, dict[str, float]]) -> list[str]:
    periods = {period for rows in revenue_maps.values() for period in rows}
    return sorted(periods, key=period_key)


def recompute_series(periods: list[str], revenue: list[float | None]) -> tuple[list[float | None], list[float | None]]:
    by_period = dict(zip(periods, revenue))
    mom: list[float | None] = []
    yoy: list[float | None] = []

    for index, period in enumerate(periods):
        value = revenue[index]
        if value is None:
            mom.append(None)
            yoy.append(None)
            continue

        previous = revenue[index - 1] if index > 0 else None
        mom.append(round((value / previous - 1) * 100, 2) if previous not in (None, 0) else None)

        year, month = period.split("/")
        previous_year = f"{int(year) - 1:02d}/{month}"
        year_value = by_period.get(previous_year)
        yoy.append(round((value / year_value - 1) * 100, 2) if year_value not in (None, 0) else None)

    return mom, yoy


def aggregate_revenue(component_maps: list[dict[str, float]]) -> dict[str, float]:
    periods = sorted({period for rows in component_maps for period in rows}, key=period_key)
    output: dict[str, float] = {}
    for period in periods:
        values = [rows[period] for rows in component_maps if period in rows]
        if len(values) == len(component_maps):
            output[period] = round(sum(values), 1)
    return output


def fetch_all_revenue(args: argparse.Namespace, tickers: dict[str, str]) -> tuple[dict[str, dict[str, float]], list[str]]:
    fetched: dict[str, dict[str, float]] = {}
    warnings: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(fetch_company_revenue, company, ticker, args.timeout, args.retries): company
            for company, ticker in tickers.items()
        }
        for future in concurrent.futures.as_completed(futures):
            company = futures[future]
            try:
                name, rows = future.result()
                fetched[name] = rows
            except Exception as exc:
                warnings.append(str(exc))
                print(f"warning: {exc}", file=sys.stderr)

    if len(fetched) < max(1, len(tickers) // 2):
        raise RuntimeError(f"too few Yahoo revenue pages fetched successfully: {len(fetched)}/{len(tickers)}")
    return fetched, warnings


def remove_company_from_categories(cats: dict, company: str) -> None:
    for names in cats.values():
        while company in names:
            names.remove(company)


def sync_custom_categories(blob: dict, custom_companies: list[dict[str, str]]) -> None:
    cats = blob.setdefault("cats", {})
    previous_custom = set(blob.get("customCompanies", {}))
    current_custom = {entry["name"] for entry in custom_companies}

    for company in previous_custom - current_custom:
        blob["data"].pop(company, None)
        remove_company_from_categories(cats, company)

    for entry in custom_companies:
        remove_company_from_categories(cats, entry["name"])
        cats.setdefault(entry["category"], [])
        if entry["name"] not in cats[entry["category"]]:
            cats[entry["category"]].append(entry["name"])

    blob["customCompanies"] = {
        entry["name"]: {"ticker": entry["ticker"], "category": entry["category"]}
        for entry in custom_companies
    }


def sync_blob(
    blob: dict,
    fetched: dict[str, dict[str, float]],
    custom_companies: list[dict[str, str]],
) -> tuple[dict, str, list[str], list[str]]:
    sync_custom_categories(blob, custom_companies)
    company_order = list(blob["data"].keys())
    revenue_maps = {company: old_company_map(data) for company, data in blob["data"].items()}
    updated_companies = sorted(fetched)

    for company, rows in fetched.items():
        revenue_maps.setdefault(company, {})
        revenue_maps[company].update(rows)
        if company not in company_order and company not in AGGREGATES:
            company_order.append(company)

    for entry in custom_companies:
        if entry["name"] not in company_order:
            company_order.append(entry["name"])
            revenue_maps.setdefault(entry["name"], {})

    for aggregate, components in AGGREGATES.items():
        component_maps = [revenue_maps[component] for component in components if component in revenue_maps]
        if len(component_maps) == len(components):
            revenue_maps[aggregate] = aggregate_revenue(component_maps)
            updated_companies.append(aggregate)

    periods = ordered_periods({company: revenue_maps[company] for company in company_order})
    new_data = {}
    for company in company_order:
        old = blob["data"].get(company, {})
        rows = revenue_maps[company]
        revenue = [rows.get(period) for period in periods]
        mom, yoy = recompute_series(periods, revenue)
        new_data[company] = {
            "p": periods,
            "r": revenue,
            "mom": mom,
            "yoy": yoy,
            "wu": old.get("wu", "M"),
        }

    blob["data"] = new_data
    latest_period = max(
        (period for rows in revenue_maps.values() for period, value in rows.items() if value is not None),
        key=period_key,
    )

    twd_usd = blob["fx"].setdefault("twd_usd", {})
    usd_krw = blob["fx"].setdefault("usd_krw", {})
    if latest_period not in twd_usd:
        previous = max((p for p in twd_usd if period_key(p) <= period_key(latest_period)), key=period_key, default=None)
        if previous:
            twd_usd[latest_period] = twd_usd[previous]
    if latest_period not in usd_krw:
        previous = max((p for p in usd_krw if period_key(p) <= period_key(latest_period)), key=period_key, default=None)
        if previous:
            usd_krw[latest_period] = usd_krw[previous]

    missing_latest = [
        company
        for company in company_order
        if blob["data"][company]["r"][blob["data"][company]["p"].index(latest_period)] is None
    ]
    return blob, latest_period, sorted(set(updated_companies)), missing_latest


def write_blob(html_path: Path, blob: dict, start: int, end: int, original_html: str, latest_period: str) -> bool:
    next_html = original_html[:start] + json.dumps(blob, ensure_ascii=False, separators=(",", ":")) + original_html[end:]
    next_html = re.sub(r"\d+ Companies", f"{len(blob['data'])} Companies", next_html, count=1)
    next_html = re.sub(r"Updated ~\d{2}/\d{2}", f"Updated ~{latest_period}", next_html, count=1)
    if next_html == original_html:
        return False
    html_path.write_text(next_html)
    return True


def main() -> int:
    args = parse_args()
    if args.companies_json is None:
        args.companies_json = args.html.parent / "custom_companies.json"
    original_html = args.html.read_text()
    blob, start, end = load_blob(original_html)
    custom_companies = load_custom_companies(args.companies_json)
    custom_companies = resolve_custom_company_names(custom_companies, args.timeout, args.retries)
    custom_list_changed = write_custom_companies(args.companies_json, custom_companies)
    tickers = company_tickers(custom_companies)
    fetched, fetch_warnings = fetch_all_revenue(args, tickers)
    blob, latest_period, updated_companies, missing_latest = sync_blob(blob, fetched, custom_companies)
    blob["tickers"] = tickers
    changed = write_blob(args.html, blob, start, end, original_html, latest_period)

    if not args.quiet:
        print(f"Updated: {args.html}")
        print(f"Latest period: {latest_period}")
        print(f"Fetched companies: {len(fetched)}/{len(tickers)}")
        print(f"Custom companies: {len(custom_companies)}")
        print(f"Custom list changed: {custom_list_changed}")
        print(f"Updated BLOB companies: {len(updated_companies)}")
        print(f"Changed file: {changed}")
        if missing_latest:
            print(f"Companies without {latest_period}: {', '.join(missing_latest)}")
        if fetch_warnings:
            print(f"Warnings: {len(fetch_warnings)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"update_from_yahoo.py: {exc}", file=sys.stderr)
        raise SystemExit(1)
