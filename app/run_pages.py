#!/usr/bin/env python3
"""Build static dashboard HTML (3 pages) for GitHub Pages deployment."""
import os
import sys
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))

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


OUTDIR = Path(os.environ.get("PAGES_OUTDIR", "public")).resolve()


REDIRECT_HTML = """<!DOCTYPE html>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0; url=news_preview.html">
<title>Redirecting...</title>
<a href="news_preview.html">news_preview.html</a>
"""


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {OUTDIR}")

    print("Loading KRX listings...")
    stocks = load_krx_listings()
    name_map = build_name_map(stocks)
    code_map = build_code_map(stocks)
    print(f"  KRX {len(stocks):,} symbols")

    print("Fetching Naver theme index...")
    theme_index = fetch_naver_theme_index()
    print(f"  themes {len(theme_index)}")

    print("Fetching news (Naver sections + RSS)...")
    naver_news = fetch_policy_news(
        sections=[258, 259, 261, 262, 263, 310], per_section=30
    )
    rss_news = fetch_rss_news(per_feed=20, fetch_body=True)
    news = merge_news_dedupe([naver_news, rss_news])
    print(f"  merged news {len(news)}")

    print("[Page 1] policy event cards...")
    policy_cards = build_event_cards(
        news, CATEGORIES, theme_index, name_map, code_map
    )
    policy_html = render_policy_event_html(policy_cards, total_news_count=len(news))
    policy_html = inject_nav(policy_html, active="policy")
    (OUTDIR / "news_preview.html").write_text(policy_html, encoding="utf-8")
    print(f"  -> news_preview.html ({len(policy_html):,} bytes)")

    print("[Calendar events build]")
    today = date.today()
    days_to_eoy = (date(today.year, 12, 31) - today).days
    cal_events = build_calendar_events(
        news, name_map, code_map, theme_index, CATEGORIES,
        window_days=days_to_eoy,
    )
    print(f"  total events {len(cal_events)}")

    def _is_future_schedule(ev: dict) -> bool:
        return "future_schedule" in (ev.get("flags") or [])

    # DART 페이지: 접수된 공시 (과거 14일) + 미래 일정
    dart_events = [e for e in cal_events if e.get("type") == "DISCLOSURE"]
    # 캘린더 페이지: 비-DART 미래 이벤트 + DART 미래 일정 (future_schedule)
    upcoming_events = [
        e for e in cal_events
        if e.get("type") != "DISCLOSURE" or _is_future_schedule(e)
    ]

    dart_future_count = sum(1 for e in dart_events if _is_future_schedule(e))
    print(f"[Page 2] DART disclosures ({len(dart_events)}, 미래 {dart_future_count})...")
    dart_html = render_calendar_html(
        dart_events, page_title="다트공시", page_icon="📋",
        page_subtitle=f"최근 14일 접수분 + 미래 일정 {dart_future_count}건",
    )
    dart_html = inject_nav(dart_html, active="dart")
    (OUTDIR / "news_dart.html").write_text(dart_html, encoding="utf-8")
    print(f"  -> news_dart.html ({len(dart_html):,} bytes)")

    print(f"[Page 3] upcoming calendar ({len(upcoming_events)})...")
    cal_html = render_calendar_html(
        upcoming_events, page_title="다가올 이벤트 캘린더", page_icon="📅",
        page_subtitle="향후 30일",
    )
    cal_html = inject_nav(cal_html, active="calendar")
    (OUTDIR / "news_calendar.html").write_text(cal_html, encoding="utf-8")
    print(f"  -> news_calendar.html ({len(cal_html):,} bytes)")

    # [Page 4] Sector money flow (외인·기관 섹터별 합산 + 다가올 카탈리스트)
    print("[Page 4] sector money flow...")
    try:
        from sector_flow_page import build_sector_flow_page
        sector_name_map = {s["Code"]: s["Name"] for s in stocks}
        sector_html_path = OUTDIR / "news_sector_flow.html"
        sector_html = build_sector_flow_page(
            out_path=sector_html_path,
            supply_cache_path=Path(__file__).parent / "naver_supply_cache.json",
            sector_cache_path=Path(__file__).parent / "krx_sector_cache.json",
            name_map=sector_name_map,
            calendar_events=cal_events,
        )
        sector_html = inject_nav(sector_html, active="sector_flow")
        sector_html_path.write_text(sector_html, encoding="utf-8")
        print(f"  -> news_sector_flow.html ({len(sector_html):,} bytes)")
    except FileNotFoundError as exc:
        print(f"  WARN: sector flow build skipped — {exc}")

    (OUTDIR / "index.html").write_text(REDIRECT_HTML, encoding="utf-8")
    (OUTDIR / ".nojekyll").write_text("")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
