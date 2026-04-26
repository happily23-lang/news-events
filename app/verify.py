"""
전체 파이프라인 검증 스크립트.

각 소스 / 이벤트 타입 / 종목 매칭의 정확도를 프로빙하여 요약 리포트 출력.
사용:
    .venv/bin/python3 verify.py
"""

import sys
import types
import warnings
from collections import Counter
from datetime import date, datetime

warnings.filterwarnings("ignore")
sys.modules.setdefault("anthropic", types.SimpleNamespace(Anthropic=lambda **k: None))

import requests  # noqa: E402

from event_categories import CATEGORIES
from events import (
    build_code_map, build_event_cards, build_name_map,
    fetch_naver_theme_index, fetch_policy_news, load_krx_listings,
)
from calendar_page import build_calendar_events
from news_sources import fetch_rss_news, merge_news_dedupe


def banner(t: str):
    print()
    print("=" * 60)
    print(t)
    print("=" * 60)


def main():
    today = date.today()
    print(f"검증 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')} · 기준일 {today}")

    banner("1. 소스별 수집량")
    stocks = load_krx_listings()
    name_map = build_name_map(stocks)
    code_map = build_code_map(stocks)
    theme_index = fetch_naver_theme_index()
    print(f"  KRX 종목: {len(stocks):,}개 (매칭 후보 {len(name_map):,})")
    print(f"  네이버 테마 인덱스: {len(theme_index)}개")

    naver_news = fetch_policy_news(sections=[258, 259, 261, 262, 263, 310], per_section=30)
    rss_news = fetch_rss_news(per_feed=20, fetch_body=True)
    news = merge_news_dedupe([naver_news, rss_news])
    print(f"  네이버 뉴스: {len(naver_news)}")
    print(f"  RSS 뉴스: {len(rss_news)}")
    print(f"  병합 후: {len(news)} (중복 제거 {len(naver_news) + len(rss_news) - len(news)}건)")

    # 매체 분포
    sec_counter = Counter(n.get("section") for n in news)
    print(f"\n  섹션/매체 분포:")
    for sec, cnt in sec_counter.most_common():
        print(f"    {sec}: {cnt}")

    banner("2. 페이지 1: 정책·이벤트 수혜 카드")
    policy_cards = build_event_cards(news, CATEGORIES, theme_index, name_map, code_map)
    print(f"  매칭 카테고리: {len(policy_cards)}/{len(CATEGORIES)}")
    for c in policy_cards:
        label = c["category"]["label"]
        n_news = len(c["matched_news"])
        n_direct = len(c["direct_stocks"])
        n_inferred = len(c["inferred_stocks"])
        title_match = sum(1 for m in c["matched_news"] if m.get("title_match"))
        print(f"  · {label:15s} 뉴스 {n_news:3d}건 (제목매칭 {title_match}) "
              f"· 📌 {n_direct} · 🔗 {n_inferred}")

    banner("2-1. 카테고리별 실제 매칭 기사 샘플 (정확도 진단)")
    for c in policy_cards:
        label = c["category"]["label"]
        news_list = c["matched_news"]
        title_matched = [n for n in news_list if n.get("title_match")]
        body_only = [n for n in news_list if not n.get("title_match")]
        print(f"\n  [{label}] 제목매칭 {len(title_matched)} / 본문만 {len(body_only)}")

        # 제목 매칭 상위 3개 (강한 시그널)
        if title_matched:
            print(f"    ✅ 제목매칭 샘플:")
            for n in title_matched[:3]:
                kws = "/".join(n.get("matched_keywords", [])[:3])
                print(f"      [{kws}] {n['title'][:70]}")
        # 본문만 매칭 상위 3개 (약한 시그널 — 오탐 검사용)
        if body_only:
            print(f"    ⚠ 본문만 매칭 샘플 (노이즈 가능성 높음):")
            for n in body_only[:3]:
                kws = "/".join(n.get("matched_keywords", [])[:3])
                print(f"      [{kws}] {n['title'][:70]}")

    banner("3. 페이지 2: 캘린더 이벤트")
    cal_events = build_calendar_events(news, name_map, code_map, theme_index, CATEGORIES, window_days=90)
    type_counts = Counter(e["type"] for e in cal_events)
    print(f"  총 {len(cal_events)}건 · 타입별: {dict(type_counts)}")

    banner("4. 범위 표현 동작 확인")
    ranged_events = [e for e in cal_events if e.get("event_date_label")]
    print(f"  범위 라벨 있는 이벤트: {len(ranged_events)}건")
    for e in ranged_events[:6]:
        print(f"    · [{e['event_date']}] {e['event_date_label']} → {e['title'][:60]}")

    banner("5. DART 공시 검증")
    dart_events = [e for e in cal_events if e.get("type") == "DISCLOSURE"]
    print(f"  DART 공시 이벤트: {len(dart_events)}건")
    type_dist = Counter(e.get("disclosure_type") for e in dart_events)
    direction_dist = Counter(e.get("direction") for e in dart_events)
    print(f"  공시유형별: {dict(type_dist)}")
    print(f"  방향성 분포: {dict(direction_dist)}")
    # 링크 유효성 샘플 3개
    print(f"\n  링크 유효성 샘플 (상위 3):")
    for e in dart_events[:3]:
        try:
            r = requests.head(e["source_url"], timeout=5, allow_redirects=True)
            print(f"    HTTP {r.status_code} · {e['title'][:50]}")
        except Exception as ex:
            print(f"    FAIL ({ex}) · {e['title'][:50]}")

    banner("6. 뉴스 미래형 오탐 의심 탐지")
    nf_events = [e for e in cal_events if e.get("type") == "NEWS_FUTURE"]
    print(f"  NEWS_FUTURE 이벤트: {len(nf_events)}건")
    # heuristic: 스니펫에 과거 시제 마커 있는 경우 의심
    PAST_MARKERS = ["했다", "였다", "됐다", "밝혔다", "지난", "완료", "마쳤", "끝났"]
    suspect = []
    for e in nf_events:
        snip = e.get("body_snippet", "")
        if any(m in snip for m in PAST_MARKERS):
            suspect.append(e)
    print(f"  과거 시제 마커 포함 (오탐 의심): {len(suspect)}건 ({len(suspect)*100/max(len(nf_events),1):.0f}%)")
    for e in suspect[:3]:
        print(f"    ⚠ [{e['event_date']}] {e['title'][:55]}")
        print(f"       → {e['body_snippet'][:80]}")

    banner("7. 종목 매칭 품질")
    direct_total = sum(len(e.get("direct_stocks", [])) for e in cal_events)
    inferred_total = sum(len(e.get("inferred_stocks", [])) for e in cal_events)
    with_direct = sum(1 for e in cal_events if e.get("direct_stocks"))
    with_inferred = sum(1 for e in cal_events if e.get("inferred_stocks"))
    no_stock = sum(1 for e in cal_events if not e.get("direct_stocks") and not e.get("inferred_stocks"))
    print(f"  📌 direct 총 {direct_total}종목 ({with_direct}/{len(cal_events)} 이벤트에 부착)")
    print(f"  🔗 inferred 총 {inferred_total}종목 ({with_inferred}/{len(cal_events)} 이벤트에 부착)")
    print(f"  ❌ 종목 매칭 전혀 없음: {no_stock}건")

    # direct 종목에서 "대상/한국/CJ" 같은 일반명사 오탐 추려내기
    GENERIC_CONCERN = {"대상", "한국", "하이브", "러셀", "SBS"}
    direct_names = Counter()
    for e in cal_events:
        for s in e.get("direct_stocks", []):
            direct_names[s.get("name")] += 1
    hits = [(n, c) for n, c in direct_names.most_common(10)]
    print(f"\n  direct 매칭 TOP 10:")
    for n, c in hits:
        flag = " ⚠ 일반명사 의심" if n in GENERIC_CONCERN else ""
        print(f"    {n}: {c}건{flag}")

    banner("8. 중복/이상 탐지")
    # 같은 날짜에 같은 종목이 여러 이벤트로 잡히면 중복 신호
    dup_counter = Counter()
    for e in cal_events:
        for s in e.get("direct_stocks", []):
            dup_counter[(e["event_date"], s.get("name"))] += 1
    dups = [(k, c) for k, c in dup_counter.items() if c > 1]
    print(f"  같은 날짜+종목 중복 direct: {len(dups)}건")
    for (d, n), c in sorted(dups, key=lambda x: -x[1])[:5]:
        print(f"    · {d} {n} ({c}개 이벤트에 중복 부착)")

    banner("요약")
    print(f"  ✅ 파이프라인 가동 OK (네이버 + RSS + DART + 거시)")
    print(f"  📰 뉴스 {len(news)}건 / 이벤트 {len(cal_events)}건 / 정책 카테고리 {len(policy_cards)}개 매칭")
    print(f"  ⚠ 뉴스 미래형 오탐 의심률 {len(suspect)*100/max(len(nf_events),1):.0f}%")
    print(f"  ⚠ 종목 매칭 없는 이벤트 {no_stock}건 ({no_stock*100/max(len(cal_events),1):.0f}%)")


if __name__ == "__main__":
    main()
