"""
주식 인사이트 대시보드 진입점 — 3페이지 출력.

페이지 1: 정책·이벤트 수혜 (events.py 사용) → /tmp/news_preview.html
페이지 2: 다트공시 (calendar_page.py 사용, DISCLOSURE 만) → /tmp/news_dart.html
페이지 3: 다가올 이벤트 캘린더 (calendar_page.py 사용, MACRO + NEWS_FUTURE) → /tmp/news_calendar.html

세 HTML 모두 상단에 공통 네비 바를 주입해 서로 왕래 가능.

실행:
    .venv/bin/python3 run_event_preview.py
"""

import warnings
from datetime import date

warnings.filterwarnings("ignore")

from event_categories import CATEGORIES
from events import (
    build_code_map,
    build_event_cards,
    build_name_map,
    fetch_naver_theme_index,
    fetch_policy_news,
    load_krx_listings,
    render_policy_event_html,
)
from calendar_page import (
    build_calendar_events,
    inject_nav,
    render_calendar_html,
)
from news_sources import fetch_rss_news, merge_news_dedupe


POLICY_OUT = "/tmp/news_preview.html"
DART_OUT = "/tmp/news_dart.html"
CALENDAR_OUT = "/tmp/news_calendar.html"


def main():
    print("=" * 50)
    print("📊 주식 인사이트 대시보드 (3페이지)")
    print("=" * 50)

    print("▸ KRX 캐시 로드...")
    stocks = load_krx_listings()
    name_map = build_name_map(stocks)
    code_map = build_code_map(stocks)
    print(f"  KRX 종목 {len(stocks):,}개")

    print("▸ 네이버 테마 인덱스...")
    theme_index = fetch_naver_theme_index()
    print(f"  테마 {len(theme_index)}개")

    print("▸ 경제 뉴스 수집 (네이버 증권·경제)...")
    naver_news = fetch_policy_news(sections=[258, 259, 261, 262, 263, 310], per_section=30)
    print(f"  네이버 {len(naver_news)}건")

    print("▸ 원 publisher RSS 수집 (연합뉴스·매경)...")
    rss_news = fetch_rss_news(per_feed=20, fetch_body=True)
    print(f"  RSS {len(rss_news)}건")

    news = merge_news_dedupe([naver_news, rss_news])
    print(f"  병합·중복제거 후 뉴스 {len(news)}건")

    # ------------- 페이지 1: 정책·이벤트 수혜 -------------
    print("\n[페이지 1] 정책·이벤트 수혜 카드 빌드...")
    policy_cards = build_event_cards(news, CATEGORIES, theme_index, name_map, code_map)
    print(f"  매칭 카테고리 {len(policy_cards)}개")
    policy_html = render_policy_event_html(policy_cards, total_news_count=len(news))
    policy_html = inject_nav(policy_html, active="policy")
    with open(POLICY_OUT, "w", encoding="utf-8") as f:
        f.write(policy_html)
    print(f"  ✅ {POLICY_OUT} ({len(policy_html):,} bytes)")

    # ------------- 캘린더 이벤트 빌드 (DART + 캘린더 페이지가 공유) -------------
    print("\n[캘린더 이벤트 빌드]")
    today = date.today()
    days_to_eoy = (date(today.year, 12, 31) - today).days
    cal_events = build_calendar_events(news, name_map, code_map, theme_index, CATEGORIES, window_days=days_to_eoy)
    print(f"  전체 이벤트 {len(cal_events)}건 (거시 + 뉴스 미래형 + DART 공시)")

    dart_events = [e for e in cal_events if e.get("type") == "DISCLOSURE"]
    upcoming_events = [e for e in cal_events if e.get("type") != "DISCLOSURE"]

    # ------------- 페이지 2: 다트공시 -------------
    print(f"\n[페이지 2] 다트공시 빌드 ({len(dart_events)}건)...")
    for e in dart_events[:5]:
        d_count = len(e.get("direct_stocks", []))
        print(f"    · {e['event_date']} [{e['type']}] {e['title'][:40]} · 📌{d_count}")
    dart_html = render_calendar_html(
        dart_events,
        page_title="다트공시",
        page_icon="📋",
        page_subtitle="최근 14일 접수분",
    )
    dart_html = inject_nav(dart_html, active="dart")
    with open(DART_OUT, "w", encoding="utf-8") as f:
        f.write(dart_html)
    print(f"  ✅ {DART_OUT} ({len(dart_html):,} bytes)")

    # ------------- 페이지 3: 다가올 이벤트 캘린더 -------------
    print(f"\n[페이지 3] 다가올 이벤트 캘린더 빌드 ({len(upcoming_events)}건)...")
    for e in upcoming_events[:8]:
        d_count = len(e.get("direct_stocks", []))
        i_count = len(e.get("inferred_stocks", []))
        print(f"    · {e['event_date']} [{e['type']}] {e['title'][:40]} "
              f"· 📌{d_count} 🔗{i_count}")
    calendar_html = render_calendar_html(
        upcoming_events,
        page_title="다가올 이벤트 캘린더",
        page_icon="📅",
        page_subtitle="향후 30일",
    )
    calendar_html = inject_nav(calendar_html, active="calendar")
    with open(CALENDAR_OUT, "w", encoding="utf-8") as f:
        f.write(calendar_html)
    print(f"  ✅ {CALENDAR_OUT} ({len(calendar_html):,} bytes)")

    print("\n세 파일 다 준비 완료. 페이지 상단 네비로 왕래하세요.")


if __name__ == "__main__":
    main()
