# 월간 그리드 캘린더 (다가올 이벤트 탭) — 디자인 스펙

**작성일**: 2026-05-06
**대상 페이지**: `news_calendar.html` (다가올 이벤트 캘린더 탭)
**스코프**: 월간 그리드 시각화를 페이지 상단에 추가, 기존 리스트 뷰는 하단에 유지

---

## 1. 배경 / 목적

현재 다가올 이벤트 캘린더 탭은 시간순 리스트 뷰만 제공한다. 리스트는 "오늘부터 차례로 뭐가 있나"를 보기엔 좋지만 "이번달 어디에 이벤트가 몰려있나" 같은 분포 패턴 인식이 어렵다. 월간 그리드 뷰를 페이지 상단에 추가해 분포 시각화 가치를 보강한다.

**기존 결정과의 일관성:** 직전 커밋(`4ebb717`)에서 DART 이벤트는 다트공시 탭 전담으로 캘린더 탭에서 완전 제외했다. 본 스펙은 그 결정을 유지한다 — 그리드에 표시되는 이벤트도 macro + 뉴스 미래형만이며 DART는 등장하지 않는다.

---

## 2. 사용자 결정사항

브레인스토밍에서 확정된 사항:

| Q | 선택 |
|---|---|
| 그리드 위치 | 다가올 캘린더 탭 안에 그리드 상단 + 리스트 하단 (별도 탭 X) |
| 표시 범위 | 이번 달 + 다음 달 = 그리드 2개 세로 스택 |
| 셀 콘텐츠 | 아이콘 + 첫 이벤트 제목(잘림) + "+N건" |
| 클릭 동작 | 셀 → 같은 페이지 하단 리스트의 해당 날짜로 anchor scroll |
| 과거 / 오늘 | 과거는 회색 빈 셀, 오늘은 노랑 배경 + 굵은 테두리 |
| 모바일 | 그리드 숨김(리스트만 보이게), `@media (max-width: 600px)` |
| 주 시작 | **월요일** (월 화 수 목 금 토 일) |
| 토/일 컬러 | 토 = 파랑, 일 = 빨강 (헤더 + 셀 날짜) |

---

## 3. 아키텍처 / 변경 범위

- **단일 파일 수정**: `app/calendar_page.py`
- 새 헬퍼 함수: `_render_month_grid(events_by_date, year, month, today) -> str`
  - 한 달 분 그리드 HTML 한 덩어리 반환
  - 입력: `dict[YYYY-MM-DD, list[event]]` 형태로 미리 bucket된 events
  - 입력: 표시할 `(year, month)` 와 `today` (오늘 강조 판정용)
- 기존 `render_calendar_html(events, page_title, page_icon, page_subtitle) -> str` 시그니처 **유지**
  - 함수 내부에서 events를 날짜별로 bucket → 이번 달 / 다음 달 두 번 `_render_month_grid()` 호출 → 결과 HTML을 페이지 본문 상단에 prepend
- 리스트 섹션은 기존 렌더 로직 그대로 — 단, 날짜 그룹 헤더에 `id="date-YYYY-MM-DD"` 속성만 추가
- CSS는 기존 컨벤션대로 동일 함수 안 inline `<style>` 블록에 추가
- **JS 0줄** — anchor link (`<a href="#date-...">`) 만 사용

다트공시 탭 (`news_dart.html`) 은 이번 라운드 변경 없음. `render_calendar_html()` 은 두 탭에서 공유되는 함수이지만, 그리드 prepend는 호출 인자(예: `show_grid=True`)로 게이트하거나 별도 변형 함수 분리. **추천: 호출 측 (`run_pages.py`) 에서 그리드용/리스트 전용 분기 처리하여 한 함수가 양쪽 모드 다 지원**.

```python
# 시그니처 안:
def render_calendar_html(events, page_title, page_icon, page_subtitle,
                        show_month_grid: bool = False,
                        today: date | None = None) -> str:
    ...
```

`run_pages.py` 의 호출 측에서:
- 다가올 캘린더 (`news_calendar.html`): `show_month_grid=True, today=date.today()`
- 다트공시 (`news_dart.html`): 기본값 (False) — 변경 없음

---

## 4. 데이터 흐름

```
build_calendar_events(news, ...)
  ↓
events: list[dict]  (오늘 ~ 12/31, DART 제외)
  ↓
render_calendar_html(events, ..., show_month_grid=True, today=...)
  ↓
  if show_month_grid:
    events_by_date = {}
    for e in events:
        events_by_date.setdefault(e["event_date"], []).append(e)
    # type_order 로 각 bucket 내부 정렬
    for d in events_by_date:
        events_by_date[d].sort(key=lambda e: type_order.get(e["type"], 9))

    grids_html = (
        _render_month_grid(events_by_date, today.year, today.month, today)
        + _render_month_grid(events_by_date, *next_month(today), today)
    )

    # 페이지 상단에 grids_html prepend
  ↓
  기존 list 렌더 로직 (각 날짜 그룹 헤더에 id="date-YYYY-MM-DD" 부여)
  ↓
  최종 HTML
```

**single source of truth**: events list 한 벌만 받아서 그리드와 리스트 모두 만든다. 정렬/필터는 이미 `build_calendar_events()` 단계에서 끝난 결과 사용.

`next_month(today)` 헬퍼:
- `today.month == 12` → `(today.year + 1, 1)`
- 그 외 → `(today.year, today.month + 1)`

---

## 5. HTML / CSS 구조

### 5.1 HTML

```html
<section class="month-grid-section">
  <div class="month-grid">
    <h2 class="grid-month-title">2026년 5월</h2>
    <div class="grid-header">
      <span>월</span><span>화</span><span>수</span><span>목</span>
      <span>금</span><span class="sat">토</span><span class="sun">일</span>
    </div>
    <div class="grid-body">
      <!-- leading 빈칸 (1일 weekday 만큼) -->
      <div class="cell empty"></div>
      <div class="cell empty"></div>
      ...
      <!-- 과거 (오늘 전, 이번달) -->
      <div class="cell past"><div class="cell-date">1</div></div>
      ...
      <!-- 오늘 -->
      <a class="cell today has-events" href="#date-2026-05-06">
        <div class="cell-date">6</div>
        <div class="cell-events">📰 한은 금통위<br><span class="more">+2건</span></div>
      </a>
      <!-- 미래 + 이벤트 있음 -->
      <a class="cell has-events" href="#date-2026-05-12">
        <div class="cell-date sat">12</div>
        <div class="cell-events">🌐 FOMC<br><span class="more">+1건</span></div>
      </a>
      <!-- 미래 + 이벤트 없음 -->
      <div class="cell"><div class="cell-date">13</div></div>
      ...
    </div>
  </div>

  <!-- 다음 달 그리드 동일 구조 -->
  <div class="month-grid">
    <h2 class="grid-month-title">2026년 6월</h2>
    ...
  </div>
</section>

<!-- 기존 리스트 섹션 -->
<section class="event-list-section">
  <h3 id="date-2026-05-06" class="list-date-header">5월 6일 (수)</h3>
  ...이벤트 카드...

  <h3 id="date-2026-05-12" class="list-date-header">5월 12일 (화)</h3>
  ...
</section>
```

**셀별 클래스 부여 규칙:**
- `empty`: leading 빈칸 (해당 월의 1일 이전 자리)
- `past`: 같은 달 내 과거 날짜 (1일 ~ 어제)
- `today`: 오늘 (`year/month/day == today.year/month/day`)
- `has-events`: 그 날 이벤트 1건 이상 존재 (이때만 `<a>` 태그, 아니면 `<div>`)
- `sat` / `sun`: 토요일/일요일 셀 (날짜 컬러용 — `.cell-date` 에 적용 또는 cell 자체에)

### 5.2 CSS (요지)

```css
.month-grid-section { margin-bottom: 24px; }
.month-grid { margin-bottom: 16px; }
.grid-month-title { font-size: 18px; font-weight: 700; margin: 0 0 8px; }

.grid-header {
  display: grid; grid-template-columns: repeat(7, 1fr);
  font-size: 11px; color: #666; padding: 4px 0;
  border-bottom: 1px solid #ddd;
}
.grid-header span { text-align: center; }
.grid-header .sat { color: #2563eb; }
.grid-header .sun { color: #dc2626; }

.grid-body {
  display: grid; grid-template-columns: repeat(7, 1fr);
  gap: 1px; background: #e5e5e5;
}
.cell {
  background: #fff; min-height: 64px;
  padding: 4px 6px; font-size: 11px;
  display: block; text-decoration: none; color: inherit;
}
.cell-date { font-weight: 600; color: #555; }
.cell-date.sat { color: #2563eb; }
.cell-date.sun { color: #dc2626; }
.cell.past { background: #f5f5f5; opacity: 0.5; }
.cell.empty { background: #fafafa; }
.cell.today { background: #fff8e1; box-shadow: inset 0 0 0 2px #f59e0b; }
.cell.has-events:hover { background: #eff6ff; }
.cell-events {
  margin-top: 2px; line-height: 1.3;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cell-events .more { color: #888; font-size: 10px; }

@media (max-width: 600px) {
  .month-grid-section { display: none; }
}
```

---

## 6. Edge Cases

- **12월 → 1월 넘김**: `next_month(date(2026, 12, 1))` → `(2027, 1)` 로 자연 처리. 다음 해 1월 이벤트 0건이면 빈 그리드.
- **이벤트 0건인 달**: 그리드 그대로 렌더 (모든 셀이 비어있는 박스). 헤더 + 빈 캔버스로 표시 — 사용자에게 "이 달은 등록된 이벤트가 없음" 시그널.
- **셀 안 텍스트 잘림**: `text-overflow: ellipsis; white-space: nowrap; overflow: hidden;` 로 한 줄 컷.
- **anchor 미스매치 방지**: 그리드 셀에 `<a>` 태그가 붙는 건 `has-events` 일 때만. 즉 anchor가 가리키는 `id="date-..."` 는 반드시 리스트 섹션에 존재.
- **모바일**: `@media (max-width: 600px) { .month-grid-section { display: none; } }` — 그리드 통째 숨김. 리스트는 기존 동작 그대로.
- **`event_date` 형식 안전성**: `event_date` 가 `YYYY-MM-DD` 형식이라고 가정 (`build_calendar_events()` 에서 보장). 안전하게 `date.fromisoformat(e["event_date"])` 로 파싱하여 (year, month, day) 추출.

---

## 7. 테스트

- **단위 테스트** (`app/test_calendar_grid.py` 신규):
  - 임의 events fixture 입력 → `_render_month_grid()` 호출
  - cell 개수 = leading_blanks + days_in_month
  - 오늘에 `today` 클래스, 과거 셀에 `past` 클래스
  - 이벤트 있는 셀: `<a href="#date-YYYY-MM-DD">` 형태, `class="cell has-events"`
  - 첫 이벤트 제목 + "+N건" 정확히 렌더 (N=2 이상일 때)
  - 12월 입력 시 다음 달 = 다음 해 1월
  - 토/일 셀 컬러 클래스 부여
- **회귀 테스트**: 기존 `test_events.py` 의 `render_calendar_html()` 호출이 있다면 그대로 통과 (시그니처 추가만 함, 기본값 False라 다트공시 등 다른 호출자엔 영향 없음).
- **시각 검증**: `python app/run_event_preview.py` 실행 → `/tmp/news_calendar.html` 브라우저로 확인:
  1. 5월/6월 두 그리드 보임
  2. 5/1~5/5 회색, 5/6 노랑 하이라이트
  3. 토 = 파랑, 일 = 빨강
  4. 셀 클릭하면 하단 리스트 해당 날짜로 스크롤
  5. 모바일 너비(devtools)에서 그리드 숨김
  6. 다트공시 탭 (`/tmp/news_dart.html`) 은 그리드 없이 기존 리스트만 보임 (회귀 없음)

---

## 8. 비목표 (Out of Scope)

- 다트공시 탭에 그리드 추가 (다음 라운드)
- 좌우 화살표로 더 먼 미래 달 네비게이션 (필요시 후속)
- 셀 hover/click 시 인라인 펼치기 (JS 필요, anchor link 로 충분)
- 모바일 전용 주간 뷰 (CSS 토글 한 줄로 끝, 추가 UX 안 만듦)
- 이벤트 카드 색상 코딩 by type (현재 아이콘으로 충분)
- 그리드와 리스트 양방향 스크롤 동기화 (anchor link 단방향만 — 리스트 → 그리드 강조는 안 함)

---

## 9. 변경 영향

- `app/calendar_page.py`: 헬퍼 함수 1개 + `render_calendar_html()` 시그니처 1개 인자 추가 + 내부 분기. CSS 약 30줄 추가.
- `app/run_pages.py`: `news_calendar.html` 빌드 호출 시 `show_month_grid=True, today=date.today()` 전달. 다트공시 호출은 그대로.
- `app/test_calendar_grid.py`: 신규 파일 (~120줄).
- 기타 모듈: 변경 없음.
