# 모바일 웹 대응 설계 (정적 대시보드 3개 페이지)

**일자**: 2026-04-28
**대상**: GitHub Pages에 배포되는 3개 정적 HTML 페이지
- `public/news_preview.html` (정책·이벤트 카드)
- `public/news_calendar.html` (다가올 이벤트)
- `public/news_dart.html` (DART 공시)

## 배경 / 목적

세 페이지는 `app/events.py`와 `app/calendar_page.py`의 Python 렌더링 함수로 생성된다.
현재 데스크톱(900px max-width 컨테이너)에 최적화되어 있고, 모바일에서는:

1. `<meta name="viewport">`가 없어 브라우저가 980px 가상폭으로 렌더링 후 축소 → 텍스트 가독성 최저
2. body / 카드 padding이 모바일 기준으로 과도
3. 상단 네비, 종목 칩, 이벤트 헤더가 작은 화면에서 줄바꿈 처리되지 않음

목표: **iPhone 기준(320–428px)에서도 가독성 있는 레이아웃**으로 만든다. 데스크톱 레이아웃은 변경 없음.

## 비목표 (YAGNI)

- 다크모드
- 햄버거 메뉴 / 모바일 전용 SPA 전환
- PWA, 오프라인 지원
- 폰트 크기 사용자 설정
- 별도 모바일 빌드 파이프라인

## 변경 범위

### 1. `app/events.py` (정책 카드 페이지 렌더러)

`render_policy_event_html` 함수의 `<head>` / `<style>` 블록을 수정.
- `events.py:689` 근처: `<head>` 안에 viewport meta 추가
- `events.py:779` 의 `@media (max-width:640px)` 블록을 확장 (현재 body / article padding만 축소)

### 2. `app/calendar_page.py` (캘린더·DART 페이지 렌더러)

`render_calendar_html` 함수의 `<head>` / `<style>` 블록 수정.
- `calendar_page.py:972` 근처: `<head>` 안에 viewport meta 추가
- `calendar_page.py:1117` 의 `@media (max-width:640px)` 블록을 확장

### 3. `app/calendar_page.py` 의 `inject_nav` 함수 (NAV_CSS)

상단 네비게이션 바도 모바일에서 압축되어야 함.
- `calendar_page.py:899` 근처 `nav-inner`의 `max-width: 900px; margin: 0 auto;` 컨테이너 padding 축소
- 탭 폰트 / padding 축소
- gap 축소

## 구현 디테일

### Viewport meta (양쪽 페이지 공통)

```html
<meta name="viewport" content="width=device-width, initial-scale=1">
```

### `@media (max-width:640px)` — 두 페이지 공통 규칙

```css
@media (max-width:640px) {
  body { padding: 12px 12px; font-size: 15px; }
  .container { /* max-width 무의미하므로 그대로 */ }

  .page-header { margin-bottom: 20px; padding-bottom: 14px; }
  .page-header h1 { font-size: 22px; }
  .page-meta { font-size: 13px; }

  /* news_preview 카드 */
  article { padding: 14px 16px; border-radius: 12px; }
  article header { gap: 8px; flex-wrap: wrap; padding-bottom: 8px; margin-bottom: 10px; }
  article h2 { font-size: 16px; }
  .news-badge { margin-left: 0; font-size: 11px; }
  .news-list li { font-size: 13px; }

  /* calendar / dart 카드 */
  .event-card { padding: 14px 16px; border-radius: 12px; }
  .event-header { gap: 6px; flex-wrap: wrap; }
  .event-header h3 { font-size: 15px; flex-basis: 100%; }
  .source-tag, .date-hint, .dir-badge, .flag-badge { font-size: 10px; padding: 2px 7px; }
  .event-body { font-size: 13px; padding: 8px 10px; }

  .date-label { font-size: 13px; padding: 5px 12px; }

  /* 종목 칩 — 모바일에서 가격(s-close) 숨김으로 줄바꿈 최소화 */
  .stock-chip { padding: 4px 8px; font-size: 12px; gap: 6px; }
  .s-close { display: none; }
  .supply-tag { font-size: 9px; padding: 1px 4px; }
}
```

### NAV_CSS — 모바일 추가 규칙

```css
@media (max-width:640px) {
  .nav-inner { padding: 8px 12px; gap: 10px; }
  .brand { font-size: 12px; }
  .tabs { gap: 2px; }
  .tab { padding: 6px 10px; font-size: 12px; }
}

@media (max-width:380px) {
  .brand { display: none; }    /* iPhone SE 등 초소형: 브랜드 숨김 */
  .nav-inner { padding: 6px 8px; }
  .tab { padding: 6px 8px; font-size: 11px; }
}
```

## 테스트 / 검증

1. `python app/run_pages.py` 로컬 빌드 → `public/*.html` 생성
2. 생성된 HTML 안에 viewport meta + 새 미디어쿼리 포함 확인 (grep)
3. 브라우저 DevTools 모바일 뷰(360px / 414px / 768px)에서 시각 확인
4. 데스크톱(>640px) 레이아웃에 회귀 없는지 확인

## 위험 / 트레이드오프

- `s-close`(종가) 숨김: 모바일에서 종가가 안 보이지만 등락률(`s-pct`)은 유지. 칩 클릭 시 네이버 종목 페이지로 이동하므로 정보 손실 미미.
- `flex-wrap` 추가로 일부 데스크톱 레이아웃에서 미세한 변화 가능 → 640px 이상에서는 적용 안 함으로 회피.
- 정적 HTML이라 JS 토글 없이 CSS만으로 처리.
