"""
페이지 2: 다가올 이벤트 캘린더.

독립된 HTML 페이지로 렌더된다. events.py 에서 KRX/테마/종목매칭 유틸만 재사용.

출력:
  render_calendar_html(events) → 단독 HTML 문자열
  inject_nav_header(html, active_page) → 두 페이지 HTML 에 공통 네비를 끼워넣기
"""

import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

from events import (
    _split_sentences,
    collect_inferred_stocks,
    enrich_with_krx,
    find_direct_stocks_in_text,
    resolve_category_themes,
)


_KIND_MAIN_URL = "https://kind.krx.co.kr/corpgeneral/irschedule.do?method=searchIRScheduleMain&gubun=iRScheduleCalendar"
_KIND_POST_URL = "https://kind.krx.co.kr/corpgeneral/irschedule.do"
_KIND_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": _KIND_MAIN_URL,
    "X-Requested-With": "XMLHttpRequest",
}


# ============================================================
# 이벤트 수집: 뉴스 미래형 문구 + 하드코딩 거시 일정
# ============================================================

_YMD_RE = re.compile(r'(20\d{2})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?')
_MD_RE = re.compile(r'(\d{1,2})\s*월\s*(\d{1,2})\s*일')

# 상대 날짜 패턴
_REL_NEXT_MONTH_DAY = re.compile(r'(?:내달|다음\s*달)\s*(\d{1,2})\s*일')
_REL_THIS_MONTH_DAY = re.compile(r'(?:이\s*달|이번\s*달)\s*(\d{1,2})\s*일')
_REL_ONUN_DAY = re.compile(r'오는\s*(\d{1,2})\s*일')
_REL_WEEKDAY = re.compile(r'(다음\s*주|내주|이번\s*주)\s*([월화수목금토일])요일?')
_REL_NEXT_MONTH_END = re.compile(r'(?:내달|다음\s*달)\s*말')
_REL_THIS_MONTH_END = re.compile(r'(?:이\s*달|이번\s*달)\s*말')

# 범위 표현 (정확한 날짜 불명, 시작일로 환산 + 라벨)
_RANGE_MONTH = re.compile(r'(?<!\d)(\d{1,2})\s*월\s*(중|말|초|중순|하순|상순|께|안|내)')
_RANGE_MONTH_WEEK = re.compile(r'(?<!\d)(\d{1,2})\s*월\s*(첫째|둘째|셋째|넷째)\s*주')
_RANGE_H1 = re.compile(r'(?:올해|이번해)?\s*상반기')
_RANGE_H2 = re.compile(r'(?:올해|이번해)?\s*하반기')
_RANGE_YEAR_END = re.compile(r'연내|올해\s*안|연말')
_RANGE_QUARTER = re.compile(r'([1-4])\s*분기')
# 이달 / 이번달
_RANGE_THIS_MONTH = re.compile(r'(?:이\s*달|이번\s*달)\s*(중|말|초|중순|하순|안|내)')
# 다음달 / 내달 + 시점 suffix
_RANGE_NEXT_MONTH = re.compile(r'(?:내달|다음\s*달)\s*(중|말|초|중순|하순|안|내)')
# 내년 + N월 / 상·하반기 / 초·중·말
_RANGE_NEXT_YEAR_MONTH = re.compile(r'내년\s*(\d{1,2})\s*월')
_RANGE_NEXT_YEAR_HALF = re.compile(r'내년\s*(상반기|하반기|초|말|중반)')
_RANGE_NEXT_YEAR_ALONE = re.compile(r'(?<!\d)내년(?!\s*\d*월|\s*상반기|\s*하반기|\s*초|\s*말|\s*중반)')
# 이르면/빠르면 + 시점 — 뒤에 있는 월/기간 해석에 맡기고, 이건 단독 시그널로만 취급 (날짜는 다른 패턴이 처리)

_WEEKDAY_IDX = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}

_FUTURE_MARKERS = [
    "예정", "오는", "내달", "다음달", "다가오는", "앞두고",
    "개최", "열릴", "열린다", "발표할", "발표된다", "출시", "공개",
    "예상", "계획", "진행된다", "이뤄진다", "선보일",
    # 보강
    "착수", "시행", "도입", "가동", "개시", "가시화",
    "나선다", "나설", "추진", "도래", "이르면", "빠르면",
    "실시", "진행될", "예측", "전망", "앞서", "앞둔",
]

# 제목에 '명백히 종결된 이벤트' 표현이 있으면 미래 이벤트 추출 스킵.
# 보수적으로: 본문에 미래 일정 언급이 있어도 제목이 이 정도 단호하면 사후 보도.
_CONCLUSIVE_TITLE_MARKERS = [
    "무산", "무산된", "불발", "좌초", "결렬", "결론",
    "막내렸", "종료", "마감됐", "마감된", "성사된",
    "체결", "서명", "타결", "확정발표",
]

# 사용자 선호로 캘린더에서 제외할 부정적 이벤트 토픽.
# 제목에 포함되면 NEWS_FUTURE 추출 자체를 스킵.
_NEGATIVE_NEWS_MARKERS = [
    "주권매매거래정지", "매매거래정지", "거래정지",
    "상장폐지",
    "관리종목",
    "횡령", "배임",
    "회생절차", "법정관리", "기업회생",
]

# 추세·점진적 변화 표현 — 범위 표현(하반기/N분기 등)과 결합되면 '특정일에 일어날 일' 이 아니라
# '그 기간에 걸쳐 변할 것' 이라는 추세 전망 → 캘린더 이벤트 대상에서 컷.
# 예: "하반기로 갈수록 오를 전망", "점차 회복 흐름", "분기 내내 강세 추세"
_TREND_ONLY_MARKERS = [
    "갈수록", "점차", "꾸준히", "지속적으로", "서서히", "차츰",
    "장기적", "흐름이", "흐름을", "추세를", "추세다", "추세이",
    "기조다", "기조이", "양상이", "분위기다",
]


def _next_month_first(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def _month_last_day(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _parse_relative_date(sentence: str, today: date) -> Optional[date]:
    """문장에서 상대 날짜(내달 N일, 다음주 수요일, 이달 말 등) 파싱."""
    # 내달 N일
    m = _REL_NEXT_MONTH_DAY.search(sentence)
    if m:
        day = int(m.group(1))
        nm = _next_month_first(today)
        try:
            return date(nm.year, nm.month, day)
        except ValueError:
            return None

    # 이달 N일 — 오늘 이후만
    m = _REL_THIS_MONTH_DAY.search(sentence)
    if m:
        day = int(m.group(1))
        try:
            d = date(today.year, today.month, day)
            return d if d >= today else None
        except ValueError:
            return None

    # "오는 N일" — 본문 작성자는 보통 현재 달의 N일을 의미. 이번 달 N일이 아직 안
    # 지났으면 이번 달, 이미 지났으면 다음 달 N일 (가장 가까운 미래의 N일).
    m = _REL_ONUN_DAY.search(sentence)
    if m:
        day = int(m.group(1))
        try:
            d = date(today.year, today.month, day)
            if d < today:
                nm = _next_month_first(today)
                d = date(nm.year, nm.month, day)
            return d
        except ValueError:
            return None

    # 다음 주 요일 / 이번 주 요일 / 내주 요일
    m = _REL_WEEKDAY.search(sentence)
    if m:
        scope = m.group(1)
        target_dow = _WEEKDAY_IDX[m.group(2)]
        if "다음" in scope or "내주" in scope:
            # 다음 주 월요일 기준 + target_dow
            days_to_next_mon = 7 - today.weekday()
            return today + timedelta(days=days_to_next_mon + target_dow)
        else:  # 이번 주
            delta = target_dow - today.weekday()
            return today + timedelta(days=delta) if delta >= 0 else None

    # 내달 말
    if _REL_NEXT_MONTH_END.search(sentence):
        nm = _next_month_first(today)
        return _month_last_day(nm.year, nm.month)

    # 이달 말
    if _REL_THIS_MONTH_END.search(sentence):
        last = _month_last_day(today.year, today.month)
        return last if last >= today else None

    return None


def _parse_range_date(sentence: str, today: date) -> Optional[tuple[date, str]]:
    """
    범위 표현 → (시작일, 표시 라벨). 정확한 일자가 없어도 이벤트로 띄우기 위함.
    우선순위: 더 구체적인 패턴부터 매칭.
    """
    # 내년 N월
    m = _RANGE_NEXT_YEAR_MONTH.search(sentence)
    if m:
        try:
            mon = int(m.group(1))
            return date(today.year + 1, mon, 1), f"내년 {mon}월"
        except ValueError:
            pass

    # 내년 상/하반기·초·말·중반
    m = _RANGE_NEXT_YEAR_HALF.search(sentence)
    if m:
        suffix = m.group(1)
        year = today.year + 1
        if suffix == "상반기":
            return date(year, 1, 1), f"{year} 상반기"
        if suffix == "하반기":
            return date(year, 7, 1), f"{year} 하반기"
        if suffix == "초":
            return date(year, 1, 1), f"{year} 초"
        if suffix == "중반":
            return date(year, 6, 1), f"{year} 중반"
        if suffix == "말":
            return date(year, 12, 1), f"{year} 말"

    # '내년' 단독 (위 패턴들이 다 실패했을 때)
    if _RANGE_NEXT_YEAR_ALONE.search(sentence):
        return date(today.year + 1, 1, 1), f"{today.year + 1}년"

    # N월 첫째/둘째/셋째/넷째 주 — 대략 (1주=7일)
    m = _RANGE_MONTH_WEEK.search(sentence)
    if m:
        try:
            mon = int(m.group(1))
            week_label = m.group(2)
            week_idx = {"첫째": 0, "둘째": 1, "셋째": 2, "넷째": 3}[week_label]
            year = today.year
            start = date(year, mon, 1) + timedelta(days=week_idx * 7)
            if start < today:
                start = date(year + 1, mon, 1) + timedelta(days=week_idx * 7)
            return start, f"{mon}월 {week_label}주"
        except (ValueError, KeyError):
            pass

    # N월 중/말/초/중순/하순/께/안/내
    m = _RANGE_MONTH.search(sentence)
    if m:
        try:
            mon = int(m.group(1))
            suffix = m.group(2)
            year = today.year
            cand = date(year, mon, 1)
            if cand < date(today.year, today.month, 1):  # 이번 달 이전 → 내년
                cand = date(year + 1, mon, 1)
            return cand, f"{mon}월 {suffix}"
        except ValueError:
            pass

    # 이달 중/말/초/...
    m = _RANGE_THIS_MONTH.search(sentence)
    if m:
        suffix = m.group(1)
        return today, f"이달 {suffix}"

    # 다음달/내달 + 시점
    m = _RANGE_NEXT_MONTH.search(sentence)
    if m:
        suffix = m.group(1)
        if today.month == 12:
            nxt = date(today.year + 1, 1, 1)
        else:
            nxt = date(today.year, today.month + 1, 1)
        return nxt, f"다음달 {suffix}"

    # 상반기 (1월~6월)
    if _RANGE_H1.search(sentence):
        if today.month <= 6:
            return today, f"{today.year} 상반기"
        return date(today.year + 1, 1, 1), f"{today.year + 1} 상반기"

    # 하반기 (7월~12월)
    if _RANGE_H2.search(sentence):
        start = date(today.year, 7, 1)
        if start < today:
            return today if today.month >= 7 else start, f"{start.year} 하반기"
        return start, f"{start.year} 하반기"

    # N분기
    m = _RANGE_QUARTER.search(sentence)
    if m:
        q = int(m.group(1))
        start_month = (q - 1) * 3 + 1
        year = today.year
        start = date(year, start_month, 1)
        if start < date(today.year, today.month, 1):
            year += 1
            start = date(year, start_month, 1)
        return start, f"{year} {q}분기"

    # 연내 / 연말
    if _RANGE_YEAR_END.search(sentence):
        return today, f"{today.year} 연내"

    return None


def _parse_absolute_date(sentence: str, today: date) -> Optional[date]:
    """YYYY년/월/일 또는 월/일(연도 추정)."""
    ymd = _YMD_RE.search(sentence)
    if ymd:
        try:
            y, m, d = map(int, ymd.groups())
            return date(y, m, d)
        except ValueError:
            return None
    md = _MD_RE.search(sentence)
    if md:
        try:
            m, d = map(int, md.groups())
            candidate = date(today.year, m, d)
            if candidate < today:  # 올해 과거 → 내년으로
                candidate = date(today.year + 1, m, d)
            return candidate
        except ValueError:
            return None
    return None


def extract_future_events_from_news(news_items: list[dict],
                                    today: Optional[date] = None,
                                    window_days: int = 30) -> list[dict]:
    """
    뉴스 본문/제목에서 미래형 마커 + 날짜(절대/상대) 가 같은 문장에 있을 때 이벤트로 추출.
    상대 날짜 지원: 내달 N일, 이달 N일, 오는 N일, 다음주 X요일, 이달 말, 내달 말
    """
    if today is None:
        today = date.today()
    cutoff = today + timedelta(days=window_days)

    events: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for n in news_items:
        title = n.get("title", "")
        body = n.get("content", "")
        if not title and not body:
            continue
        # 제목에 명백한 종결 마커가 있으면 미래 이벤트 추출 대상에서 제외
        if any(m in title for m in _CONCLUSIVE_TITLE_MARKERS):
            continue
        # 사용자 선호로 컷하는 부정적 토픽 (거래정지 등)
        if any(m in title for m in _NEGATIVE_NEWS_MARKERS):
            continue
        for sentence in _split_sentences(title + " " + body):
            if not any(m in sentence for m in _FUTURE_MARKERS):
                continue

            event_date = _parse_absolute_date(sentence, today)
            event_label: Optional[str] = None
            if event_date is None:
                event_date = _parse_relative_date(sentence, today)
            if event_date is None:
                # 범위 표현(하반기/N분기/연내 등)이 추세 어휘와 결합되면 특정일 일정이 아닌
                # 추세 전망이라 캘린더에 띄울 가치 없음 → 컷
                if any(m in sentence for m in _TREND_ONLY_MARKERS):
                    continue
                ranged = _parse_range_date(sentence, today)
                if ranged:
                    event_date, event_label = ranged
            if event_date is None:
                continue

            if event_date < today or event_date > cutoff:
                continue

            key = (event_date.isoformat(), n.get("link", ""), event_label or "")
            if key in seen:
                continue
            seen.add(key)

            snippet = sentence.strip()
            if len(snippet) > 240:
                snippet = snippet[:240].rstrip() + "…"

            events.append({
                "type": "NEWS_FUTURE",
                "event_date": event_date.isoformat(),
                "event_date_label": event_label,  # 범위 표현이면 "6월 중" 같은 라벨
                "title": title,
                "body_snippet": snippet,
                "source_url": n.get("link", ""),
                "source_label": "뉴스",
                "news": n,
                "category_hints": [],
            })
    return events


# 2026년 주요 거시 일정 하드코딩 — bok_schedule.fetch_bok_mpc_schedule 가 죽었을 때
# 캘린더의 MACRO 섹션이 통째로 비는 걸 막는 fallback. 한국 일정은 스크레이퍼가 권위.
# 외부(미 Fed/CPI 등)는 ECOS 미커버 영역이라 hardcoded 가 적합.
# direction: 발표 전엔 결과 미정이므로 기본 "neutral" (변동성 ↑ 시그널)
_MACRO_EVENTS_2026 = [
    {"date": "2026-04-29", "title": "Fed FOMC 기준금리 결정",
     "body": "미국 연방공개시장위원회(FOMC) 정례회의 결과 발표. 기준금리 동결/인하 여부에 따라 금리 민감 섹터(은행·증권·보험) 변동성 확대 가능.",
     "category_hints": ["fomc"], "direction": "neutral"},
    {"date": "2026-05-13", "title": "미국 4월 소비자물가지수(CPI) 발표",
     "body": "미국 노동통계국 4월 CPI 발표. 인플레이션 둔화/재반등 방향이 Fed 다음 결정에 직접 영향.",
     "category_hints": ["fomc", "fx"], "direction": "neutral"},
    # 한국은행 금통위 일정은 bok_schedule.fetch_bok_mpc_schedule 이 라이브로 채움.
    # 스크레이퍼 실패 시 fallback 으로 노출되더라도 잘못된 추정 날짜를 보여주는 것보다
    # 비어있는 게 나아 hardcoded 에서 제거.
]


def get_hardcoded_macro_events(today: Optional[date] = None,
                               window_days: int = 30) -> list[dict]:
    if today is None:
        today = date.today()
    cutoff = today + timedelta(days=window_days)
    out: list[dict] = []
    for e in _MACRO_EVENTS_2026:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if today <= d <= cutoff:
            out.append({
                "type": "MACRO",
                "event_date": e["date"],
                "title": e["title"],
                "body_snippet": e["body"],
                "source_url": "",
                "source_label": "거시 캘린더 (하드코딩)",
                "category_hints": e.get("category_hints", []),
                "direction": e.get("direction", "neutral"),
            })
    return out


def fetch_kind_ir_events(today: Optional[date] = None,
                         window_days: int = 30) -> list[dict]:
    """
    KRX KIND 의 IR 일정 달력에서 향후 window_days 이내 기업 IR/실적 이벤트 수집.
    같은 날짜의 여러 기업은 하나의 'IR' 이벤트 카드로 묶는다 (`ir_stock_names` 리스트).
    """
    if today is None:
        today = date.today()
    cutoff = today + timedelta(days=window_days)

    # 이번 달 + 필요시 다음 달
    months: list[tuple[int, int]] = [(today.year, today.month)]
    if cutoff.month != today.month or cutoff.year != today.year:
        if today.month == 12:
            months.append((today.year + 1, 1))
        else:
            months.append((today.year, today.month + 1))

    session = requests.Session()
    try:
        session.get(_KIND_MAIN_URL, headers=_KIND_HEADERS, timeout=15)
    except requests.RequestException:
        return []

    entries: list[tuple[date, str]] = []
    for year, month in months:
        data = {
            "method": "searchIRScheduleCalendar",
            "gubun": "iRScheduleCalendar",
            "selYear": str(year),
            "selMonth": f"{month:02d}",
        }
        try:
            r = session.post(_KIND_POST_URL, data=data, headers=_KIND_HEADERS, timeout=15)
        except requests.RequestException:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for td in soup.find_all("td"):
            text = td.get_text(strip=True)
            m = re.match(r"^(\d{1,2})(?=\D|$)", text)
            if not m:
                continue
            try:
                ev_date = date(year, month, int(m.group(1)))
            except ValueError:
                continue
            if ev_date < today or ev_date > cutoff:
                continue
            for a in td.find_all("a"):
                name = a.get_text(strip=True)
                if name:
                    entries.append((ev_date, name))

    # 날짜별로 묶기 (중복 기업 제거)
    by_date: dict[date, list[str]] = defaultdict(list)
    for d, n in entries:
        by_date[d].append(n)

    events: list[dict] = []
    for d, names in sorted(by_date.items()):
        seen: set[str] = set()
        uniq = []
        for n in names:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        if not uniq:
            continue
        events.append({
            "type": "IR",
            "event_date": d.isoformat(),
            "title": f"기업 IR · 실적 일정 ({len(uniq)}개 기업)",
            "body_snippet": "KRX KIND 등록 기업 설명회·실적 발표·공시 일정.",
            "source_url": _KIND_MAIN_URL,
            "source_label": "KIND IR",
            "news": None,
            "category_hints": [],
            "ir_stock_names": uniq,
        })
    return events


def attach_stocks_to_event(event: dict,
                           name_map: dict[str, dict],
                           code_map: dict[str, dict],
                           theme_index: dict[str, dict],
                           categories: list[dict],
                           max_stocks: int = 5) -> dict:
    """
    이벤트에 관련 종목을 붙인다.
    - direct: 이벤트 본문/제목에서 KRX 종목 직접 추출 (📌)
    - inferred: 카테고리 매핑된 테마의 주도주 (🔗)
    IR 이벤트는 `ir_stock_names` 에 담긴 기업들을 direct 로 전체 매핑.
    """
    # IR 이벤트: name 리스트 그대로 direct 로 (제한 없음)
    if event.get("type") == "IR" and event.get("ir_stock_names"):
        direct_rows = []
        for n in event["ir_stock_names"]:
            hit = name_map.get(n)
            if hit:
                direct_rows.append({
                    "name": n, "code": hit["Code"],
                    "close": hit.get("Close"),
                    "change_pct": hit.get("ChagesRatio"),
                    "market": hit.get("Market"),
                })
        event["direct_stocks"] = direct_rows
        event["inferred_stocks"] = []
        event["matched_categories"] = []
        event["resolved_themes"] = []
        return event

    # DART 공시: stock_code/stock_name_hint 기반 direct 1건
    if event.get("type") == "DISCLOSURE":
        direct_rows: list[dict] = []
        code = (event.get("stock_code") or "").strip() or None
        name_hint = event.get("stock_name_hint") or ""
        if code and code in code_map:
            krx = code_map[code]
            direct_rows.append({
                "name": krx.get("Name"), "code": code,
                "close": krx.get("Close"),
                "change_pct": krx.get("ChagesRatio"),
                "market": krx.get("Market"),
            })
        elif name_hint and name_hint in name_map:
            hit = name_map[name_hint]
            direct_rows.append({
                "name": name_hint, "code": hit["Code"],
                "close": hit.get("Close"),
                "change_pct": hit.get("ChagesRatio"),
                "market": hit.get("Market"),
            })
        event["direct_stocks"] = direct_rows
        event["inferred_stocks"] = []
        event["matched_categories"] = []
        event["resolved_themes"] = []
        return event

    text_parts = [event.get("title", ""), event.get("body_snippet", "")]
    news = event.get("news")
    if news:
        text_parts.append(news.get("content", ""))
    text = " ".join(text_parts)

    direct_rows = find_direct_stocks_in_text(text, name_map, limit=max_stocks, min_len=3)
    direct_enriched = enrich_with_krx(
        [{"name": s["Name"], "code": s["Code"], "close": s.get("Close"),
          "change_pct": s.get("ChagesRatio"), "market": s.get("Market")}
         for s in direct_rows],
        name_map, code_map,
    )

    # 카테고리 매칭: 명시 hint 우선, 없으면 키워드 스캔 + 임베딩 정밀도 필터
    hint_ids = set(event.get("category_hints") or [])
    if hint_ids:
        matched_cats = [c for c in categories if c["id"] in hint_ids]
    else:
        candidate_cats = [c for c in categories if any(kw in text for kw in c["keywords"])]
        if candidate_cats:
            from category_matcher import (
                DEFAULT_THRESHOLD,
                build_category_index,
                score_article_categories,
            )
            cat_index = build_category_index(categories)
            if cat_index is None:
                matched_cats = candidate_cats
            else:
                scores = score_article_categories(
                    event.get("title", ""), text, cat_index,
                )
                matched_cats = [
                    c for c in candidate_cats
                    if scores.get(c["id"], 1.0) >= DEFAULT_THRESHOLD
                ]
        else:
            matched_cats = []

    inferred_stocks: list[dict] = []
    direct_codes = {s.get("code") for s in direct_enriched if s.get("code")}
    inferred_codes: set[str] = set()
    resolved_themes: list[dict] = []
    is_macro = event.get("type") == "MACRO"
    for c in matched_cats[:2]:
        if is_macro:
            # 거시 이벤트(CPI/FOMC/금통위 등)는 카테고리 theme_hints가 광범위해
            # "오늘 등락률 상위 증권/보험사" 같은 가짜 시그널을 만든다 — 종목 fetch 생략하고
            # 영향 섹터 라벨로만 표시한다 (렌더 단계).
            themes = resolve_category_themes(c, theme_index)
        else:
            stocks, themes = collect_inferred_stocks(c, theme_index, stocks_per_theme=3, cap=5)
            stocks = enrich_with_krx(stocks, name_map, code_map)
            for s in stocks:
                code = s.get("code")
                if not code or code in direct_codes or code in inferred_codes:
                    continue
                inferred_codes.add(code)
                s["matched_category"] = c["label"]
                inferred_stocks.append(s)
        for t in themes:
            if t["no"] not in {x["no"] for x in resolved_themes}:
                resolved_themes.append(t)
        if not is_macro and len(inferred_stocks) >= max_stocks:
            break

    event["direct_stocks"] = direct_enriched[:max_stocks]
    event["inferred_stocks"] = inferred_stocks[:max_stocks]
    event["matched_categories"] = [c["label"] for c in matched_cats]
    event["resolved_themes"] = resolved_themes
    return event


_TITLE_NORM_RE = re.compile(r"[\s\-…·,\.\(\)\[\]\"'`~!?:;/<>《》「」『』·*&^%$#@　]+")
_KO_THOUSAND_RE = re.compile(r"(\d+)\s*천\s*(\d{1,3}(?!\d))")  # "1천909" -> "1909"
_KO_WON_RE = re.compile(r"억\s*원")  # "억원" -> "억"


def _normalize_title(title: str) -> str:
    """제목 정규화: 한글 숫자단위(`1천909`→`1909`, `억원`→`억`) 통일 후
    공백·구두점·괄호 제거 + lowercase."""
    s = (title or "").lower()
    s = _KO_THOUSAND_RE.sub(lambda m: m.group(1) + m.group(2).zfill(3), s)
    s = _KO_WON_RE.sub("억", s)
    s = _TITLE_NORM_RE.sub("", s)
    return s


def _title_ngrams(s: str, n: int = 3) -> set[str]:
    if len(s) < n:
        return {s} if s else set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _title_similarity(a: str, b: str) -> float:
    """문자 3-gram Jaccard. 한글 헤드라인의 미세한 표기 차이에 강건."""
    sa = _title_ngrams(_normalize_title(a))
    sb = _title_ngrams(_normalize_title(b))
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _dedupe_news_events(events: list[dict], threshold: float = 0.55) -> list[dict]:
    """NEWS_FUTURE 이벤트 중 같은 날짜·동일 핵심종목(또는 카테고리) 그룹 안에서
    제목 유사도가 threshold 이상이면 1건만 살리고, 흡수된 기사 url 은
    살아남은 카드의 ``related_urls`` 에 부착한다.

    살아남는 카드 선택 기준: direct_stocks 개수 우선, 다음 본문 길이.
    """

    def _group_key(e: dict):
        date_s = e.get("event_date", "")
        direct = e.get("direct_stocks") or []
        if direct:
            code = (direct[0] or {}).get("code")
            if code:
                return (date_s, "S", code)
        cats = tuple(sorted(e.get("matched_categories") or []))
        if cats:
            return (date_s, "C", cats)
        return None

    def _score(e: dict) -> tuple[int, int]:
        return (
            len(e.get("direct_stocks") or []),
            len(e.get("body_snippet") or ""),
        )

    survivors: list[dict] = []
    consumed: set[int] = set()
    n = len(events)

    for i in range(n):
        if i in consumed:
            continue
        e = events[i]
        if e.get("type") != "NEWS_FUTURE":
            survivors.append(e)
            continue
        key_i = _group_key(e)
        if key_i is None:
            survivors.append(e)
            continue

        cluster = [(i, e)]
        for j in range(i + 1, n):
            if j in consumed:
                continue
            f = events[j]
            if f.get("type") != "NEWS_FUTURE":
                continue
            if _group_key(f) != key_i:
                continue
            if _title_similarity(e.get("title", ""), f.get("title", "")) >= threshold:
                cluster.append((j, f))
                consumed.add(j)

        if len(cluster) == 1:
            survivors.append(e)
            continue

        cluster.sort(key=lambda pair: _score(pair[1]), reverse=True)
        best = cluster[0][1]
        absorbed_urls: list[dict] = []
        seen_urls = {best.get("source_url") or ""}
        for _, c in cluster[1:]:
            url = c.get("source_url") or ""
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            absorbed_urls.append(
                {
                    "url": url,
                    "title": c.get("title", ""),
                    "source_label": c.get("source_label", ""),
                }
            )
        if absorbed_urls:
            existing = best.get("related_urls") or []
            best["related_urls"] = existing + absorbed_urls
        survivors.append(best)

    return survivors


def build_calendar_events(news_items: list[dict],
                          name_map: dict[str, dict],
                          code_map: dict[str, dict],
                          theme_index: dict[str, dict],
                          categories: list[dict],
                          window_days: int = 30) -> list[dict]:
    today = date.today()
    news_events = extract_future_events_from_news(news_items, today=today, window_days=window_days)
    from bok_schedule import get_macro_events
    macro_events = get_macro_events(today=today, window_days=window_days)
    # KIND IR/실적 일정은 시그널 가치 낮아 캘린더에서 제외 (필요 시 fetch_kind_ir_events 재활성화)

    # DART 공시 (무상증자결정 · 주식분할결정) — 최근 14일 접수분
    from dart_disclosure import fetch_dart_target_events, load_dart_key
    dart_events: list[dict] = []
    dart_key = load_dart_key()
    if dart_key:
        dart_events = fetch_dart_target_events(dart_key, today=today, past_window_days=14)

    events = macro_events + news_events + dart_events

    # ECOS 보강 — MACRO 이벤트 body_snippet 에 현재 지표값/변동 tail 부착.
    # ECOS_API_KEY 가 없으면 enrich 가 no-op 으로 그대로 통과.
    from ecos_client import load_ecos_key, enrich_event_with_ecos_context
    if load_ecos_key():
        for e in events:
            if e.get("type") == "MACRO":
                enrich_event_with_ecos_context(e)

    for e in events:
        attach_stocks_to_event(e, name_map, code_map, theme_index, categories)

    # 외국인·기관 5일 수급 보강 (NAVER 스크래핑) — direct 종목만, 호출 비용 관리
    from naver_supply import enrich_stocks_with_supply
    all_chips: list[dict] = []
    for e in events:
        all_chips.extend(e.get("direct_stocks", []))
    enrich_stocks_with_supply(all_chips, days=5, max_calls=120)

    # 시그널 태깅: NEWS_FUTURE 가 종목/카테고리 매칭 0건이면 low_signal 로 표시.
    # 렌더 단계에서 별도 섹션으로 강등 (drop 하지 않음).
    for e in events:
        has_signal = (
            e.get("type") in ("MACRO", "DISCLOSURE")
            or e.get("direct_stocks")
            or e.get("matched_categories")
        )
        e["low_signal"] = not has_signal

    # 오늘 이전 날짜는 제외 (지난 일정은 캘린더에 노이즈).
    # 단, DISCLOSURE(DART 공시) 는 접수일이 과거 14일 이내라 통과시킨다 — 'N일 전 공시' 라벨로 표시.
    today_iso = today.isoformat()
    events = [
        e for e in events
        if e.get("event_date", "") >= today_iso or e.get("type") == "DISCLOSURE"
    ]

    # 악재 필터: direction == 'negative' 인 이벤트는 캘린더에서 제외
    # (DART 의 유상증자결정·CB·BW 등 희석성 공시)
    # 단, future_schedule (CB 납입일·전환청구 시작 등) 은 일정 정보로서 통과.
    events = [
        e for e in events
        if e.get("direction") != "negative"
        or "future_schedule" in (e.get("flags") or [])
    ]

    # 뉴스 이벤트 중복 제거: 같은 날짜·동일 핵심종목 그룹 내 제목 3-gram Jaccard ≥ 0.55
    # 흡수된 기사는 살아남은 카드의 related_urls 로 부착되어 카드 하단에 노출.
    events = _dedupe_news_events(events)

    # 정렬: 날짜 → 타입 우선순위 → 종목 수 많은 순
    type_order = {"MACRO": 0, "NEWS_FUTURE": 1, "DISCLOSURE": 2}
    events.sort(key=lambda e: (
        e["event_date"],
        type_order.get(e.get("type"), 9),
        -(len(e.get("direct_stocks", [])) + len(e.get("inferred_stocks", []))),
    ))
    return events


# ============================================================
# HTML 렌더
# ============================================================

def _html_escape(s: str) -> str:
    import html as html_lib
    return html_lib.escape(s or "")


def _stock_chip(s: dict, kind: str) -> str:
    name = _html_escape(s.get("name") or "")
    code = s.get("code") or ""
    pct = s.get("change_pct")
    close = s.get("close")
    pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else ""
    close_str = f"{close:,.0f}" if isinstance(close, (int, float)) and close > 0 else ""
    cls = "up" if isinstance(pct, (int, float)) and pct > 0 else (
        "down" if isinstance(pct, (int, float)) and pct < 0 else "flat"
    )
    link = f"https://m.stock.naver.com/domestic/stock/{code}/total" if code else "#"
    cat_tag = ""
    if kind == "inferred" and s.get("matched_category"):
        cat_tag = f'<span class="theme-tag">{_html_escape(s["matched_category"])}</span>'
    from naver_supply import supply_badge_html
    supply_html = supply_badge_html(s.get("supply"), days=5)
    return (
        f'<a class="stock-chip {cls} {kind}" href="{link}" target="_blank" rel="noopener">'
        f'<span class="s-name">{name}</span>'
        + (f'<span class="s-close">{close_str}</span>' if close_str else '')
        + (f'<span class="s-pct">{pct_str}</span>' if pct_str else '')
        + supply_html
        + cat_tag
        + '</a>'
    )


def _theme_chip(t: dict) -> str:
    name = _html_escape(t.get("name") or "")
    no = t.get("no") or ""
    pct = t.get("change_pct")
    pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else ""
    cls = "up" if isinstance(pct, (int, float)) and pct > 0 else (
        "down" if isinstance(pct, (int, float)) and pct < 0 else "flat"
    )
    link = (
        f"https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={no}"
        if no else "#"
    )
    return (
        f'<a class="theme-chip {cls}" href="{link}" target="_blank" rel="noopener">'
        f'<span class="t-name">{name}</span>'
        + (f'<span class="t-pct">{pct_str}</span>' if pct_str else '')
        + '</a>'
    )


def _format_date_korean(iso_str: str) -> str:
    try:
        d = datetime.strptime(iso_str, "%Y-%m-%d").date()
    except ValueError:
        return iso_str
    today = date.today()
    dday = (d - today).days
    weekday = "월화수목금토일"[d.weekday()]
    label = f"{d.month}월 {d.day}일 ({weekday})"
    if dday == 0:
        return f"{label} · 오늘"
    if dday == 1:
        return f"{label} · 내일"
    if dday < 0:
        return f"{label} · {-dday}일 전 공시"
    return f"{label} · D-{dday}"


def _render_event_card(event: dict) -> str:
    title = _html_escape(event.get("title", ""))
    body = _html_escape(event.get("body_snippet", ""))
    src_url = event.get("source_url") or ""
    src_label = _html_escape(event.get("source_label") or "")
    event_type = event.get("type", "")
    type_icon = {
        "MACRO": "🌐", "NEWS_FUTURE": "📰", "IR": "🏛️", "DISCLOSURE": "📋",
    }.get(event_type, "📅")

    title_html = (
        f'<a href="{_html_escape(src_url)}" target="_blank" rel="noopener">{title}</a>'
        if src_url else title
    )

    stocks_html = ""
    if event.get("direct_stocks"):
        chips = "".join(_stock_chip(s, "direct") for s in event["direct_stocks"])
        stocks_html += (
            '<div class="stocks-block">'
            '<div class="label label-direct">📌 본문 직접 언급</div>'
            f'<div class="chips">{chips}</div></div>'
        )
    is_macro = event.get("type") == "MACRO"
    if is_macro and event.get("resolved_themes"):
        # 거시 이벤트는 개별 종목 대신 영향 섹터를 표시 (테마 등락률 = 섹터 반응 시그널).
        chips = "".join(_theme_chip(t) for t in event["resolved_themes"])
        cats = ", ".join(_html_escape(c) for c in event.get("matched_categories", [])[:3])
        stocks_html += (
            '<div class="stocks-block">'
            '<div class="label label-inferred">🌐 영향 섹터 (카테고리 매핑)</div>'
            f'<div class="chips">{chips}</div>'
            + (f'<div class="theme-source">매핑 카테고리: {cats}</div>' if cats else '')
            + '</div>'
        )
    elif event.get("inferred_stocks"):
        chips = "".join(_stock_chip(s, "inferred") for s in event["inferred_stocks"])
        cats = ", ".join(_html_escape(c) for c in event.get("matched_categories", [])[:3])
        stocks_html += (
            '<div class="stocks-block">'
            '<div class="label label-inferred">🔗 관련주 추정 (카테고리 매핑)</div>'
            f'<div class="chips">{chips}</div>'
            + (f'<div class="theme-source">매핑 카테고리: {cats}</div>' if cats else '')
            + '</div>'
        )
    if not stocks_html:
        stocks_html = '<div class="stocks-block"><div class="empty-chip">(관련 종목 매칭 없음)</div></div>'

    date_hint = ""
    if event.get("event_date_label"):
        date_hint = f'<span class="date-hint">📅 {_html_escape(event["event_date_label"])}</span>'

    # 방향성 뱃지
    # - DART 공시: 공시 유형 자체로 호재/악재 판정 가능
    # - MACRO: 발표 전엔 결과 미정 → '변동성 주목' 시그널로 표시
    direction = event.get("direction")
    dir_badge = ""
    if direction == "positive":
        dir_badge = '<span class="dir-badge dir-pos">📈 호재</span>'
    elif direction == "negative":
        dir_badge = '<span class="dir-badge dir-neg">📉 악재</span>'
    elif direction == "neutral":
        if event_type == "MACRO":
            dir_badge = '<span class="dir-badge dir-watch">🎯 변동성 주목</span>'
        else:
            dir_badge = '<span class="dir-badge dir-neu">➖ 중립</span>'

    # 부가 플래그 뱃지 (DART 액면가 분석 등)
    flag_badges = ""
    flags = event.get("flags") or []
    if "preferred_share_issuance" in flags:
        meta = event.get("face_value_meta") or {}
        pre = meta.get("pre")
        post = meta.get("post")
        tooltip = f"신주 액면가 {post}원 / 기존 {pre}원 → 종류주(우선주 등) 발행 의심" if pre and post else "종류주 발행 의심"
        flag_badges += f'<span class="flag-badge flag-pref" title="{_html_escape(tooltip)}">🏷️ 종류주 의심</span>'

    related_html = ""
    related_urls = event.get("related_urls") or []
    if related_urls:
        items = "".join(
            f'<li><a href="{_html_escape(r.get("url",""))}" target="_blank" rel="noopener">'
            f'{_html_escape(r.get("title",""))}</a>'
            + (
                f' <span class="related-source">{_html_escape(r.get("source_label",""))}</span>'
                if r.get("source_label") else ""
            )
            + '</li>'
            for r in related_urls
        )
        related_html = (
            '<div class="related-block">'
            f'<div class="related-label">📎 같은 사건 다른 보도 ({len(related_urls)})</div>'
            f'<ul class="related-list">{items}</ul>'
            '</div>'
        )

    return (
        '<article class="event-card">'
        f'<header class="event-header"><span class="type-icon">{type_icon}</span>'
        f'<h3>{title_html}</h3>'
        f'{dir_badge}{flag_badges}{date_hint}'
        f'<span class="source-tag">{src_label}</span></header>'
        f'<p class="event-body">{body}</p>'
        f'{stocks_html}'
        f'{related_html}'
        '</article>'
    )


# 세 페이지가 공유할 네비 HTML (상단 고정). 활성 페이지에 따라 aria-current 주입.
NAV_TEMPLATE = """
<nav class="topnav">
  <div class="nav-inner">
    <div class="brand">📊 주식 인사이트</div>
    <div class="tabs">
      <a href="news_preview.html" class="tab {policy_active}">🚀 정책·이벤트 수혜</a>
      <a href="news_dart.html" class="tab {dart_active}">📋 다트공시</a>
      <a href="news_calendar.html" class="tab {calendar_active}">📅 다가올 캘린더</a>
      <a href="news_sector_flow.html" class="tab {sector_active}">💰 섹터 자금흐름</a>
    </div>
  </div>
</nav>
"""


NAV_CSS = """
  body { padding-top: 0 !important; }
  .topnav {
    position: sticky; top: 0; z-index: 100;
    background: rgba(255,255,255,0.95);
    backdrop-filter: saturate(180%) blur(12px);
    border-bottom: 1px solid #e5e5ea;
  }
  .nav-inner {
    max-width: 900px; margin: 0 auto;
    display: flex; align-items: center; gap: 20px;
    padding: 12px 20px;
  }
  .brand { font-size: 14px; font-weight: 700; color: #1d1d1f; }
  .tabs { display: flex; gap: 4px; margin-left: auto; }
  .tab {
    padding: 8px 14px; border-radius: 8px;
    font-size: 13px; font-weight: 500; color: #555;
    text-decoration: none; transition: background .15s, color .15s;
  }
  .tab:hover { background: #f0f0f5; color: #1d1d1f; }
  .tab.active { background: #0071e3; color: #fff; }

  @media (max-width:640px) {
    .nav-inner { padding: 8px 12px; gap: 10px; }
    .brand { font-size: 12px; }
    .tabs { gap: 2px; }
    .tab { padding: 6px 10px; font-size: 12px; border-radius: 6px; }
  }
  @media (max-width:380px) {
    .brand { display: none; }
    .nav-inner { padding: 6px 8px; gap: 4px; }
    .tab { padding: 6px 8px; font-size: 11px; }
  }
"""


def nav_html(active: str) -> str:
    """active: 'policy' | 'dart' | 'calendar' | 'sector_flow'"""
    return NAV_TEMPLATE.format(
        policy_active="active" if active == "policy" else "",
        dart_active="active" if active == "dart" else "",
        calendar_active="active" if active == "calendar" else "",
        sector_active="active" if active == "sector_flow" else "",
    )


def inject_nav(full_html: str, active: str) -> str:
    """완성된 HTML 에 상단 네비를 끼워넣기. <body> 바로 뒤 + <style> 내부에 NAV_CSS 추가."""
    nav = nav_html(active)
    if "</style>" in full_html:
        full_html = full_html.replace("</style>", NAV_CSS + "</style>", 1)
    if "<body>" in full_html:
        full_html = full_html.replace("<body>", "<body>\n" + nav, 1)
    return full_html


def render_calendar_html(events: list[dict],
                         page_title: str = "다가올 이벤트 캘린더",
                         page_icon: str = "📅",
                         page_subtitle: str = "향후 30일") -> str:
    today = date.today().isoformat()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _render_date_groups(evts: list[dict]) -> str:
        groups: dict[str, list[dict]] = defaultdict(list)
        for e in evts:
            groups[e["event_date"]].append(e)
        sections: list[str] = []
        for d in sorted(groups.keys()):
            label = _html_escape(_format_date_korean(d))
            cards_html = "".join(_render_event_card(e) for e in groups[d])
            sections.append(
                f'<div class="date-group"><div class="date-label">{label}</div>{cards_html}</div>'
            )
        return "".join(sections)

    if not events:
        body = f'<section class="empty-state">{_html_escape(page_subtitle)} 내 감지된 이벤트가 없습니다.</section>'
    else:
        main_events = [e for e in events if not e.get("low_signal")]
        low_events = [e for e in events if e.get("low_signal")]
        main_html = _render_date_groups(main_events) if main_events else ''
        low_html = ''
        if low_events:
            low_html = (
                '<section class="low-signal-section">'
                '<h2 class="section-divider">📎 시장 관련성 미확정'
                f'<span class="section-meta"> · {len(low_events)}건 · 종목·카테고리 매칭 없음</span>'
                '</h2>'
                + _render_date_groups(low_events)
                + '</section>'
            )
        body = main_html + low_html

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{page_title} — {today}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect x='14' y='22' width='72' height='68' rx='10' fill='%23fff' stroke='%230071e3' stroke-width='5'/><rect x='14' y='22' width='72' height='20' rx='10' fill='%230071e3'/><rect x='14' y='34' width='72' height='8' fill='%230071e3'/><circle cx='32' cy='14' r='5' fill='%230071e3'/><circle cx='68' cy='14' r='5' fill='%230071e3'/><circle cx='40' cy='60' r='4' fill='%23222'/><circle cx='60' cy='60' r='4' fill='%23222'/><path d='M40 72 Q50 80 60 72' stroke='%23222' stroke-width='3' fill='none' stroke-linecap='round'/><circle cx='30' cy='68' r='4' fill='%23FF9DB0' opacity='0.7'/><circle cx='70' cy='68' r='4' fill='%23FF9DB0' opacity='0.7'/></svg>">
<style>
  :root {{
    --bg:#f5f5f7; --card:#fff; --border:#e5e5ea;
    --text:#1d1d1f; --muted:#86868b; --accent:#0071e3; --accent-soft:#f0f6ff;
    --up:#e74c3c; --up-soft:#fdecea;
    --down:#0080ff; --down-soft:#e8f2ff;
    --flat:#888;
    --direct:#2e7d32; --direct-soft:#e8f5e9;
    --inferred:#6a1b9a; --inferred-soft:#f3e5f5;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    font-family:-apple-system,'Apple SD Gothic Neo','Pretendard','Segoe UI',sans-serif;
    background:var(--bg); color:var(--text); margin:0;
    padding:48px 20px; font-size:16px; line-height:1.6;
  }}
  .container {{ max-width:900px; margin:0 auto; }}
  .page-header {{ margin-bottom:28px; padding-bottom:20px; border-bottom:1px solid var(--border); }}
  .page-header h1 {{ font-size:28px; font-weight:700; margin:0 0 6px; letter-spacing:-0.02em; }}
  .page-meta {{ color:var(--muted); font-size:14px; }}
  .page-meta strong {{ color:var(--text); font-weight:600; }}

  .date-group {{ margin-bottom:26px; }}
  .date-label {{
    font-size:14px; font-weight:700; color:var(--accent);
    background:var(--accent-soft);
    display:inline-block; padding:6px 14px; border-radius:20px; margin-bottom:10px;
  }}

  .event-card {{
    background:var(--card); border:1px solid var(--border); border-radius:14px;
    padding:18px 22px; margin-bottom:10px;
  }}
  .event-header {{
    display:flex; align-items:baseline; gap:10px;
    padding-bottom:8px; border-bottom:1px dashed var(--border); margin-bottom:10px;
  }}
  .event-header h3 {{ margin:0; font-size:16px; font-weight:700; flex:1; }}
  .event-header h3 a {{ color:var(--text); text-decoration:none; }}
  .event-header h3 a:hover {{ color:var(--accent); }}
  .type-icon {{ font-size:15px; }}
  .source-tag {{
    font-size:11px; color:var(--muted); background:#f0f0f5;
    padding:2px 8px; border-radius:10px; white-space:nowrap;
  }}
  .date-hint {{
    font-size:11px; color:#ff9500; background:#fff4e5;
    padding:2px 8px; border-radius:10px; white-space:nowrap;
    font-weight:600;
  }}
  .dir-badge {{
    font-size:11px; font-weight:700;
    padding:2px 10px; border-radius:10px; white-space:nowrap;
  }}
  .dir-pos {{ background:var(--up-soft); color:var(--up); }}
  .dir-neg {{ background:var(--down-soft); color:var(--down); }}
  .dir-neu {{ background:#f0f0f5; color:#666; }}
  .dir-watch {{ background:#fff4e5; color:#ff9500; }}
  .flag-badge {{
    font-size:11px; font-weight:600;
    padding:2px 10px; border-radius:10px; white-space:nowrap;
    cursor:help;
  }}
  .flag-pref {{ background:#fef3c7; color:#92400e; border:1px solid #fde68a; }}
  .event-body {{
    font-size:13.5px; color:#444; margin:0 0 12px;
    padding:8px 12px; background:#fafafa; border-radius:6px;
    line-height:1.65;
  }}

  .stocks-block {{ margin-top:10px; }}
  .label {{ font-size:12px; font-weight:700; margin-bottom:6px; }}
  .label-direct {{ color:var(--direct); }}
  .label-inferred {{ color:var(--inferred); }}
  .chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
  .empty-chip {{ color:var(--muted); font-size:13px; font-style:italic; padding:4px 0; }}
  .stock-chip {{
    display:inline-flex; align-items:center; gap:8px;
    background:#f8f8fa; border:1px solid var(--border);
    padding:5px 10px; border-radius:8px;
    text-decoration:none; color:var(--text); font-size:13px;
  }}
  .stock-chip.direct {{ border-left:3px solid var(--direct); }}
  .stock-chip.inferred {{ border-left:3px solid var(--inferred); background:#faf7fc; }}
  .stock-chip.up {{ background:var(--up-soft); border-color:#f5c6cb; }}
  .stock-chip.up .s-pct {{ color:var(--up); font-weight:700; }}
  .stock-chip.down {{ background:var(--down-soft); border-color:#b8d4ff; }}
  .stock-chip.down .s-pct {{ color:var(--down); font-weight:700; }}
  .s-name {{ font-weight:600; }}
  .s-close {{ font-size:11px; color:#555; }}
  .s-pct {{ font-size:12px; }}
  .supply-tag {{
    font-size:10px; font-weight:700;
    padding:1px 5px; border-radius:4px; margin-left:3px;
    white-space:nowrap; cursor:help;
  }}
  .supply-buy {{ background:#fdecea; color:#c0392b; }}
  .supply-sell {{ background:#e8f2ff; color:#1565c0; }}
  .theme-tag {{
    background:var(--inferred-soft); color:var(--inferred);
    font-size:10px; padding:2px 6px; border-radius:4px;
  }}
  .theme-chip {{
    display:inline-flex; align-items:center; gap:8px;
    background:var(--inferred-soft); border:1px solid var(--border);
    border-left:3px solid var(--inferred);
    padding:5px 10px; border-radius:8px;
    text-decoration:none; color:var(--text); font-size:13px;
  }}
  .theme-chip.up {{ background:var(--up-soft); border-color:#f5c6cb; }}
  .theme-chip.up .t-pct {{ color:var(--up); font-weight:700; }}
  .theme-chip.down {{ background:var(--down-soft); border-color:#b8d4ff; }}
  .theme-chip.down .t-pct {{ color:var(--down); font-weight:700; }}
  .t-name {{ font-weight:600; }}
  .t-pct {{ font-size:12px; }}
  .theme-source {{ margin-top:8px; font-size:11px; color:var(--muted); font-style:italic; }}

  .related-block {{
    margin-top:12px; padding-top:10px; border-top:1px dashed var(--border);
  }}
  .related-label {{
    font-size:11px; font-weight:600; color:var(--muted); margin-bottom:6px;
  }}
  .related-list {{ list-style:none; padding:0; margin:0; }}
  .related-list li {{ padding:3px 0; font-size:12.5px; }}
  .related-list a {{ color:#555; text-decoration:none; }}
  .related-list a:hover {{ color:var(--accent); text-decoration:underline; }}
  .related-source {{ font-size:10px; color:var(--muted); margin-left:6px; }}

  .empty-state {{
    background:var(--card); border:1px dashed var(--border); border-radius:14px;
    padding:40px 24px; text-align:center; color:var(--muted);
  }}

  .low-signal-section {{
    margin-top:36px; padding-top:20px;
    border-top:2px dashed var(--border);
  }}
  .low-signal-section .section-divider {{
    font-size:15px; font-weight:600; color:var(--muted);
    margin:0 0 14px; letter-spacing:-0.01em;
  }}
  .low-signal-section .section-meta {{
    font-size:12px; font-weight:400; color:var(--muted);
  }}
  .low-signal-section .event-card {{
    padding:12px 16px; background:#fafafa; opacity:0.78;
  }}
  .low-signal-section .event-card:hover {{ opacity:1; }}
  .low-signal-section .date-label {{
    background:#f0f0f5; color:var(--muted);
  }}

  @media (max-width:640px) {{
    body {{ padding:12px 12px; font-size:15px; }}
    .page-header {{ margin-bottom:20px; padding-bottom:14px; }}
    .page-header h1 {{ font-size:22px; }}
    .page-meta {{ font-size:13px; }}

    .date-group {{ margin-bottom:20px; }}
    .date-label {{ font-size:13px; padding:5px 12px; }}

    .event-card {{ padding:14px 16px; border-radius:12px; }}
    .event-header {{ gap:6px; flex-wrap:wrap; padding-bottom:8px; margin-bottom:8px; }}
    .event-header h3 {{ font-size:15px; flex:1 1 100%; }}
    .type-icon {{ font-size:14px; }}
    .source-tag, .date-hint, .dir-badge, .flag-badge {{
      font-size:10px; padding:2px 7px;
    }}
    .event-body {{ font-size:13px; padding:8px 10px; line-height:1.55; }}

    .stocks-block {{ margin-top:8px; }}
    .label {{ font-size:11px; margin-bottom:5px; }}
    .stock-chip, .theme-chip {{ padding:4px 8px; font-size:12px; gap:6px; }}
    .s-close {{ display:none; }}
    .s-pct, .t-pct {{ font-size:11px; }}
    .supply-tag {{ font-size:9px; padding:1px 4px; }}
    .theme-tag {{ font-size:9px; padding:1px 5px; }}
    .theme-source {{ font-size:10px; }}

    .empty-state {{ padding:28px 16px; }}

    .related-block {{ margin-top:10px; padding-top:8px; }}
    .related-label {{ font-size:10px; }}
    .related-list li {{ font-size:12px; }}
    .related-source {{ font-size:9px; }}

    .low-signal-section {{ margin-top:24px; padding-top:14px; }}
    .low-signal-section .section-divider {{ font-size:14px; }}
    .low-signal-section .event-card {{ padding:10px 14px; }}
  }}
</style></head><body>
<div class="container">
  <header class="page-header">
    <h1>{page_icon} {page_title}</h1>
    <div class="page-meta">
      오늘 <strong>{today}</strong> · {page_subtitle} · 이벤트 <strong>{len(events)}건</strong> · {now}
    </div>
  </header>
  {body}
</div>
</body></html>"""
