---
title: 섹터 자금흐름 페이지 (Sector Money Flow) 설계
date: 2026-05-04
status: 설계 완료
author: ellie.lee (Claude Code 브레인스토밍)
target_modules:
  - app/sector_flow_page.py (신규)
  - app/run_sector.py (신규)
  - app/run_pages.py (수정)
  - app/calendar_page.py::inject_nav (수정)
  - app/krx_sector_cache.json (런타임 생성)
roadmap_step: Step 1 of 4 (전체 로드맵: 섹터 자금흐름 → 테마 모멘텀 → 거시 국면 → 워치리스트)
---

# 섹터 자금흐름 페이지 설계

## 1. 배경 및 목적

현재 대시보드(`news_preview` / `news_calendar` / `news_dart`)는 **종목·이벤트 단위**의 카탈리스트 정보만 제공한다. 사용자는 **중기 섹터 로테이션** 관점의 투자 판단을 강화하고 싶어하며, 그 첫 질문이 "지금 외인·기관이 어느 섹터를 사고 있는가"이다.

이 페이지는 이미 수집 중인 `naver_supply_cache.json`(종목별 외인·기관 순매수)을 **섹터 단위로 합산**하여, 자금이 몰리고 빠지는 섹터를 한눈에 파악할 수 있게 한다.

**전체 로드맵**: 본 작업은 4단계 중 1단계.
1. **(본 spec) 섹터 자금흐름** — 외부 API 0개 추가, 빠른 효과
2. 테마 모멘텀 — Naver 테마지수 시계열 누적
3. 거시 국면 — ECOS + FRED + 환율/원자재
4. 워치리스트 사이드바 — 1~3 결과를 사용자 종목에 매핑

## 2. 범위

### 2.1 포함

- 새 페이지 `public/news_sector_flow.html`
- 새 빌더 모듈 `app/sector_flow_page.py`
- KRX 섹터 매핑 수집기 `app/run_sector.py` (주 1회 cron)
- 섹터 매핑 캐시 `app/krx_sector_cache.json`
- `run_pages.py` 에 빌드 호출 추가
- 모든 페이지 nav (`inject_nav`)에 새 탭 **"섹터 자금흐름"** 추가
- 단위 테스트 `app/test_sector_flow.py`

### 2.2 비범위 (V1에서 제외)

- ❌ 20일 누적 윈도우 (현재 cache 가 window=5 만 보유 → V2 에서 `run_naver.py` 확장 후 추가)
- ❌ 섹터 카드 클릭 시 드릴다운 페이지
- ❌ KOSPI / KOSDAQ 분리 토글 (V1 은 통합)
- ❌ 외인 보유비중 변화 추이 (별도 데이터 소스 필요)
- ❌ 거래대금 가중 (V1 은 단순 net buying)

## 3. 데이터 소스

### 3.1 입력 1: `naver_supply_cache.json` (기존)

- 키: `{code}|{date}|{window}` 형식, 현재 `window=5` 만 캐시
- 값:
  ```json
  {
    "foreign_net_shares": -4473938,
    "institution_net_shares": 1303181,
    "foreign_net_value": -949193222500,
    "institution_net_value": 289531714000,
    "rows": [
      {"date": "2026-04-24", "close": 219500,
       "institution_net": -66031, "foreign_net": -...}
    ]
  }
  ```
- 커버리지: 약 177개 종목 (KOSPI200 + 주요 KOSDAQ)
- 사용:
  - 5일 누적 = 캐시의 `foreign_net_value` / `institution_net_value` 직접 사용
  - 1일 = `rows[-1]` 의 일별 매매 필드. **구현 단계에서 `app/naver_supply.py` 의 rows 스키마를 먼저 확인**하여 value/share 단위 결정 (만약 share 만 있으면 close × shares 로 환산)

### 3.2 입력 2: KRX 섹터 분류 (신규)

- 데이터 소스: **pykrx** (`pykrx.stock.get_market_sector_classifications` 또는 동등 API)
  - KRX 표준업종 분류 (KOSPI / KOSDAQ 모두 포함)
  - 28개 내외 섹터
- 캐시: `app/krx_sector_cache.json`
  ```json
  {
    "_meta": {"refreshed_at": "2026-05-04T00:00:00Z", "source": "pykrx"},
    "005930": {"sector": "전기·전자", "market": "KOSPI"},
    "035720": {"sector": "서비스업",   "market": "KOSPI"},
    "...": "..."
  }
  ```
- 갱신 주기: **주 1회 (월요일 21:00 UTC = 06:00 KST 화요일 새벽)**
  - 기존 종목 섹터 변경은 드물지만 신규 IPO 가 매주 추가되므로 주 단위가 적절
  - 호출 비용 0에 가까움 (pykrx 한 번 호출)
- 매핑 안 되는 종목 (스팩·리츠·우선주 등): `sector="기타"` 처리

### 3.3 입력 3: KRX 종목 메타 (기존)

- `events.load_krx_listings()` 결과 (Code, Name, Market 등)
- 종목 코드 → 종목명 매핑에 사용

## 4. 아키텍처

### 4.1 모듈 구조

```
app/
├── run_sector.py              (신규) pykrx 호출, krx_sector_cache.json 갱신
├── sector_flow_page.py        (신규) 집계 + HTML 렌더
│   ├── load_sector_mapping()
│   ├── aggregate_sector_flows(supply_cache, sector_map, window)
│   ├── classify_intensity(net_value_pct) → 5단계 라벨
│   └── render_sector_flow_html(aggregated)
├── test_sector_flow.py        (신규) 집계 단위 테스트
├── run_pages.py               (수정) build_sector_flow_html 호출 추가
├── calendar_page.py           (수정) inject_nav 에 새 탭 추가
└── krx_sector_cache.json      (런타임 생성)
```

### 4.2 데이터 흐름

```
[월요일 21 UTC cron]
    ↓
run_sector.py → pykrx 호출 → krx_sector_cache.json 갱신 → git commit
                                     │
[기존 naver_supply cron 별도 실행]    │
                                     │
[build_pages cron 또는 push]         ▼
    ↓
run_pages.py
    ├── load supply cache + sector cache + KRX listings
    ├── sector_flow_page.aggregate_sector_flows(window=1)
    ├── sector_flow_page.aggregate_sector_flows(window=5)
    ├── sector_flow_page.render_sector_flow_html()  → public/news_sector_flow.html
    └── inject_nav 적용
```

### 4.3 집계 로직

```python
def aggregate_sector_flows(
    supply_cache: dict,
    sector_map: dict[str, dict],
    window: int,  # 1 or 5
    as_of: str,   # YYYY-MM-DD
) -> dict[str, SectorFlow]:
    """
    각 섹터별로:
      - foreign_net_value_total: 모든 종목 합산
      - institution_net_value_total: 모든 종목 합산
      - combined_net_value: 위 둘 합
      - sector_market_cap: 섹터 시총 합 (또는 종목 수, V1은 종목 수)
      - intensity_pct: combined_net_value / (1억 × 종목 수) × 100  ← V1 단순화
      - sparkline_5d: 5일 일별 합산 (rows[] 사용)
      - top_buy: combined_net_value 상위 3종목
      - top_sell: combined_net_value 하위 3종목
    """
```

### 4.4 강도 라벨 (5단계)

`intensity_pct = combined_net_value / (종목 수 × 100억) × 100` 기준 (V1 임시 단순 산식):

| 라벨 | 조건 | 색상 |
|---|---|---|
| 🔥 강매수 | intensity ≥ +5% | red-strong |
| 📈 매수 | +1% ≤ intensity < +5% | red-soft |
| ⚪ 중립 | -1% < intensity < +1% | gray |
| 📉 매도 | -5% < intensity ≤ -1% | blue-soft |
| ❄ 강매도 | intensity ≤ -5% | blue-strong |

> **주의**: 5단계 경계값은 V1 직관 기반. 실제 데이터 분포 확인 후 V1.1 에서 조정 가능.

## 5. UI 명세

### 5.1 페이지 헤더

- 제목: "섹터 자금흐름 — YYYY-MM-DD"
- 메타: "데이터 기준일: YYYY-MM-DD · 윈도우: 1일 | **5일** · 외인·기관 합산"
- 토글:
  - **윈도우**: `1일 | 5일` (5일 default)
  - **주체**: `외인+기관 | 외인 | 기관` (합산 default)
  - 토글은 querystring 또는 클라이언트 JS로 (정적 사이트라 페이지를 다중 빌드하거나 JS 토글)
  - **V1 결정**: 모든 조합을 미리 빌드한 정적 HTML 다중 페이지가 아니라, **단일 페이지에 6개 조합 데이터를 JSON으로 임베드 + JS로 토글**

### 5.2 카드 레이아웃 (한 섹터)

```
┌───────────────────────────────────────────────┐
│  반도체                          🔥 강매수    │
│  외인 +1,240억  ·  기관 −180억               │
│  ━━━━━━━━━━━━━━━━━━━━━━ +1,060억              │  (가로 막대, +/- 색상)
│  ▁▂▄▆█▇▅                                      │  (5일 sparkline, SVG)
│  ─────────────────────────                    │
│  📈 매수 TOP3                                 │
│    삼성전자        +850억                     │
│    SK하이닉스      +320억                     │
│    한미반도체      +180억                     │
│  📉 매도 TOP3                                 │
│    원익IPS         −95억                      │
│    DB하이텍        −62억                      │
│    리노공업        −38억                      │
└───────────────────────────────────────────────┘
```

### 5.3 정렬·표시

- Default: 5일 외인+기관 합산 순매수 **내림차순** (양수 → 음수)
- 매수 TOP3 / 매도 TOP3 같이 표시 (밀도 ↑)
- 종목 수가 3개 미만인 섹터는 가능한 만큼만 표시
- 섹터 카드 수: 매핑된 모든 섹터 (예상 ~28개) — "기타" 섹터는 마지막에 표시

### 5.4 모바일

- 기존 페이지의 mobile responsive 패턴(`docs/superpowers/specs/2026-04-28-mobile-responsive-design.md`) 따라 1열 reflow
- 카드 폭 100%, sparkline 폭 자동 축소
- 토글은 가로 스크롤 방지 위해 wrap

### 5.5 스타일

- 기존 페이지의 CSS 변수 시스템(`--up`, `--down`, `--up-soft`, `--down-soft`) 재사용
- 카드 디자인은 `news_dart` / `news_preview` 와 일관성 유지

## 6. CI / 배포

### 6.1 새 워크플로우: `.github/workflows/krx_sector.yml`

- 트리거: 매주 월요일 21:00 UTC (KST 화요일 06:00) + workflow_dispatch
- 작업: `python app/run_sector.py` → `krx_sector_cache.json` 변경 시 commit
- `[skip ci]` 메시지 사용 (현재 다른 cache cron 들과 동일한 패턴)
- 동시 push race condition 방지: `git pull --rebase origin main` 추가 (현재 다른 cron 도 같이 패치 권장)

### 6.2 `build_pages.yml`

- 변경 없음 (기존 cron + push 트리거가 자동으로 새 페이지도 빌드)

### 6.3 의존성 추가

- `requirements.txt` 에 `pykrx` 추가 (run_sector.py 용)
- `requirements-pages.txt` 에는 추가 불필요 (페이지 빌드 시점에는 캐시만 읽음)

## 7. 테스트

### 7.1 `test_sector_flow.py`

- `aggregate_sector_flows()` 단위 테스트
  - 정상 케이스: 외인 + 기관 합산 정확성
  - 빈 섹터 처리
  - 매핑 안 되는 종목 → "기타" 그룹화
  - 종목 수 3개 미만 섹터 처리
- `classify_intensity()` 경계값 테스트 (5단계)
- 성능: 200종목 × 28섹터 합산이 100ms 이내

### 7.2 수동 검증

- 빌드 후 `public/news_sector_flow.html` 파일 직접 열어서 확인
- 데스크탑/모바일 둘 다 확인
- 5일 / 1일 토글 동작 확인

## 8. 마이그레이션 / 호환성

- 기존 페이지·기존 cron 전혀 영향 없음 (순수 추가)
- nav 변경: 모든 페이지 헤더에 탭 1개 늘어남 (UI breaking 아님)

## 9. 향후 단계 (out of scope, V2+)

- **V1.1**: 강도 라벨 경계값을 실제 데이터 분포 기반으로 재조정
- **V1.2**: 섹터 시가총액 가중치 적용 (현재는 종목 수 기반 단순 평균)
- **V2**: 20일 윈도우 추가 (`run_naver.py` 에 window=20 캐시 확장 선행)
- **V2**: 섹터 카드 클릭 시 드릴다운 (그 섹터 전체 종목 리스트 + 5일 sparkline)
- **V2**: KOSPI / KOSDAQ 분리 토글
- **Step 2** (테마 모멘텀): Naver 테마지수 시계열을 별도 캐시로 누적 시작

## 10. 의사결정 기록

- **섹터 분류 소스**: pykrx 표준업종 채택. WICS·FnGuide 같은 더 세분화된 분류는 유료/계약 필요. KRX 표준은 28개 내외로 한 화면에 카드로 표시하기 적절한 양.
- **20일 윈도우 미포함**: 현재 cache 에 없음. 별도 수집 작업이 필요해 V1 범위 분리.
- **JS 토글 방식**: 정적 사이트라 다중 빌드 vs 클라이언트 토글 중 후자 선택. 데이터 양이 작아 (~28섹터 × 6조합 = 168 카드 데이터) 단일 HTML 에 JSON 임베드해도 무리 없음.
- **카드 안 매도 TOP3 포함**: 정보 밀도 우선. 단순 매수만 보면 "매도 압력" 시그널을 놓침.
