"""
NAVER 금융 종목별 외국인·기관 매매동향 수집.

KRX(MDCSTAT02302 등) 가 봇 차단으로 막혀 있어 NAVER 의
`finance.naver.com/item/frgn.naver?code={code}` 표를 직접 파싱한다.

캐시: 같은 날 같은 종목은 디스크 캐시(naver_supply_cache.json)에서 즉시 반환.
"""

import json
import os
import re
import time
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup


_URL = "https://finance.naver.com/item/frgn.naver?code={code}"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0",
    "Referer": "https://finance.naver.com/",
}

_CACHE_PATH = os.path.join(os.path.dirname(__file__), "naver_supply_cache.json")
_REQUEST_DELAY_SEC = 0.25  # 호출 간격
_session = requests.Session()


def _load_cache() -> dict:
    if not os.path.exists(_CACHE_PATH):
        return {}
    try:
        with open(_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    except OSError:
        pass


_cache = _load_cache()


def _parse_int(s: str) -> Optional[int]:
    s = s.replace(",", "").replace("+", "").strip()
    if not s or s in ("-", "N/A"):
        return None
    try:
        return int(s)
    except ValueError:
        # '+12,345' 같은 +- 부호 처리는 위에서 + 만 제거함. 음수는 그대로.
        m = re.match(r"^-?\d+$", s)
        return int(s) if m else None


def _scrape(code: str) -> Optional[list[dict]]:
    """
    종목 코드의 최근 일자별 외국인·기관 매매 행 리스트.
    각 행: {date, close, change_pct, volume, institution_net, foreign_net,
            foreign_holding, foreign_ratio}
    실패 시 None.
    """
    try:
        r = _session.get(_URL.format(code=code), headers=_HEADERS, timeout=10)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.select("table.type2")
    if len(tables) < 2:
        return None

    rows: list[dict] = []
    for tr in tables[1].find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        # 데이터 행은 9칸: 날짜·종가·전일비·등락률·거래량·기관순매매·외국인순매매·보유주수·보유율
        if len(cells) < 9:
            continue
        d_str = cells[0]
        if not re.match(r"^\d{4}\.\d{2}\.\d{2}$", d_str):
            continue
        rows.append({
            "date": d_str.replace(".", "-"),
            "close": _parse_int(cells[1]),
            "change_pct_raw": cells[3],  # "+3.22%" 형태 그대로
            "volume": _parse_int(cells[4]),
            "institution_net": _parse_int(cells[5]),  # 주식 수
            "foreign_net": _parse_int(cells[6]),       # 주식 수
            "foreign_holding": _parse_int(cells[7]),
            "foreign_ratio": cells[8],                 # "49.16%" 형태
        })
    return rows or None


def fetch_supply_flow(code: str, days: int = 5,
                      use_cache: bool = True) -> Optional[dict]:
    """
    종목의 최근 `days` 영업일 외국인·기관 누적 순매수.
    반환: {
      "foreign_net_shares": int (최근 days 일 합),
      "institution_net_shares": int,
      "foreign_net_value": int (원, close × shares 추정),
      "institution_net_value": int,
      "rows": [...최근 days 행],
      "foreign_ratio_latest": "49.16%" or None,
      "as_of": YYYY-MM-DD,
    }
    실패 시 None.
    """
    if not code:
        return None
    today_iso = date.today().isoformat()
    cache_key = f"{code}|{today_iso}|{days}"
    if use_cache and cache_key in _cache:
        return _cache[cache_key]

    rows = _scrape(code)
    if _REQUEST_DELAY_SEC > 0:
        time.sleep(_REQUEST_DELAY_SEC)
    if not rows:
        return None

    recent = rows[:days]
    f_shares = sum(r["foreign_net"] for r in recent if r.get("foreign_net") is not None)
    i_shares = sum(r["institution_net"] for r in recent if r.get("institution_net") is not None)
    # 거래대금 환산 — 일자별 종가 × 일자별 순매수 합
    f_value = sum(
        (r["foreign_net"] * r["close"])
        for r in recent
        if r.get("foreign_net") is not None and r.get("close")
    )
    i_value = sum(
        (r["institution_net"] * r["close"])
        for r in recent
        if r.get("institution_net") is not None and r.get("close")
    )

    result = {
        "foreign_net_shares": f_shares,
        "institution_net_shares": i_shares,
        "foreign_net_value": f_value,
        "institution_net_value": i_value,
        "rows": recent,
        "foreign_ratio_latest": recent[0].get("foreign_ratio") if recent else None,
        "as_of": recent[0].get("date") if recent else today_iso,
    }
    _cache[cache_key] = result
    _save_cache(_cache)
    return result


def enrich_stocks_with_supply(stocks: list[dict],
                              days: int = 5,
                              max_calls: int = 60) -> list[dict]:
    """
    stocks 의 각 항목 (`code` 키 필요) 에 supply 정보를 in-place 로 첨부.
    같은 코드는 한 번만 호출. max_calls 초과시 그 이후는 스킵.
    """
    seen: dict[str, Optional[dict]] = {}
    calls = 0
    for s in stocks:
        code = s.get("code")
        if not code:
            continue
        if code in seen:
            supply = seen[code]
        else:
            if calls >= max_calls:
                supply = None
            else:
                supply = fetch_supply_flow(code, days=days)
                calls += 1
            seen[code] = supply
        if supply:
            s["supply"] = supply
    return stocks


def _format_won(value: int) -> str:
    """원 단위 정수 → '+120억' / '-3.5억' / '+450만' 같은 콤팩트 라벨."""
    if value == 0:
        return "0"
    sign = "+" if value > 0 else "-"
    abs_v = abs(value)
    if abs_v >= 100_000_000:
        n = abs_v / 100_000_000
        return f"{sign}{n:.0f}억" if n >= 10 else f"{sign}{n:.1f}억"
    if abs_v >= 10_000:
        n = abs_v / 10_000
        return f"{sign}{n:.0f}만" if n >= 100 else f"{sign}{n:.1f}만"
    return f"{sign}{abs_v}"


def supply_badge_html(supply: dict, days: int = 5) -> str:
    """
    공급 데이터를 작은 인라인 배지 HTML 로 변환.
    외국인·기관 둘 다 표시 (값이 0이 아닐 때).
    """
    if not supply:
        return ""
    parts = []
    f = supply.get("foreign_net_value") or 0
    i = supply.get("institution_net_value") or 0
    if f != 0:
        cls = "buy" if f > 0 else "sell"
        parts.append(f'<span class="supply-tag supply-{cls}" title="외국인 {days}일 순매수">외 {_format_won(f)}</span>')
    if i != 0:
        cls = "buy" if i > 0 else "sell"
        parts.append(f'<span class="supply-tag supply-{cls}" title="기관 {days}일 순매수">기 {_format_won(i)}</span>')
    return "".join(parts)


# 배지용 공통 CSS — 두 페이지에서 import 해서 <style> 에 끼워넣음
SUPPLY_CSS = """
  .supply-tag {
    font-size:10px; font-weight:700;
    padding:1px 5px; border-radius:4px; margin-left:3px;
    white-space:nowrap; cursor:help;
  }
  .supply-buy { background:#fdecea; color:#c0392b; }
  .supply-sell { background:#e8f2ff; color:#1565c0; }
"""
