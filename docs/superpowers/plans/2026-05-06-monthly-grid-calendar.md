# 월간 그리드 캘린더 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 다가올 이벤트 캘린더 탭(`news_calendar.html`)에 월간 그리드(이번 달 + 다음 달) 시각화를 페이지 상단에 추가한다. 기존 리스트 뷰는 하단에 유지하고, 그리드 셀 클릭 시 anchor link로 같은 페이지의 해당 날짜로 스크롤된다.

**Architecture:** `app/calendar_page.py` 안에 `_render_month_grid()` 헬퍼 추가. `render_calendar_html()` 시그니처에 `show_month_grid: bool = False, today: date | None = None` 두 인자 추가하여 기본값(False)일 땐 기존 동작 유지(다트공시 탭 회귀 방지). `_render_date_groups()` 의 `<div class="date-group">` 에 `id="date-YYYY-MM-DD"` 속성을 부여하여 anchor target 제공.

**Tech Stack:** Python 3.11, 정적 HTML/CSS (JS 0줄), pytest.

---

## File Structure

| 파일 | 변경 종류 | 책임 |
|---|---|---|
| `app/calendar_page.py` | 수정 | `_render_month_grid()` 추가, `render_calendar_html()` 시그니처/내부 수정, `_render_date_groups()` 에 id 부여, 그리드 CSS 추가 |
| `app/run_pages.py` | 수정 | `news_calendar.html` 빌드 호출에 `show_month_grid=True, today=date.today()` 전달 |
| `app/test_calendar_grid.py` | 신규 | `_render_month_grid()` 단위 테스트 |

각 task는 TDD로 진행: 먼저 테스트 작성 → 실패 확인 → 구현 → 통과 확인 → 커밋.

---

## Task 1: `_render_month_grid()` 기본 구조 (빈 그리드)

**Files:**
- Modify: `app/calendar_page.py` (private 헬퍼 함수 추가)
- Test: `app/test_calendar_grid.py` (신규)

이번 task는 이벤트 0건 입력에 대해 그리드 뼈대(헤더 + leading 빈칸 + 날짜 셀)를 생성하는 부분만 만든다. 이벤트 표시는 Task 3에서 추가.

- [ ] **Step 1: 테스트 파일 만들고 실패 테스트 작성**

`app/test_calendar_grid.py`:
```python
"""월간 그리드 캘린더 단위 테스트 (calendar_page._render_month_grid)."""

from datetime import date

from calendar_page import _render_month_grid


def test_empty_grid_has_correct_cell_count():
    """이벤트 0건 입력 시: leading 빈칸 + 그 달 일수 = 총 셀 개수."""
    # 2026년 5월: 1일이 금요일 (월=0 기준 weekday=4) → leading 4칸
    # 5월 31일까지 → 31칸
    # 총 35칸
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    assert html.count('class="cell') == 35


def test_grid_has_month_title():
    """그리드 상단에 '2026년 5월' 같은 제목."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    assert "2026년 5월" in html


def test_grid_has_weekday_header_monday_first():
    """헤더는 월요일부터: 월 화 수 목 금 토 일."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2026, 5, today)
    # 헤더는 grid-header div 안에 7개 span
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
```

- [ ] **Step 2: 테스트 실행해서 import error/함수 없음으로 실패하는지 확인**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v`
Expected: FAIL — `ImportError: cannot import name '_render_month_grid' from 'calendar_page'`

- [ ] **Step 3: `_render_month_grid()` 최소 구현**

`app/calendar_page.py` 의 적당한 위치(예: `_render_event_card()` 함수 정의 직전, 라인 910 부근)에 추가:

```python
import calendar as _stdlib_calendar


def _render_month_grid(events_by_date: dict, year: int, month: int, today: date) -> str:
    """한 달치 월간 그리드 HTML 한 덩어리 반환.

    events_by_date: {"YYYY-MM-DD": [event, ...]} — 이미 type_order 정렬되어 있다고 가정
    year, month: 표시할 월
    today: 오늘 강조 판정용
    """
    # 첫째 날의 weekday (월=0, 일=6)
    first_weekday = date(year, month, 1).weekday()
    days_in_month = _stdlib_calendar.monthrange(year, month)[1]

    cells: list[str] = []

    # leading 빈 셀
    for _ in range(first_weekday):
        cells.append('<div class="cell empty"></div>')

    # 날짜 셀 (이번 task에선 단순 .cell + cell-date 만)
    for day in range(1, days_in_month + 1):
        cells.append(f'<div class="cell"><div class="cell-date">{day}</div></div>')

    cells_html = "".join(cells)

    return (
        f'<div class="month-grid">'
        f'<h2 class="grid-month-title">{year}년 {month}월</h2>'
        f'<div class="grid-header">'
        f'<span>월</span><span>화</span><span>수</span><span>목</span>'
        f'<span>금</span><span class="sat">토</span><span class="sun">일</span>'
        f'</div>'
        f'<div class="grid-body">{cells_html}</div>'
        f'</div>'
    )
```

`from datetime import date` 는 파일 상단에 이미 있음 — 확인하고 없으면 추가. `import calendar as _stdlib_calendar` 도 새로 추가.

- [ ] **Step 4: 테스트 다시 실행, 4건 모두 통과 확인**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v`
Expected: 4 passed

- [ ] **Step 5: 커밋**

```bash
cd /Users/happily23/Documents/work/study/news-events
git add app/calendar_page.py app/test_calendar_grid.py
git commit -m "feat(calendar): _render_month_grid skeleton (header + empty cells)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 과거 / 오늘 / 토일 클래스 부여

**Files:**
- Modify: `app/calendar_page.py:_render_month_grid()`
- Modify: `app/test_calendar_grid.py`

- [ ] **Step 1: 추가 실패 테스트 작성**

`app/test_calendar_grid.py` 끝에 추가:
```python
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
```

- [ ] **Step 2: 테스트 실행, 새 4건 실패 확인**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v`
Expected: 첫 4건 PASS, 새 4건 FAIL

- [ ] **Step 3: `_render_month_grid()` 수정 — 클래스 부여 로직 추가**

`app/calendar_page.py` 의 날짜 셀 생성 루프 부분을 다음으로 교체:

```python
    # 날짜 셀
    for day in range(1, days_in_month + 1):
        cell_date = date(year, month, day)
        weekday = cell_date.weekday()  # 월=0..일=6

        cell_classes = ["cell"]
        if cell_date < today:
            cell_classes.append("past")
        elif cell_date == today:
            cell_classes.append("today")

        date_classes = ["cell-date"]
        if weekday == 5:
            date_classes.append("sat")
        elif weekday == 6:
            date_classes.append("sun")

        cell_class_attr = " ".join(cell_classes)
        date_class_attr = " ".join(date_classes)

        cells.append(
            f'<div class="{cell_class_attr}">'
            f'<div class="{date_class_attr}">{day}</div>'
            f'</div>'
        )
```

- [ ] **Step 4: 테스트 통과 확인 (총 8건 PASS)**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v`
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
cd /Users/happily23/Documents/work/study/news-events
git add app/calendar_page.py app/test_calendar_grid.py
git commit -m "feat(calendar): mark past/today/sat/sun classes on grid cells

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 셀 안 이벤트 표시 + anchor link

**Files:**
- Modify: `app/calendar_page.py:_render_month_grid()`
- Modify: `app/test_calendar_grid.py`

- [ ] **Step 1: 실패 테스트 추가**

`app/test_calendar_grid.py` 끝에 추가:
```python
def _make_event(event_date: str, type_: str, title: str, icon: str = "") -> dict:
    """테스트 fixture 헬퍼."""
    return {
        "event_date": event_date,
        "type": type_,
        "title": title,
        "icon": icon,
    }


def test_cell_with_single_event_shows_icon_and_title():
    """이벤트 1건인 셀: 아이콘 + 제목, '+N건' 없음."""
    events_by_date = {
        "2026-05-12": [_make_event("2026-05-12", "MACRO", "5월 FOMC", "🌐")],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert "🌐 5월 FOMC" in html
    # +N건은 0건이라 안 나타남
    assert "+1건" not in html


def test_cell_with_multiple_events_shows_first_and_more_count():
    """이벤트 3건인 셀: 첫 건 표시 + '+2건'."""
    events_by_date = {
        "2026-05-12": [
            _make_event("2026-05-12", "MACRO", "5월 FOMC", "🌐"),
            _make_event("2026-05-12", "NEWS_FUTURE", "삼성 실적", "📰"),
            _make_event("2026-05-12", "NEWS_FUTURE", "현대 IR", "📰"),
        ],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert "🌐 5월 FOMC" in html
    assert "+2건" in html


def test_event_cell_is_anchor_link():
    """이벤트 있는 셀은 <a href="#date-YYYY-MM-DD"> 형태."""
    events_by_date = {
        "2026-05-12": [_make_event("2026-05-12", "MACRO", "FOMC", "🌐")],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert 'href="#date-2026-05-12"' in html
    assert 'class="cell has-events' in html


def test_event_cell_today_has_both_today_and_has_events():
    """오늘 이벤트 있으면: today + has-events 둘 다, 그리고 anchor."""
    events_by_date = {
        "2026-05-06": [_make_event("2026-05-06", "MACRO", "한은 금통위", "🏦")],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert 'href="#date-2026-05-06"' in html
    assert "today" in html
    assert "has-events" in html


def test_html_escape_in_event_title():
    """제목에 특수문자 들어가면 escape 처리."""
    events_by_date = {
        "2026-05-12": [_make_event("2026-05-12", "MACRO", "<script>alert(1)</script>", "🌐")],
    }
    today = date(2026, 5, 6)
    html = _render_month_grid(events_by_date, 2026, 5, today)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
```

- [ ] **Step 2: 테스트 실행, 5건 실패 확인**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v`
Expected: 첫 8건 PASS, 새 5건 FAIL

- [ ] **Step 3: 셀 생성 로직에 이벤트 표시 + anchor 추가**

`app/calendar_page.py` 의 날짜 셀 루프를 다음으로 교체:

```python
    # 날짜 셀
    for day in range(1, days_in_month + 1):
        cell_date = date(year, month, day)
        weekday = cell_date.weekday()
        date_iso = cell_date.isoformat()

        cell_classes = ["cell"]
        if cell_date < today:
            cell_classes.append("past")
        elif cell_date == today:
            cell_classes.append("today")

        date_classes = ["cell-date"]
        if weekday == 5:
            date_classes.append("sat")
        elif weekday == 6:
            date_classes.append("sun")

        date_html = f'<div class="{" ".join(date_classes)}">{day}</div>'

        day_events = events_by_date.get(date_iso) or []
        if day_events:
            cell_classes.append("has-events")
            first = day_events[0]
            icon = first.get("icon") or ""
            title = _html_escape(first.get("title", ""))
            head = f'{icon} {title}'.strip()
            extra = len(day_events) - 1
            more = f'<br><span class="more">+{extra}건</span>' if extra > 0 else ''
            events_html = f'<div class="cell-events">{head}{more}</div>'
            cells.append(
                f'<a class="{" ".join(cell_classes)}" href="#date-{date_iso}">'
                f'{date_html}{events_html}</a>'
            )
        else:
            cells.append(
                f'<div class="{" ".join(cell_classes)}">{date_html}</div>'
            )
```

**중요**: 이벤트 셀은 `<div>` 가 아니라 `<a>` 태그로 변경됨. 빈 셀은 그대로 `<div>`.

- [ ] **Step 4: 테스트 통과 확인 (총 13건 PASS)**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v`
Expected: 13 passed

기존 cell count 테스트가 깨지지 않았는지 확인 — `class="cell` 검색은 `<a class="cell ..."` 도 매칭해야 함 (현재 검색 패턴 그대로 작동).

- [ ] **Step 5: 커밋**

```bash
cd /Users/happily23/Documents/work/study/news-events
git add app/calendar_page.py app/test_calendar_grid.py
git commit -m "feat(calendar): render events in grid cells with anchor links

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 12월 → 다음 해 1월 넘김 처리 + 모든 셀이 미래

**Files:**
- Modify: `app/test_calendar_grid.py`

`_render_month_grid()` 자체는 (year, month) 입력만 받고 12월 처리를 안 함 — 그 책임은 호출자(다음 task의 `render_calendar_html`)에 있음. 여기선 12월 입력에 대해 그리드 자체가 정상 동작하는지만 회귀 방지 테스트로 추가.

- [ ] **Step 1: 12월 그리드 회귀 테스트 추가**

`app/test_calendar_grid.py` 끝에 추가:
```python
def test_december_grid_renders_correctly():
    """2026년 12월: 1일이 화요일(weekday=1) → leading 1칸. 31일까지."""
    today = date(2026, 5, 6)  # 오늘이 5월이라도 12월 그리드 렌더 가능
    html = _render_month_grid({}, 2026, 12, today)
    # leading 1 + 31 days = 32 cells
    assert html.count('class="cell') == 32
    # 12월 셀은 모두 미래 → past/today 없음
    assert 'class="cell past"' not in html
    assert "2026년 12월" in html


def test_january_2027_grid_renders():
    """2027년 1월 (다음 해): 1일이 금요일(weekday=4) → leading 4칸. 31일까지."""
    today = date(2026, 5, 6)
    html = _render_month_grid({}, 2027, 1, today)
    assert html.count('class="cell') == 35
    assert "2027년 1월" in html
```

- [ ] **Step 2: 테스트 실행 — 통과해야 함 (구현 변경 없이도 정상 동작)**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v`
Expected: 15 passed

만약 실패하면 `_render_month_grid()` 의 weekday/days_in_month 계산 로직 점검.

- [ ] **Step 3: 커밋**

```bash
cd /Users/happily23/Documents/work/study/news-events
git add app/test_calendar_grid.py
git commit -m "test(calendar): regression tests for december/cross-year grids

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: `render_calendar_html()` 시그니처 확장 + 그리드 prepend

**Files:**
- Modify: `app/calendar_page.py:render_calendar_html()` (라인 1093 부근)
- Modify: `app/calendar_page.py:_render_date_groups()` (라인 1100 부근)

- [ ] **Step 1: `_render_date_groups()` 수정 — date-group div에 id 부여**

현재 라인 1108-1110:
```python
sections.append(
    f'<div class="date-group"><div class="date-label">{label}</div>{cards_html}</div>'
)
```

다음으로 교체:
```python
sections.append(
    f'<div class="date-group" id="date-{d}">'
    f'<div class="date-label">{label}</div>{cards_html}'
    f'</div>'
)
```

`d` 는 이미 `YYYY-MM-DD` 형식 (`for d in sorted(groups.keys())`).

- [ ] **Step 2: `render_calendar_html()` 시그니처 확장**

라인 1093-1096 의 함수 시그니처를:
```python
def render_calendar_html(events: list[dict],
                         page_title: str = "다가올 이벤트 캘린더",
                         page_icon: str = "📅",
                         page_subtitle: str = "향후 30일") -> str:
```

다음으로 교체:
```python
def render_calendar_html(events: list[dict],
                         page_title: str = "다가올 이벤트 캘린더",
                         page_icon: str = "📅",
                         page_subtitle: str = "향후 30일",
                         show_month_grid: bool = False,
                         today: "date | None" = None) -> str:
```

- [ ] **Step 3: `render_calendar_html()` 안에서 그리드 prepend**

라인 1113-1129 의 `body` 계산 직후 (`body = main_html + low_html` 다음 줄)에 다음을 삽입:

```python
    if show_month_grid and events:
        from collections import defaultdict as _dd
        today_d = today or date.today()
        events_by_date: dict[str, list[dict]] = _dd(list)
        type_order = {"MACRO": 0, "NEWS_FUTURE": 1, "DISCLOSURE": 2}
        for e in events:
            events_by_date[e["event_date"]].append(e)
        for d in events_by_date:
            events_by_date[d].sort(key=lambda e: type_order.get(e.get("type"), 9))

        # 이번 달 + 다음 달
        next_year = today_d.year + (1 if today_d.month == 12 else 0)
        next_month = 1 if today_d.month == 12 else today_d.month + 1
        grid_html = (
            '<section class="month-grid-section">'
            + _render_month_grid(dict(events_by_date), today_d.year, today_d.month, today_d)
            + _render_month_grid(dict(events_by_date), next_year, next_month, today_d)
            + '</section>'
        )
        body = grid_html + body
```

- [ ] **Step 4: 회귀 확인 — 다트공시 호출(기본값) 은 그리드 없이 렌더되는지 unit test 로 확인**

`app/test_calendar_grid.py` 끝에 추가:
```python
def test_render_calendar_html_default_has_no_grid():
    """show_month_grid=False (기본) 일 때 그리드 섹션이 없어야 함."""
    from calendar_page import render_calendar_html

    html = render_calendar_html(
        events=[],
        page_title="test",
        page_icon="X",
        page_subtitle="x",
    )
    assert "month-grid-section" not in html


def test_render_calendar_html_with_grid_shows_two_grids():
    """show_month_grid=True 면 그리드 2개 (이번 달 + 다음 달)."""
    from calendar_page import render_calendar_html

    events = [
        {"event_date": "2026-05-12", "type": "MACRO", "title": "FOMC",
         "icon": "🌐", "low_signal": False, "direct_stocks": [],
         "matched_categories": [], "body_snippet": ""},
    ]
    html = render_calendar_html(
        events,
        page_title="test", page_icon="📅", page_subtitle="x",
        show_month_grid=True, today=date(2026, 5, 6),
    )
    assert "month-grid-section" in html
    assert "2026년 5월" in html
    assert "2026년 6월" in html


def test_render_calendar_html_grid_in_december_crosses_year():
    """12월일 때 다음 그리드는 다음 해 1월."""
    from calendar_page import render_calendar_html

    events = [
        {"event_date": "2026-12-15", "type": "MACRO", "title": "FOMC",
         "icon": "🌐", "low_signal": False, "direct_stocks": [],
         "matched_categories": [], "body_snippet": ""},
    ]
    html = render_calendar_html(
        events,
        page_title="test", page_icon="📅", page_subtitle="x",
        show_month_grid=True, today=date(2026, 12, 5),
    )
    assert "2026년 12월" in html
    assert "2027년 1월" in html


def test_render_calendar_html_date_group_has_id_attribute():
    """리스트 섹션의 date-group 에 id="date-YYYY-MM-DD" 부여 (anchor target)."""
    from calendar_page import render_calendar_html

    events = [
        {"event_date": "2026-05-12", "type": "MACRO", "title": "FOMC",
         "icon": "🌐", "low_signal": False, "direct_stocks": [],
         "matched_categories": [], "body_snippet": ""},
    ]
    html = render_calendar_html(events, show_month_grid=False)  # 그리드 안 켜도 id는 부여
    assert 'id="date-2026-05-12"' in html
```

- [ ] **Step 5: 테스트 실행, 모두 통과 확인 (총 19건)**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v`
Expected: 19 passed

만약 `_render_event_card` 등 dependency 미스로 실패하면 fixture에 빠진 키 추가.

- [ ] **Step 6: 커밋**

```bash
cd /Users/happily23/Documents/work/study/news-events
git add app/calendar_page.py app/test_calendar_grid.py
git commit -m "feat(calendar): wire month grid into render_calendar_html

show_month_grid=True 시 이번 달 + 다음 달 그리드를 페이지 상단에 prepend.
date-group div에 id=\"date-YYYY-MM-DD\" 부여하여 anchor link 타겟으로 사용.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: 그리드 CSS 추가

**Files:**
- Modify: `app/calendar_page.py` (HTML template 안의 `<style>` 블록)

`render_calendar_html()` 의 inline CSS 블록(라인 1136 부근부터 1300 가량까지)에 그리드 스타일을 추가한다. 위치는 `.date-group` 스타일 정의 직전(라인 1158 근처)이 자연스러움.

- [ ] **Step 1: CSS 블록 추가**

`render_calendar_html()` 의 `<style>` 안, `.date-group {{ margin-bottom:26px; }}` 라인 직전에 다음을 추가:

```css
  /* === 월간 그리드 === */
  .month-grid-section {{ margin-bottom:24px; }}
  .month-grid {{ margin-bottom:16px; }}
  .grid-month-title {{
    font-size:18px; font-weight:700; margin:0 0 8px;
    color:var(--text);
  }}
  .grid-header {{
    display:grid; grid-template-columns:repeat(7, 1fr);
    font-size:11px; color:var(--muted); padding:6px 0;
    border-bottom:1px solid var(--border); text-align:center;
  }}
  .grid-header span {{ font-weight:600; }}
  .grid-header .sat {{ color:#2563eb; }}
  .grid-header .sun {{ color:#dc2626; }}
  .grid-body {{
    display:grid; grid-template-columns:repeat(7, 1fr);
    gap:1px; background:var(--border);
  }}
  .cell {{
    background:var(--card); min-height:64px;
    padding:4px 6px; font-size:11px;
    display:block; text-decoration:none; color:inherit;
  }}
  .cell-date {{ font-weight:600; color:#555; }}
  .cell-date.sat {{ color:#2563eb; }}
  .cell-date.sun {{ color:#dc2626; }}
  .cell.past {{ background:#f5f5f5; opacity:0.5; }}
  .cell.empty {{ background:#fafafa; }}
  .cell.today {{ background:#fff8e1; box-shadow:inset 0 0 0 2px #f59e0b; }}
  .cell.has-events:hover {{ background:var(--accent-soft); }}
  .cell-events {{
    margin-top:2px; line-height:1.3;
    overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  }}
  .cell-events .more {{ color:#888; font-size:10px; }}

  @media (max-width:600px) {{
    .month-grid-section {{ display:none; }}
  }}
```

- [ ] **Step 2: 테스트 실행해서 회귀 없는지 확인**

Run: `cd /Users/happily23/Documents/work/study/news-events/app && python -m pytest test_calendar_grid.py -v && python -m pytest test_events.py -v`
Expected: All passed (19 + 기존 events 테스트)

- [ ] **Step 3: 커밋**

```bash
cd /Users/happily23/Documents/work/study/news-events
git add app/calendar_page.py
git commit -m "feat(calendar): add CSS for month grid + mobile hide

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `run_pages.py` 호출 측 수정

**Files:**
- Modify: `app/run_pages.py` (라인 95-98 부근, `news_calendar.html` 빌드 호출)

- [ ] **Step 1: `run_pages.py` 의 calendar 빌드 호출 수정**

라인 104-107 (현재 위치, `Page 3` 섹션):
```python
    cal_html = render_calendar_html(
        upcoming_events, page_title="다가올 이벤트 캘린더", page_icon="📅",
        page_subtitle="향후 30일",
    )
```

다음으로 교체:
```python
    cal_html = render_calendar_html(
        upcoming_events, page_title="다가올 이벤트 캘린더", page_icon="📅",
        page_subtitle="향후 30일",
        show_month_grid=True, today=today,
    )
```

(`today` 변수는 이미 라인 74 에서 `today = date.today()` 로 정의되어 있음.)

다트공시 빌드(`Page 2`, 라인 95-98)는 변경 없음 — 기본값 `show_month_grid=False` 로 그리드 prepend 안 됨, 회귀 없음.

- [ ] **Step 2: 시각 검증 — 로컬 프리뷰 빌드**

```bash
cd /Users/happily23/Documents/work/study/news-events
python app/run_event_preview.py
```

`/tmp/news_calendar.html` 을 브라우저로 열어서 다음을 확인:
- 페이지 상단에 5월 / 6월 그리드 두 개 (또는 현재 달 + 다음 달)
- 5/1~5/5 회색, 5/6 노랑 하이라이트
- 토 = 파랑, 일 = 빨강
- 이벤트 있는 셀의 첫 이벤트 제목 + "+N건" (있을 경우)
- 셀 클릭 → 같은 페이지 하단 리스트의 그 날짜로 스크롤
- 모바일 너비(devtools < 600px)에서 그리드 숨김

`/tmp/news_dart.html` 도 열어서 다트공시 탭은 기존대로 그리드 없이 리스트만 보이는지(회귀 없음) 확인.

- [ ] **Step 3: 커밋**

```bash
cd /Users/happily23/Documents/work/study/news-events
git add app/run_pages.py
git commit -m "feat(pages): enable month grid on upcoming calendar tab

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: main 푸시**

```bash
cd /Users/happily23/Documents/work/study/news-events
git pull --rebase --autostash origin main
git push origin main
```

푸시 후 `Build & Deploy Pages` 워크플로우가 자동 트리거되어 GitHub Pages 에 배포됨. 배포 후 실제 페이지에서도 그리드 동작 확인.

---

## 자기 검토 체크리스트

- [ ] 모든 task가 spec 의 결정사항(Q1~Q6)을 1:1 매핑하는가
- [ ] `_render_month_grid()` 시그니처가 task 1, 5 에서 동일한가 (events_by_date, year, month, today)
- [ ] 각 task가 TDD 패턴 (실패 테스트 → 구현 → 통과) 을 따르는가
- [ ] anchor target id (`date-YYYY-MM-DD`) 가 task 5 의 `_render_date_groups` 수정으로 보장되는가
- [ ] 다트공시 탭 회귀 방지가 명시되어 있는가 (기본값 False, task 5 단위 테스트, task 7 수동 검증)
