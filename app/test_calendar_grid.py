"""월간 그리드 캘린더 단위 테스트 (calendar_page._render_month_grid)."""

import re
from datetime import date

from calendar_page import _render_month_grid


def test_empty_grid_has_correct_cell_count():
    """이벤트 0건 입력 시: leading 빈칸 + 그 달 일수 = 총 셀 개수."""
    # 2026년 5월: 1일이 금요일 (월=0 기준 weekday=4) → leading 4칸
    # 5월 31일까지 → 31칸
    # 총 35칸
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    assert len(re.findall(r'class="cell[" ]', html)) == 35


def test_grid_has_month_title():
    """그리드 상단에 '2026년 5월' 같은 제목."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    assert "2026년 5월" in html


def test_grid_has_weekday_header_monday_first():
    """헤더는 월요일부터: 월 화 수 목 금 토 일."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    assert '<span>월</span>' in html
    assert '<span class="sat">토</span>' in html
    assert '<span class="sun">일</span>' in html
    # 월요일이 토요일보다 앞에 나옴
    assert html.index('<span>월</span>') < html.index('<span class="sat">토</span>')


def test_leading_blank_cells_for_first_week():
    """5월 1일이 금요일(월=0 기준 weekday=4)이면 월~목 4칸이 leading 빈칸."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    assert html.count('class="cell empty"') == 4


def test_today_cell_has_today_class():
    """오늘 날짜 셀에 'today' 클래스 부여."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    assert 'class="cell today"' in html


def test_past_cells_in_current_month_have_past_class():
    """이번 달 5/1~5/5는 'past' 클래스."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    # 5/1~5/5 = 5개 past 셀
    assert html.count('class="cell past"') == 5


def test_future_month_has_no_past_or_today():
    """다음 달(6월) 그리드는 모두 미래 → past/today 클래스 없음."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 6, today)
    assert 'class="cell past"' not in html
    assert 'class="cell today"' not in html


def test_saturday_sunday_date_classes():
    """토/일 셀의 cell-date 에 sat/sun 클래스. 5월의 토(2,9,16,23,30)와 일(3,10,17,24,31)."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    # 각 토요일/일요일 5번씩
    assert html.count('class="cell-date sat"') == 5
    assert html.count('class="cell-date sun"') == 5


def _make_event(event_date: str, type_: str, title: str) -> dict:
    """테스트 fixture 헬퍼."""
    return {
        "event_date": event_date,
        "type": type_,
        "title": title,
    }


def test_cell_with_single_event_shows_icon_and_title():
    """이벤트 1건인 셀: 아이콘 + 제목, '+N건' 없음."""
    events_by_date = {
        "2026-05-12": [_make_event("2026-05-12", "MACRO", "5월 FOMC")],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert "🌐 5월 FOMC" in html
    # +N건은 0건이라 안 나타남
    assert "+1건" not in html


def test_cell_with_three_events_shows_all_no_more_count():
    """이벤트 3건인 셀: 3건 모두 표시, '+N건' 없음."""
    events_by_date = {
        "2026-05-12": [
            _make_event("2026-05-12", "MACRO", "5월 FOMC"),
            _make_event("2026-05-12", "NEWS_FUTURE", "삼성 실적"),
            _make_event("2026-05-12", "NEWS_FUTURE", "현대 IR"),
        ],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert "🌐 5월 FOMC" in html
    assert "삼성 실적" in html
    assert "현대 IR" in html
    assert "건</div>" not in html or "+0건" not in html
    # +N건 표시 없음
    import re as _re
    assert _re.search(r"\+\d+건", html) is None


def test_cell_with_more_than_three_events_truncates_with_more_count():
    """이벤트 5건인 셀: 상위 3건만 보이고 '+2건' 표시."""
    events_by_date = {
        "2026-05-12": [
            _make_event("2026-05-12", "MACRO", "FOMC"),
            _make_event("2026-05-12", "NEWS_FUTURE", "삼성"),
            _make_event("2026-05-12", "NEWS_FUTURE", "현대"),
            _make_event("2026-05-12", "NEWS_FUTURE", "LG"),
            _make_event("2026-05-12", "NEWS_FUTURE", "SK"),
        ],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert "FOMC" in html
    assert "삼성" in html
    assert "현대" in html
    # 4번째/5번째는 안 보이고 +2건으로 처리
    assert "LG" not in html
    assert "SK" not in html
    assert "+2건" in html


def test_event_cell_is_anchor_link():
    """이벤트 있는 셀은 <a href="#date-YYYY-MM-DD"> 형태."""
    events_by_date = {
        "2026-05-12": [_make_event("2026-05-12", "MACRO", "FOMC")],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert 'href="#date-2026-05-12"' in html
    assert 'class="cell has-events' in html


def test_event_cell_today_has_both_today_and_has_events():
    """오늘 이벤트 있으면: today + has-events 둘 다, 그리고 anchor."""
    events_by_date = {
        "2026-05-06": [_make_event("2026-05-06", "MACRO", "한은 금통위")],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert 'href="#date-2026-05-06"' in html
    assert "today" in html
    assert "has-events" in html


def test_html_escape_in_event_title():
    """제목에 특수문자 들어가면 escape 처리."""
    events_by_date = {
        "2026-05-12": [_make_event("2026-05-12", "MACRO", "<script>alert(1)</script>")],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_december_grid_renders_correctly():
    """2026년 12월: 1일이 화요일(weekday=1) → leading 1칸. 31일까지 → 총 32칸."""
    today = date(2026, 5, 6)  # 오늘이 5월이라도 12월 그리드 렌더 가능
    html = _render_month_grid({}, 2026, 12, today)
    assert len(re.findall(r'class="cell[" ]', html)) == 32
    # 12월 셀은 모두 미래 → past/today 없음
    assert 'class="cell past"' not in html
    assert "2026년 12월" in html


def test_january_2027_grid_renders():
    """2027년 1월 (다음 해): 1일이 금요일(weekday=4) → leading 4칸. 31일까지 → 총 35칸."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2027, 1, today)
    assert len(re.findall(r'class="cell[" ]', html)) == 35
    assert "2027년 1월" in html


# ============================================================
# render_calendar_html 통합 테스트
# ============================================================

def _make_full_event(event_date: str, type_: str = "MACRO", title: str = "테스트 이벤트") -> dict:
    """render_calendar_html 호출용 fixture (low_signal/direct_stocks 등 필수 키 포함)."""
    return {
        "event_date": event_date,
        "type": type_,
        "title": title,
        "low_signal": False,
        "direct_stocks": [],
        "inferred_stocks": [],
        "matched_categories": [],
        "body_snippet": "",
        "source_url": "",
        "source_label": "",
        "icon": "",
    }


def test_render_calendar_html_default_has_no_grid():
    """show_month_grid=False (기본) 일 때 그리드 섹션이 없어야 함."""
    from calendar_page import render_calendar_html

    events = [_make_full_event("2026-05-12", "MACRO", "FOMC")]
    html = render_calendar_html(events)
    # CSS rule (.month-grid-section) 은 항상 들어있어도, 실제 <section> 태그는 없어야 함
    assert '<section class="month-grid-section">' not in html


def test_render_calendar_html_with_grid_shows_two_grids():
    """show_month_grid=True 면 그리드 2개 (이번 달 + 다음 달)."""
    from calendar_page import render_calendar_html

    events = [_make_full_event("2026-05-12", "MACRO", "FOMC")]
    html = render_calendar_html(
        events,
        show_month_grid=True,
        today=date(2026, 5, 6),
    )
    assert '<section class="month-grid-section">' in html
    assert "2026년 5월" in html
    assert "2026년 6월" in html


def test_render_calendar_html_grid_in_december_crosses_year():
    """12월일 때 다음 그리드는 다음 해 1월."""
    from calendar_page import render_calendar_html

    events = [_make_full_event("2026-12-15", "MACRO", "FOMC")]
    html = render_calendar_html(
        events,
        show_month_grid=True,
        today=date(2026, 12, 5),
    )
    assert "2026년 12월" in html
    assert "2027년 1월" in html


def test_render_calendar_html_date_group_has_id_attribute():
    """리스트 섹션의 date-group 에 id="date-YYYY-MM-DD" 부여 (anchor target)."""
    from calendar_page import render_calendar_html

    events = [_make_full_event("2026-05-12", "MACRO", "FOMC")]
    html = render_calendar_html(events)  # 그리드 안 켜도 id는 부여
    assert 'id="date-2026-05-12"' in html


def test_render_calendar_html_marks_dart_dilution_risk():
    from calendar_page import render_calendar_html

    event = _make_full_event("2026-05-12", "DISCLOSURE", "테스트사 전환사채권발행결정")
    event.update({
        "direction": "negative",
        "disclosure_type": "전환사채권발행결정",
        "source_label": "DART 공시",
    })

    html = render_calendar_html([event])

    assert "희석 리스크 높음" in html
    assert "CB·BW·유상증자" in html


def test_render_calendar_html_does_not_mark_split_as_dilution_risk():
    from calendar_page import render_calendar_html

    event = _make_full_event("2026-05-12", "DISCLOSURE", "테스트사 주식분할결정")
    event.update({
        "direction": "positive",
        "disclosure_type": "주식분할결정",
        "source_label": "DART 공시",
    })

    html = render_calendar_html([event])

    assert "희석 리스크 높음" not in html


def test_build_calendar_events_keeps_negative_dart_disclosures(monkeypatch):
    import calendar_page
    import bok_schedule
    import dart_disclosure
    import ecos_client
    import naver_supply

    monkeypatch.setattr(calendar_page, "extract_future_events_from_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(bok_schedule, "get_macro_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(dart_disclosure, "load_dart_key", lambda: "DART_KEY")
    monkeypatch.setattr(ecos_client, "load_ecos_key", lambda: None)
    monkeypatch.setattr(calendar_page, "attach_stocks_to_event", lambda event, *args, **kwargs: event)
    monkeypatch.setattr(naver_supply, "enrich_stocks_with_supply", lambda stocks, **kwargs: stocks)

    def fake_dart_events(api_key, today, past_window_days):
        return [{
            "type": "DISCLOSURE",
            "event_date": today.isoformat(),
            "title": "테스트사 · 유상증자결정",
            "body_snippet": "희석성 공시",
            "source_url": "",
            "source_label": "DART 공시",
            "direction": "negative",
            "flags": [],
            "direct_stocks": [],
            "inferred_stocks": [],
            "matched_categories": [],
        }]

    monkeypatch.setattr(dart_disclosure, "fetch_dart_target_events", fake_dart_events)

    events = calendar_page.build_calendar_events([], {}, {}, {}, [], window_days=30)

    assert [e["title"] for e in events] == ["테스트사 · 유상증자결정"]


def test_build_calendar_events_accepts_supply_max_calls(monkeypatch):
    import calendar_page
    import bok_schedule
    import dart_disclosure
    import ecos_client
    import naver_supply

    monkeypatch.setattr(calendar_page, "extract_future_events_from_news", lambda *args, **kwargs: [{
        "type": "NEWS_FUTURE",
        "event_date": date.today().isoformat(),
        "title": "테스트 이벤트",
        "direction": "neutral",
        "flags": [],
        "direct_stocks": [{"code": "005930", "name": "삼성전자"}],
        "inferred_stocks": [],
        "matched_categories": ["반도체"],
    }])
    monkeypatch.setattr(bok_schedule, "get_macro_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(dart_disclosure, "load_dart_key", lambda: None)
    monkeypatch.setattr(ecos_client, "load_ecos_key", lambda: None)
    monkeypatch.setattr(calendar_page, "attach_stocks_to_event", lambda event, *args, **kwargs: event)

    supply_calls = []

    def fake_enrich(stocks, **kwargs):
        supply_calls.append(kwargs)
        return stocks

    monkeypatch.setattr(naver_supply, "enrich_stocks_with_supply", fake_enrich)

    calendar_page.build_calendar_events([], {}, {}, {}, [], window_days=30, supply_max_calls=0)

    assert supply_calls[0]["max_calls"] == 0
