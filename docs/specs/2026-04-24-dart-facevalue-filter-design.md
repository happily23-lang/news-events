---
title: DART 공시 액면가 기반 필터 확장 설계
date: 2026-04-24
status: 구현 완료
author: ellie.lee (Claude Code 브레인스토밍)
target_module: .idea/app/dart_disclosure.py
---

# DART 공시 액면가 기반 필터 확장 설계

## 1. 배경 및 목적

현재 `.idea/app/dart_disclosure.py` 는 DART `list.json` 응답의 **제목 문자열**만으로 공시를 필터링한다. 따라서 액면가 숫자 비교 같은 정량 조건은 걸 수 없다.

사용자 요구사항은 **유상증자결정** 공시 중 신주 1주당 액면가가 기존 보통주 액면가보다 **낮은** 케이스에 `preferred_share_issuance` 플래그를 부착하는 것이다. 이는 실무상 거의 항상 종류주(우선주·전환우선주 등) 발행 시그널이므로, 라벨이 사실 그대로 의미를 전달한다.

이를 위해 DART **주요사항보고서 상세 API**(`piicDecsn.json`)를 호출하여 신주 액면가를 확보하고, 회사의 기존 보통주 액면가는 `stockTotqyItr.json` 에서 분기별로 동기화한 로컬 캐시로 비교한다.

### 1.1 기각된 안: 감자결정 액면감소 케이스 신규 편입

브레인스토밍 도중 "감자결정 중 변경 후 액면가 < 변경 전 액면가" 케이스를 신규 편입하는 안을 검토했으나 **기각**했다. 액면감자는 실무적으로 거의 항상 결손금 보전 목적이며, 발표 직후 단기 주가 평균 -5~-15% 하락이 관찰되는 강한 악재 시그널이기 때문이다. 본 알림 채널은 **호재 시그널 정제** 가 목적이므로 적합하지 않다. 감자결정은 현행대로 `EXCLUDE_SUBSTRINGS` 에 남는다.

## 2. 범위

### 2.1 영향 받는 공시 유형

| 공시 유형 | list.json 통과 | 상세 API 호출 | 포함 조건 | `direction` | 신규 `flags` |
|---|---|---|---|---|---|
| 주식분할결정 | ✅ | ❌ 불필요 (항상 액면↓) | 무조건 포함 (현행) | `positive` | — |
| 주식병합결정 | ❌ 제외 (현행 유지) | — | — | — | — |
| **유상증자결정** | ✅ | ✅ **호출** | **무조건 포함** | `negative` (현행) | 신주액면 < 기존액면이면 `preferred_share_issuance` |
| 감자결정 | ❌ 제외 (현행 유지) | — | — | — | — |
| 기타 (자기주식취득·현물배당·분할결정·주식교환·타법인주식취득·전환사채·신주인수권부사채) | 현행 유지 | ❌ | 현행 유지 | 현행 유지 | — |

### 2.2 비범위 (변경 없음)

- 무상증자결정, 자기주식취득(신탁) 등 기존 `positive` / `neutral` 유형의 로직
- 주식병합·합병·자기주식처분·감자결정 등 `EXCLUDE_SUBSTRINGS` 로 막혀 있는 유형
- list.json 호출 파라미터 자체 (`pblntf_ty=B`, 14일 창, 최대 5페이지)
- `events.py` / `calendar_page.py` / `run_event_preview.py` 등 consumer 측 로직

## 3. 아키텍처

### 3.1 모듈 전략

단일 파일 확장. `dart_disclosure.py` 내부에 기능을 추가하고 별도 모듈로 분리하지 않는다. 추가되는 코드가 약 200라인 수준이고 도메인 결합도가 높다.

### 3.2 구성요소

```
.idea/app/
├── dart_disclosure.py              # 기존 파일 확장
│   ├── (기존) TARGET_TITLE_TAGS           ← 변경 없음
│   ├── (기존) EXCLUDE_SUBSTRINGS          ← 변경 없음 (감자 그대로 제외)
│   ├── (기존) _title_matches              ← 변경 없음
│   ├── (기존) fetch_dart_target_events    ← 본체 호출 흐름 변경
│   ├── (신규) _load_detail_cache / _save_detail_cache  (tempfile + os.replace)
│   ├── (신규) _purge_stale_resolved
│   ├── (신규) _http_get_json              (HTTP 래퍼)
│   ├── (신규) _with_retry                 (in-process 1회 재시도)
│   ├── (신규) _parse_face_value           (액면가 파싱)
│   ├── (신규) _get_existing_face_value    (보통주 액면가 90일 캐시)
│   ├── (신규) _fetch_piic_face_value      (유상증자 신주 액면가 추출)
│   ├── (신규) _enrich_rights_issue        (캐시 또는 상세 API로 enrich)
│   └── (신규) _retry_pending_entries      (cross-run 재시도 큐)
├── test_dart_disclosure.py         # 신규 pytest 단위 테스트
└── dart_detail_cache.json          # 런타임 생성 (.idea 가 부모 .gitignore 에서 자동 제외됨)
```

## 4. 데이터 흐름

```
[실행 시작]
    ↓
Step 1  캐시 로드 (dart_detail_cache.json)
    ↓
Step 2  stale resolved 엔트리 purge (fetched_at > 30일)
    ↓
Step 3  재시도 큐 처리 (status="pending_retry" 엔트리를 list.json 호출 전에 재호출)
    ↓
Step 4  DART list.json 페이지 순회 (현행 로직)
    ↓
Step 5  제목 매칭 → 후보 분류
          ├── 주식분할결정      → 바로 hit (상세 호출 X)
          ├── 유상증자결정      → 캐시 lookup → miss 면 상세 API 호출 + 비교
          └── 기타 target       → 현행 로직 그대로
    ↓
Step 6  유상증자 enrich 결과 처리
          ├── resolved + decreased → flags=["preferred_share_issuance"], face_value_meta 설정
          ├── resolved + not decreased → flags=[], face_value_meta 설정 (참고용)
          ├── pending_retry / resolved_unknown → flags=[], face_value_meta=None,
                                                  body_snippet 에 "조회 실패" 접미
    ↓
Step 7  캐시 flush (이번 run 갱신 엔트리 저장)
    ↓
[이벤트 dict 리스트 반환]
```

## 5. 상세 API 호출 스펙

### 5.1 유상증자결정 — `piicDecsn.json`

- **URL**: `https://opendart.fss.or.kr/api/piicDecsn.json`
- **파라미터**: `crtfc_key`, `corp_code`, `bgn_de`, `end_de`
  - `rcept_no` 직접 조회 지원 안 함 → `corp_code` + 1일 창(`bgn_de=end_de=rcept_dt`) 조회 후 응답 `list[*]` 에서 `rcept_no` 매칭으로 필터
- **핵심 필드** (응답 JSON `list[*]`):
  - `fv_ps` — **신주 1주당 액면가액** (콤마 포함 문자열일 수 있음, `"-"` 는 무액면주식)

### 5.2 기존 액면가 조회 — `stockTotqyItr.json` (방식 B)

유상증자 상세 API는 신주 액면가만 제공하고 회사의 기존 보통주 액면가는 별도로 조회해야 한다.

**방식 B (채택)**: `corp_code → 현재 액면가` 매핑을 로컬 캐시에 보관하고 90일 TTL.

- **URL**: `https://opendart.fss.or.kr/api/stockTotqyItr.json` (주식의 총수 현황)
- **파라미터**: `crtfc_key`, `corp_code`, `bsns_year`, `reprt_code`
  - `bsns_year=올해-1, reprt_code="11011"` (직전 사업연도 사업보고서) 시도
  - 빈 응답 시 `bsns_year=올해-2` 폴백
- **핵심 필드**: `list[*]` 의 `se` 가 "보통주" 인 행의 `stk_fv` (1주당 액면가)
  - 보통주 행 없으면 첫 numeric 행 폴백
  - `"-"` 또는 `"0"` → None (무액면)
- **캐시**: `face_value_by_corp[corp_code] = {face_value, synced_at}` / TTL 90일

방식 A (매 공시마다 `stockTotqyItr` 호출)는 API 콜 2배라 채택하지 않음. 액면가는 수년 단위로 바뀌는 수치라 분기 동기화로 충분.

## 6. 로컬 캐시

### 6.1 파일 위치

`.idea/app/dart_detail_cache.json` (`.env` 와 같은 디렉토리). 부모 `.gitignore` 가 `.idea` 를 통째로 제외하므로 자동 git-untracked.

### 6.2 스키마

```json
{
  "version": 1,
  "entries": {
    "20260424000123": {
      "rcept_no": "20260424000123",
      "corp_code": "00126380",
      "disclosure_type": "유상증자결정",
      "status": "resolved",
      "fetched_at": "2026-04-24T10:30:00+09:00",
      "retries": 0,
      "data": {
        "new_face_value": 100,
        "existing_face_value": 500,
        "decreased": true
      }
    },
    "20260423000456": {
      "rcept_no": "20260423000456",
      "corp_code": "00293886",
      "disclosure_type": "유상증자결정",
      "status": "pending_retry",
      "fetched_at": null,
      "retries": 1,
      "last_error": "fetch_failed",
      "data": null
    }
  },
  "face_value_by_corp": {
    "00126380": {"face_value": 500, "synced_at": "2026-04-20"}
  }
}
```

### 6.3 상태 머신

| `status` | 의미 | 다음 run 동작 |
|---|---|---|
| `resolved` | 상세 API 성공, `data` 신뢰 가능 | 재호출 없이 `data` 사용 |
| `pending_retry` | 1차 실패, `retries=1` | 재시도 큐에서 우선 재호출 |
| `resolved_unknown` | 2차 실패, 영구 미해결 | 재호출하지 않음 (실패 정책 적용) |

### 6.4 정리 주기

- `resolved` 엔트리 중 `fetched_at` 이 **30일 지난 것은 purge** (매 run 시작 시 1회)
- `resolved_unknown` 은 영구 보존 (재호출 방지 목적)

## 7. 재시도 정책

1. **프로세스 내 1회 재시도**: 첫 호출 결과가 None 이면 3초 sleep 후 1회 재호출 (`_with_retry`)
2. **cross-run 재시도**: 위 2회가 모두 실패하면 `status=pending_retry, retries=1` 로 캐시에 남김
3. **다음 run 초입 재호출**: `_retry_pending_entries()` 가 list.json 조회보다 **먼저** 실행되어 지난 run의 `pending_retry` 엔트리를 1회 재호출 (in-process retry 없음)
4. **영구 미해결 처리**: 다음 run 도 실패하면 `status=resolved_unknown, retries=2` 로 고정. 이후 재호출하지 않음.

총 시도 횟수: **첫 시도 + 프로세스 내 재시도 1회 + 다음 run 재시도 1회 = 최대 3회 호출**

## 8. 실패 시 사용자 노출

| 상황 | 이벤트 포함 | `flags` | `face_value_meta` | `body_snippet` |
|---|---|---|---|---|
| 상세 성공 + 신주액면 < 기존액면 | ✅ | `["preferred_share_issuance"]` | dict (decreased=True) | 현행 |
| 상세 성공 + 신주액면 >= 기존액면 | ✅ | `[]` | dict (decreased=False) | 현행 |
| 상세 실패 (pending_retry / resolved_unknown / 무액면) | ✅ | `[]` | None | 현행 + `"(※ 신주 액면가 조회 실패 — 다음 수집 시 재시도)"` 접미 |

유상증자 공시는 *있다는 사실 자체*가 이미 시그널(희석)이고 `preferred_share_issuance` 는 부가 태그일 뿐이라 상세 조회 실패해도 본 이벤트는 emit 한다.

## 9. 데이터 모델 변경

### 9.1 반환 event dict 신규 키

기존 필드는 그대로 두고 2개 키만 추가:

```python
{
    # ... 기존 필드 ...
    "disclosure_type": "유상증자결정",
    "direction": "negative",

    # ── 신규 ──
    "face_value_meta": {
        "pre": 500,                # 기존 보통주 액면가
        "post": 100,               # 신주 1주당 액면가
        "decreased": True,
        "source": "detail_api",
    },
    "flags": ["preferred_share_issuance"]   # 태그 리스트, 없으면 빈 배열.
}
```

### 9.2 기존 consumer 영향도

- `events.py`, `calendar_page.py`, `run_event_preview.py` 는 신규 키를 읽지 않으므로 영향 없음
- 향후 consumer 는 `event.get("flags", [])`, `event.get("face_value_meta") or {}` 패턴으로 방어적 접근 권장

### 9.3 `TARGET_TITLE_TAGS` / `EXCLUDE_SUBSTRINGS` 조정

- 변경 없음. 유상증자결정은 이미 `TARGET_TITLE_TAGS` 에 `"negative"` 로 포함되어 있고, 감자결정은 `EXCLUDE_SUBSTRINGS` 에 그대로 남는다.

## 10. 배포 · 설정 영향

- `.idea/app/dart_detail_cache.json` 은 부모 `.gitignore` 의 `.idea` 룰로 자동 제외 → 추가 작업 불필요
- 신규 환경 변수 없음 (기존 `DART_API_KEY` 그대로 사용)
- 외부 종속성 없음 (기존 `requests` 만 사용)
- 테스트 의존성: `pytest` (venv 에 추가 설치 필요)

## 11. 호출량 예측

| 시나리오 | 추가 상세 API 콜 / run |
|---|---|
| 초기 실행 (캐시 빈 상태, 14일 창) | 약 70~150콜 (유상증자 건수만큼) |
| 정상 운영 (캐시 적중) | 하루 평균 5~10콜 |
| 90일 액면가 재동기화 이벤트 | 해당 corp_code 수만큼 추가 (분기 1회) |

DART 기본 쿼터 20,000/일 대비 여유 충분.

## 12. 테스트 계획

### 12.1 단위 테스트 (`test_dart_disclosure.py`, pytest 기반)

| # | 테스트 이름 | 확인 내용 |
|---|---|---|
| T1 | `test_title_matches_excludes_capital_reduction` | 감자결정 타이틀이 여전히 제외됨 (회귀 방지) |
| T2 | `test_title_matches_includes_rights_issue` | 유상증자결정 정상 매칭 |
| T3 | `test_title_matches_excludes_stock_consolidation` | 주식병합결정 제외 |
| T4 | `test_title_matches_excludes_combined_paid_unpaid` | 유무상증자 제외 |
| T5 | `test_parse_face_value_*` | 정상값·콤마·`"-"`·빈값·`"0"` 파싱 (5건) |
| T6 | `test_cache_round_trip` | JSON 직렬화/역직렬화 보존 |
| T7 | `test_cache_load_missing_returns_default` | 파일 없을 때 빈 구조 반환 |
| T8 | `test_cache_load_corrupt_returns_default` | 손상 JSON 시 빈 구조 반환 |
| T9 | `test_flag_attached_when_new_lt_existing` | 신주액면 < 기존액면 → `preferred_share_issuance` 부착 |
| T10 | `test_flag_not_attached_when_new_eq_existing` | 동일 액면 → 플래그 없음, 이벤트는 emit |
| T11 | `test_rights_issue_emitted_on_detail_failure` | 상세 실패 시 이벤트 emit, 플래그 없음, body 접미 |
| T12 | `test_pending_retry_promoted_to_resolved` | pending_retry 엔트리 다음 run 성공 → resolved |
| T13 | `test_pending_retry_to_resolved_unknown` | 2회째 실패 → resolved_unknown, retries=2 |
| T14 | `test_face_value_cache_refreshes_after_ttl` | synced_at 90일 초과 시 재동기화 |
| T15 | `test_face_value_cache_within_ttl_uses_cached` | 90일 이내면 stockTotqyItr 호출 안 함 |
| T16 | `test_no_crDecsn_calls_and_capital_reduction_excluded` | crDecsn URL 호출 0회 단언 + 감자 제외 회귀 방지 |
| T17 | `test_no_par_silent_skip` | `fv_ps="-"` (무액면) 시 silent skip, 이벤트는 emit |

### 12.2 수동 검증 절차

1. `DART_API_KEY` 환경변수 설정
2. ```bash
   ./.venv/bin/python3 -c "
   from dart_disclosure import fetch_dart_target_events, load_dart_key
   events = fetch_dart_target_events(load_dart_key())
   for e in events:
       if e.get('disclosure_type') == '유상증자결정':
           print(e['title'], e.get('flags'), e.get('face_value_meta'))
   "
   ```
3. `dart_detail_cache.json` 생성 확인 + `python -m json.tool` 로 구조 검증
4. 두 번째 실행에서 캐시 적중으로 detail API 호출 수 감소 확인

## 13. 오픈 이슈 / 추후 결정

- 90일 액면가 재동기화 주기는 액면 변경 누락 케이스가 발생하면 30일로 단축 고려
- 캐시 파일 누적 크기 모니터링 (resolved 30일 purge + resolved_unknown 영구 보존 정책)

## 14. 수락 기준 (Acceptance Criteria)

1. 유상증자결정 공시는 기존처럼 전부 노출된다.
2. 신주 1주당 액면가가 기존 보통주 액면가보다 낮은 유상증자 공시는 `flags` 에 `preferred_share_issuance` 가 포함된다.
3. 상세 API 호출 실패 시 해당 공시는 재시도 큐에 들어가고 다음 실행에서 재호출된다 (in-process 3초 1회 + cross-run 1회 = 최대 3회 시도).
4. 동일 `rcept_no` 공시가 여러 run 동안 중복으로 상세 API 호출되지 않는다 (캐시 적중).
5. 주식분할결정·주식병합결정·감자결정·기타 기존 target 유형의 동작은 변경되지 않는다.
6. 기존 consumer (`events.py`, `calendar_page.py`, `run_event_preview.py`) 가 변경 없이 동작한다.
7. `crDecsn.json` URL 은 어떤 코드 경로에서도 호출되지 않는다 (감자 제외 회귀 방지).
