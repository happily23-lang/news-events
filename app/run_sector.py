#!/usr/bin/env python3
"""Refresh KRX sector classification cache (weekly cron).

Scrapes Naver Finance 업종 listing pages to obtain a code → sector mapping
for KOSPI/KOSDAQ stocks. Naver Finance uses 79 sector groups (한국 거래소
표준업종을 기반으로 한 분류) and exposes constituent lists publicly without
authentication, which is why we use it instead of pykrx (which requires
KRX login as of late 2025).
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup


CACHE_PATH = str(Path(__file__).parent / "krx_sector_cache.json")
NAVER_BASE = "https://finance.naver.com"
SECTOR_LIST_URL = f"{NAVER_BASE}/sise/sise_group.naver?type=upjong"
SECTOR_DETAIL_URL = f"{NAVER_BASE}/sise/sise_group_detail.naver?type=upjong&no={{no}}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
REQUEST_DELAY_SEC = 0.6  # 79 sectors × 0.6s = ~50s, polite to Naver
TIMEOUT = 15


def _fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.encoding = "euc-kr"
    resp.raise_for_status()
    return resp.text


def _parse_sector_list(html: str) -> list[tuple[str, int]]:
    """Return list of (sector_name, sector_no) parsed from the index page."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="sise_group_detail"][href*="upjong"]'):
        href = a.get("href", "")
        name = a.get_text(strip=True)
        m = re.search(r"no=(\d+)", href)
        if not (name and m):
            continue
        if name in seen or name == "더보기" or "동일업종" in name:
            continue
        seen.add(name)
        out.append((name, int(m.group(1))))
    return out


def _parse_sector_constituents(html: str) -> list[tuple[str, str]]:
    """Return list of (code, name) for stocks under a sector page."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for a in soup.select('a[href*="item/main"]'):
        href = a.get("href", "")
        m = re.search(r"code=(\d{6})", href)
        name = a.get_text(strip=True)
        if not (m and name):
            continue
        code = m.group(1)
        if code in seen:
            continue
        seen.add(code)
        out.append((code, name))
    return out


def fetch_sector_mapping() -> dict[str, dict]:
    """Crawl Naver Finance sector pages and return {code: {sector, market}}.

    Market (KOSPI/KOSDAQ) cannot be inferred from the sector page alone.
    We default to "" and let downstream code resolve it from the existing
    KRX listings (which carries the Market column).
    """
    print(f"  fetching sector list from {SECTOR_LIST_URL}")
    list_html = _fetch(SECTOR_LIST_URL)
    sectors = _parse_sector_list(list_html)
    print(f"  {len(sectors)} sectors found")

    mapping: dict[str, dict] = {}
    for idx, (sector_name, sector_no) in enumerate(sectors, 1):
        url = SECTOR_DETAIL_URL.format(no=sector_no)
        try:
            html = _fetch(url)
        except Exception as exc:
            print(f"  [{idx}/{len(sectors)}] {sector_name} FAILED: {exc}", file=sys.stderr)
            continue
        constituents = _parse_sector_constituents(html)
        for code, _name in constituents:
            # If a code is already in another sector (rare), keep the first hit.
            if code in mapping:
                continue
            mapping[code] = {"sector": sector_name, "market": ""}
        print(f"  [{idx}/{len(sectors)}] {sector_name}: {len(constituents)} stocks")
        time.sleep(REQUEST_DELAY_SEC)

    return mapping


def main() -> int:
    print("Refreshing Naver-Finance sector classification...")
    mapping = fetch_sector_mapping()
    print(f"  total mapped tickers: {len(mapping)}")
    data = {
        "_meta": {
            "refreshed_at": datetime.now(timezone.utc).isoformat(),
            "source": "naver_finance",
            "sector_count": len({v["sector"] for v in mapping.values()}),
        },
        **mapping,
    }
    Path(CACHE_PATH).write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"  wrote {CACHE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
