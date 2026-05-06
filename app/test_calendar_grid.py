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
