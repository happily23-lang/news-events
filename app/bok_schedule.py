"""
한국은행 금융통화위원회 일정 + 통계청 공표일정 스크레이퍼.

calendar_page.get_hardcoded_macro_events 의 drop-in 대체로 동작.
- BOK 금통위: live 스크레이프 (URL: crncyPolicyDrcMtg/listYear.do)
- 통계청 공표일정: live 페이지에서 안정 추출이 안 돼 monthly pattern 기반 hardcoded fallback
- 둘 다 실패 / 0건이면 calendar_page._MACRO_EVENTS_2026 으로 fallback
- TTL 30일 캐시 (`bok_schedule_cache.json`), dart_disclosure 캐시 패턴 그대로 미러
"""

import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup


log = logging.getLogger(__name__)


CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bok_schedule_cache.json")
CACHE_TTL_DAYS = 30

BOK_MPC_URL = "https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do"
BOK_MPC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
BOK_MPC_TIMEOUT = 15


# ============================================================
# 카테고리/지표 매핑 (단위 테스트 가능하도록 모듈 상수)
# ============================================================

# (indicator_id, category_hints, title) — 통계청 월별 공표일정 fallback 용
# 실제 발표일은 매월 변동되므로 _kostat_monthly_fallback() 가 월별 추정일 산출.
KOSTAT_MONTHLY_PATTERN: list[dict] = [
    # 매월 1~3일: 소비자물가동향 (전월 분)
    {"indicator": "cpi", "category_hints": ["fomc", "fx"],
     "title": "통계청 소비자물가동향 발표", "day_of_month": 2,
     "body": "전월 소비자물가지수(CPI) 공표. 인플레이션 둔화/재반등 방향이 통화정책에 직접 영향."},
    # 매월 중순: 고용동향
    {"indicator": "employment", "category_hints": ["fomc"],
     "title": "통계청 고용동향 발표", "day_of_month": 14,
     "body": "전월 고용동향 공표. 실업률·취업자 수 변동이 노동시장 강도 시그널."},
    # 매월 말: 산업활동동향
    {"indicator": "ip", "category_hints": ["fomc"],
     "title": "통계청 산업활동동향 발표", "day_of_month": 30,
     "body": "전월 산업활동동향 공표. 광공업생산·서비스업생산·소매판매 종합 지표."},
]

# canonical_title 매핑 — dedupe 키 생성. 스크레이프와 hardcoded 가 같은 행사를 가리킬 때 충돌 방지.
# 순서 중요: 더 구체적인 패턴(미국/한국 구분)을 먼저 두고 모호한 fallback 은 뒤에.
_CANONICAL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"한국은행.*금융통화위원회|금통위|통화정책방향"), "bok_mpc"),
    (re.compile(r"Fed.*FOMC|FOMC|연방공개시장위원회"), "fed_fomc"),
    (re.compile(r"(미국|US|미).{0,4}(소비자물가|CPI)"), "us_cpi"),
    (re.compile(r"(통계청|한국).{0,6}(소비자물가|CPI)"), "kr_cpi"),
    (re.compile(r"소비자물가|CPI"), "cpi"),  # 출처 모호한 fallback
    (re.compile(r"고용동향"), "employment"),
    (re.compile(r"산업활동동향"), "ip"),
    (re.compile(r"국제수지"), "bop"),
]


def _canonical_title(title: str) -> str:
    """타이틀 → canonical 토큰. 매칭 안 되면 원문 lowercase 정규화."""
    for pat, token in _CANONICAL_PATTERNS:
        if pat.search(title):
            return token
    return re.sub(r"\s+", "", title).lower()


# ============================================================
# 캐시 I/O — dart_disclosure 패턴 미러
# ============================================================

def _empty_cache() -> dict:
    return {"version": 1, "by_source": {}}


def _load_cache() -> dict:
    if not os.path.exists(CACHE_PATH):
        return _empty_cache()
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_cache()
    if not isinstance(data, dict):
        return _empty_cache()
    data.setdefault("version", 1)
    data.setdefault("by_source", {})
    return data


def _save_cache(cache: dict) -> None:
    cache_dir = os.path.dirname(CACHE_PATH) or "."
    os.makedirs(cache_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".bok_schedule_cache_", dir=cache_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CACHE_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _is_fresh(fetched_at: Optional[str], ttl_days: int = CACHE_TTL_DAYS) -> bool:
    if not fetched_at:
        return False
    try:
        d = date.fromisoformat(fetched_at)
    except ValueError:
        return False
    return (date.today() - d) <= timedelta(days=ttl_days)


# ============================================================
# 한국은행 금통위 스크레이퍼
# ============================================================

# 페이지 셀 포맷은 'MM월 DD일(요일)' 이다. 셀에 연도가 없으므로 함수 인자 year 와
# 페이지 표시 연도(`YYYY년 ...` 빈도로 추론)가 일치할 때만 채택해 다른 연도 오인 방지.
_KOREAN_DATE_RE = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_YEAR_MENTION_RE = re.compile(r"\b(20\d{2})\s*년")


def _detect_page_year(html: str) -> Optional[int]:
    """본문에서 가장 자주 언급된 'YYYY년' 추출. 페이지가 어느 연도를 보여주는지 추정."""
    counts: dict[int, int] = {}
    for m in _YEAR_MENTION_RE.finditer(html):
        y = int(m.group(1))
        counts[y] = counts.get(y, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def _parse_bok_mpc_html(html: str, year: int) -> list[dict]:
    """HTML 에서 해당 year 의 회의일자 추출.

    셀 포맷이 'MM월 DD일(목)' 이라 셀에는 연도가 없다.
    페이지의 표시 연도가 요청 연도와 다르면 (다른 연도를 잘못 채취 위험) 빈 리스트.
    """
    page_year = _detect_page_year(html)
    if page_year is None or page_year != year:
        log.info("BOK MPC page year=%s != requested=%s — skip parse", page_year, year)
        return []

    soup = BeautifulSoup(html, "html.parser")
    found: set[date] = set()

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            # 회의일자 칼럼은 첫번째 셀. 헤더(td 가 아닌 th 또는 '회의일자' 텍스트)는 skip
            first = cells[0]
            text = first.get_text(" ", strip=True)
            if not text or "회의일자" in text:
                continue
            m = _KOREAN_DATE_RE.search(text)
            if not m:
                continue
            mo, d = int(m.group(1)), int(m.group(2))
            try:
                found.add(date(year, mo, d))
            except ValueError:
                continue

    out: list[dict] = []
    for d in sorted(found):
        out.append({
            "type": "MACRO",
            "event_date": d.isoformat(),
            "title": "한국은행 금융통화위원회",
            "body_snippet": "한은 금통위 통화정책방향 결정회의. 기준금리 결정·총재 기자간담회. 국내 금융/부동산/건설 섹터 영향.",
            "source_url": BOK_MPC_URL,
            "source_label": "한국은행 일정",
            "category_hints": ["fomc"],
            "direction": "neutral",
            "_origin": "scraper",
            "_indicator": "base_rate",
        })
    return out


def fetch_bok_mpc_schedule(year: int, *, force_refresh: bool = False,
                           _session: Optional[requests.Session] = None) -> list[dict]:
    """한은 금통위 일정 페이지를 스크레이프해 해당 year 의 MACRO 이벤트 리스트 반환.

    실패 시 캐시 사용 가능하면 캐시, 아니면 빈 리스트.
    예외는 raise 하지 않는다 (calendar 파이프라인 무중단).
    """
    cache = _load_cache()
    cache_key = f"bok_mpc_{year}"
    cached = cache["by_source"].get(cache_key)
    if not force_refresh and cached and _is_fresh(cached.get("fetched_at")):
        return cached.get("events", [])

    sess = _session or requests
    try:
        r = sess.get(BOK_MPC_URL, params={"mtgSe": "A", "menuNo": "200755"},
                     headers=BOK_MPC_HEADERS, timeout=BOK_MPC_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("BOK MPC fetch failed: %s", exc)
        return cached.get("events", []) if cached else []

    if r.status_code != 200:
        log.warning("BOK MPC HTTP %s", r.status_code)
        return cached.get("events", []) if cached else []

    events = _parse_bok_mpc_html(r.text, year)
    if not events:
        log.warning("BOK MPC parse returned 0 events for %s", year)
        return cached.get("events", []) if cached else []

    cache["by_source"][cache_key] = {
        "fetched_at": date.today().isoformat(),
        "events": events,
    }
    try:
        _save_cache(cache)
    except OSError as exc:
        log.warning("BOK cache save failed: %s", exc)
    return events


# ============================================================
# 통계청 공표일정 — monthly pattern fallback
# ============================================================

def _kostat_monthly_fallback(year: int) -> list[dict]:
    """통계청 발표는 라이브 캘린더 추출이 불안정해 monthly pattern 으로 추정.

    각 항목은 day_of_month 에 발표 (휴일이면 다음 영업일이지만 그 정확도까지는 불요).
    실제 발표 페이지에서 확정 일정 가져오는 스크레이퍼는 V5 확정 후 추가.
    """
    out: list[dict] = []
    for month in range(1, 13):
        for spec in KOSTAT_MONTHLY_PATTERN:
            day = spec["day_of_month"]
            try:
                ev_date = date(year, month, min(day, 28))
            except ValueError:
                continue
            out.append({
                "type": "MACRO",
                "event_date": ev_date.isoformat(),
                "title": spec["title"],
                "body_snippet": spec["body"],
                "source_url": "https://kostat.go.kr/menu.es?mid=a10301010000",
                "source_label": "통계청 공표일정 (예상)",
                "category_hints": list(spec["category_hints"]),
                "direction": "neutral",
                "_origin": "kostat_pattern",
                "_indicator": spec["indicator"],
            })
    return out


def fetch_kostat_release_schedule(year: int, *, force_refresh: bool = False) -> list[dict]:
    """통계청 공표일정. 현재는 monthly pattern fallback 만 반환.

    추후 V5 확정 시 실제 페이지 스크레이프로 교체 예정. 인터페이스는 호환 유지.
    """
    cache = _load_cache()
    cache_key = f"kostat_{year}"
    cached = cache["by_source"].get(cache_key)
    if not force_refresh and cached and _is_fresh(cached.get("fetched_at")):
        return cached.get("events", [])

    events = _kostat_monthly_fallback(year)
    cache["by_source"][cache_key] = {
        "fetched_at": date.today().isoformat(),
        "events": events,
    }
    try:
        _save_cache(cache)
    except OSError:
        pass
    return events


# ============================================================
# 공개 API — calendar_page.get_hardcoded_macro_events 의 drop-in 대체
# ============================================================

def get_macro_events(today: Optional[date] = None,
                     window_days: int = 30) -> list[dict]:
    """오늘 ~ today+window_days 범위의 MACRO 이벤트 리스트.

    소스 우선순위 (dedupe 시 앞쪽 우선):
    1. 한은 금통위 스크레이프 (`fetch_bok_mpc_schedule`)
    2. 통계청 monthly pattern (`fetch_kostat_release_schedule`)
    3. calendar_page._MACRO_EVENTS_2026 하드코딩 (fallback)

    스크레이프 실패해도 fallback 으로 항상 무엇인가 반환.
    """
    if today is None:
        today = date.today()
    cutoff = today + timedelta(days=window_days)

    # 윈도가 연도 경계 넘으면 다음 연도도 함께 가져온다.
    years = [today.year]
    if cutoff.year != today.year:
        years.append(cutoff.year)

    scraped: list[dict] = []
    for y in years:
        scraped.extend(fetch_bok_mpc_schedule(y))
        scraped.extend(fetch_kostat_release_schedule(y))

    # 기존 hardcoded fallback (절대로 삭제하지 않음 — 스크레이퍼 망가져도 최소 보장)
    try:
        from calendar_page import get_hardcoded_macro_events
        hardcoded = get_hardcoded_macro_events(today=today, window_days=window_days)
    except ImportError:
        hardcoded = []
    for e in hardcoded:
        e.setdefault("_origin", "hardcoded")

    # dedupe 정책:
    # 1. scraped 가 어떤 canonical 을 한 건이라도 가지면, 같은 canonical 의 hardcoded 는 모두 제거
    #    (hardcoded 의 날짜는 추정/구버전이라 실제 일정과 어긋날 수 있음 → scraped 가 권위)
    # 2. 1번 통과 후 (event_date, canonical) 쌍 중복 제거 (scraped 자체 중복 가드)
    scraped_canonicals = {_canonical_title(e["title"]) for e in scraped}
    hardcoded = [e for e in hardcoded
                 if _canonical_title(e["title"]) not in scraped_canonicals]

    seen: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for e in scraped + hardcoded:
        key = (e["event_date"], _canonical_title(e["title"]))
        if key in seen:
            continue
        seen.add(key)
        merged.append(e)

    today_iso = today.isoformat()
    cutoff_iso = cutoff.isoformat()
    in_window = [e for e in merged if today_iso <= e["event_date"] <= cutoff_iso]
    in_window.sort(key=lambda e: e["event_date"])
    return in_window
