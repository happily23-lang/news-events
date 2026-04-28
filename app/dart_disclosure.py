"""
DART OpenAPI 로 특정 공시 유형(무상증자결정·주식분할결정·유상증자결정 등)을
수집해 캘린더 이벤트로 변환한다.

유의:
 - 무상증자결정: 자사 잉여금으로 신주 무료 배정 (보통 호재)
 - 주식분할결정: 액면가 감소 + 주식수 증가 (변경 후 < 변경 전 자동 충족)
 - 유무상증자결정 공시는 순수 무상이 아니므로 제외
 - 유상증자결정: 모두 포함하되, 신주 1주당 액면가 < 기존 보통주 액면가인
   케이스는 `preferred_share_issuance` 플래그를 부착 (종류주 발행 시그널)
"""

import json
import os
import tempfile
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

import requests


DART_LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DART_PIIC_URL = "https://opendart.fss.or.kr/api/piicDecsn.json"
DART_STOCK_TOTQY_URL = "https://opendart.fss.or.kr/api/stockTotqyItr.json"
DART_BONUS_ISSUE_URL = "https://opendart.fss.or.kr/api/fricDecsn.json"
DART_TREASURY_AQ_URL = "https://opendart.fss.or.kr/api/tsstkAqDecsn.json"

ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dart_detail_cache.json")

FACE_VALUE_TTL_DAYS = 90
RESOLVED_TTL_DAYS = 30
RETRY_SLEEP_SEC = 3

# 캘린더에 표시할 주요사항보고서 유형 (B 타입)
# 각 유형에 '방향성 힌트'를 붙여둔다 (향후 B단계 호재/악재 태그에 활용)
# 사용자 선호로 '자기주식처분·합병·감자' 는 제외
TARGET_TITLE_TAGS: dict[str, str] = {
    # 일반적으로 호재로 해석되는 유형
    "무상증자결정": "positive",
    "주식분할결정": "positive",
    "자기주식취득결정": "positive",
    "자기주식취득신탁계약체결결정": "positive",
    "현물배당결정": "positive",
    # 중립 (방향 혼재)
    "분할결정": "neutral",        # 인적/물적 구분은 제목에 명시됨
    "주식교환·이전결정": "neutral",
    "타법인주식및출자증권취득결정": "neutral",
    # 유상증자·CB·BW 는 희석성이나 자금조달·M&A 시그널로 참고 가치 있어 유지
    "유상증자결정": "negative",
    "전환사채권발행결정": "negative",
    "신주인수권부사채권발행결정": "negative",
}
TARGET_TITLES = tuple(TARGET_TITLE_TAGS.keys())

# 제외: 혼합형 · 주식 수 감소 유형 · 사용자 요청 제외 유형
EXCLUDE_SUBSTRINGS = (
    "유무상증자", "주식병합",
    "자기주식처분", "합병결정", "감자결정",
)


def load_dart_key() -> Optional[str]:
    """환경변수 우선, 없으면 .env 파일에서 읽기."""
    key = os.environ.get("DART_API_KEY")
    if key:
        return key.strip()
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DART_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _parse_rcept_dt(s: str) -> Optional[date]:
    if not s or len(s) != 8:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _parse_dart_date(raw) -> Optional[date]:
    """DART 일정 필드 파싱. 'YYYY-MM-DD', 'YYYYMMDD', 'YYYY.MM.DD' 등 다양한 포맷 대응."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s == "-":
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    for sep in (".", "/", " "):
        if sep in s:
            parts = s.replace(sep, "-").split("-")
            if len(parts) == 3:
                try:
                    return date(int(parts[0]), int(parts[1]), int(parts[2]))
                except ValueError:
                    return None
    return None


def _title_matches(report_nm: str) -> Optional[tuple[str, str]]:
    """제목이 타겟이면 (유형, 방향성 태그) 반환."""
    if any(ex in report_nm for ex in EXCLUDE_SUBSTRINGS):
        return None
    for t in TARGET_TITLES:
        if t in report_nm:
            return t, TARGET_TITLE_TAGS[t]
    return None


# ============================================================
# 캐시 I/O
# ============================================================

def _empty_cache() -> dict:
    return {"version": 1, "entries": {}, "face_value_by_corp": {}}


def _load_detail_cache() -> dict:
    """JSON 캐시 로드. 파일 없거나 파싱 실패 시 빈 구조 반환."""
    if not os.path.exists(CACHE_PATH):
        return _empty_cache()
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return _empty_cache()
    if not isinstance(data, dict):
        return _empty_cache()
    data.setdefault("version", 1)
    data.setdefault("entries", {})
    data.setdefault("face_value_by_corp", {})
    return data


def _save_detail_cache(cache: dict) -> None:
    """tempfile + os.replace 로 원자적 저장."""
    cache_dir = os.path.dirname(CACHE_PATH) or "."
    os.makedirs(cache_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".dart_cache_", dir=cache_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, CACHE_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _purge_stale_resolved(cache: dict, now: datetime) -> None:
    """30일 지난 resolved 엔트리 제거. resolved_unknown 영구 보존."""
    cutoff = now - timedelta(days=RESOLVED_TTL_DAYS)
    entries = cache.get("entries", {})
    to_remove = []
    for rcept_no, entry in entries.items():
        if entry.get("status") != "resolved":
            continue
        fetched_at = entry.get("fetched_at")
        if not fetched_at:
            continue
        try:
            ts = datetime.fromisoformat(fetched_at)
        except ValueError:
            continue
        # naive vs aware 일관성 보정
        if ts.tzinfo is None and cutoff.tzinfo is not None:
            ts = ts.replace(tzinfo=cutoff.tzinfo)
        elif ts.tzinfo is not None and cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=ts.tzinfo)
        if ts < cutoff:
            to_remove.append(rcept_no)
    for rcept_no in to_remove:
        del entries[rcept_no]


# ============================================================
# 네트워크 프리미티브
# ============================================================

def _http_get_json(url: str, params: dict, timeout: int = 15) -> Optional[dict]:
    """status=='000' 이면 JSON dict, 아니면 None. 에러 처리 중앙화."""
    try:
        r = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if data.get("status") != "000":
        return None
    return data


def _with_retry(callable_fn: Callable):
    """첫 호출 None 시 3초 sleep 후 1회 재시도. 캐시 적중·cross-run 재시도엔 사용 X."""
    result = callable_fn()
    if result is not None:
        return result
    time.sleep(RETRY_SLEEP_SEC)
    return callable_fn()


# ============================================================
# 액면가 조회
# ============================================================

def _parse_face_value(raw) -> Optional[int]:
    """DART 액면가 문자열 파싱. '-'·빈값·0 → None (무액면 또는 무효)."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s == "-" or s == "0":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _get_existing_face_value(api_key: str, corp_code: str, cache: dict, today: date) -> Optional[int]:
    """기존 보통주 액면가 조회. corp_code 별 90일 캐시."""
    fv_cache = cache.setdefault("face_value_by_corp", {})
    entry = fv_cache.get(corp_code)
    if entry:
        synced_at_str = entry.get("synced_at")
        try:
            synced_at = date.fromisoformat(synced_at_str) if synced_at_str else None
        except ValueError:
            synced_at = None
        if synced_at and (today - synced_at).days < FACE_VALUE_TTL_DAYS:
            fv = entry.get("face_value")
            return int(fv) if fv is not None else None

    # 캐시 미스 또는 TTL 초과 → 재동기화 (직전 사업연도 → 이전 사업연도 폴백)
    for year_offset in (1, 2):
        bsns_year = str(today.year - year_offset)
        data = _http_get_json(DART_STOCK_TOTQY_URL, {
            "crtfc_key": api_key,
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": "11011",
        })
        if not data:
            continue
        rows = data.get("list", [])
        if not rows:
            continue
        face_value = None
        for row in rows:
            if "보통주" in str(row.get("se", "")):
                fv = _parse_face_value(row.get("stk_fv"))
                if fv is not None:
                    face_value = fv
                    break
        if face_value is None:
            for row in rows:
                fv = _parse_face_value(row.get("stk_fv"))
                if fv is not None:
                    face_value = fv
                    break
        if face_value is not None:
            fv_cache[corp_code] = {
                "face_value": face_value,
                "synced_at": today.isoformat(),
            }
            return face_value
    return None


def _fetch_piic_face_value(api_key: str, corp_code: str, rcept_no: str, rcept_dt: date) -> Optional[int]:
    """piicDecsn.json 1일 창 조회 후 rcept_no 매칭 행의 fv_ps 추출."""
    de = rcept_dt.strftime("%Y%m%d")
    data = _http_get_json(DART_PIIC_URL, {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": de,
        "end_de": de,
    })
    if not data:
        return None
    rows = data.get("list", [])
    matched = next((r for r in rows if r.get("rcept_no") == rcept_no), None)
    if not matched:
        return None
    return _parse_face_value(matched.get("fv_ps"))


# ============================================================
# 미래 일정 추출: 무상증자/자사주취득
# ============================================================

def _fetch_bonus_issuance_schedule(api_key: str, corp_code: str, rcept_no: str,
                                   rcept_dt: date) -> Optional[dict]:
    """무상증자결정 상세에서 신주배정기준일/상장예정일 추출."""
    de = rcept_dt.strftime("%Y%m%d")
    data = _http_get_json(DART_BONUS_ISSUE_URL, {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": de,
        "end_de": de,
    })
    if not data:
        return None
    rows = data.get("list", [])
    matched = next((r for r in rows if r.get("rcept_no") == rcept_no), None)
    if not matched:
        return None
    asstn = _parse_dart_date(matched.get("nstk_asstn_stdde"))
    lstg = _parse_dart_date(matched.get("nstk_lstg_pln_de"))
    if asstn is None and lstg is None:
        return None
    return {
        "asstn_stdde": asstn.isoformat() if asstn else None,
        "lstg_pln_de": lstg.isoformat() if lstg else None,
    }


def _fetch_treasury_acquisition_schedule(api_key: str, corp_code: str, rcept_no: str,
                                        rcept_dt: date) -> Optional[dict]:
    """자기주식취득결정 상세에서 취득예정기간 시작/종료일 추출."""
    de = rcept_dt.strftime("%Y%m%d")
    data = _http_get_json(DART_TREASURY_AQ_URL, {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": de,
        "end_de": de,
    })
    if not data:
        return None
    rows = data.get("list", [])
    matched = next((r for r in rows if r.get("rcept_no") == rcept_no), None)
    if not matched:
        return None
    bgd = _parse_dart_date(matched.get("aqexpd_bgd"))
    edd = _parse_dart_date(matched.get("aqexpd_edd"))
    if bgd is None and edd is None:
        return None
    return {
        "aqexpd_bgd": bgd.isoformat() if bgd else None,
        "aqexpd_edd": edd.isoformat() if edd else None,
    }


SCHEDULE_FETCHERS = {
    "무상증자결정": _fetch_bonus_issuance_schedule,
    "자기주식취득결정": _fetch_treasury_acquisition_schedule,
}


# ============================================================
# 유상증자 enrich + cross-run 재시도 큐
# ============================================================

def _now_aware() -> datetime:
    return datetime.now().astimezone()


def _build_resolved_entry(rcept_no: str, corp_code: str, new_fv: int,
                          existing_fv: int, retries: int) -> dict:
    return {
        "rcept_no": rcept_no,
        "corp_code": corp_code,
        "disclosure_type": "유상증자결정",
        "status": "resolved",
        "fetched_at": _now_aware().isoformat(),
        "retries": retries,
        "data": {
            "new_face_value": new_fv,
            "existing_face_value": existing_fv,
            "decreased": new_fv < existing_fv,
        },
    }


def _build_failure_entry(rcept_no: str, corp_code: str, retries: int) -> dict:
    is_unknown = retries >= 2
    return {
        "rcept_no": rcept_no,
        "corp_code": corp_code,
        "disclosure_type": "유상증자결정",
        "status": "resolved_unknown" if is_unknown else "pending_retry",
        "fetched_at": _now_aware().isoformat() if is_unknown else None,
        "retries": retries,
        "last_error": "fetch_failed",
        "data": None,
    }


def _enrich_rights_issue(api_key: str, corp_code: str, rcept_no: str, rcept_dt: date,
                         cache: dict, today: date) -> tuple[Optional[dict], str]:
    """유상증자 1건을 캐시 또는 상세 API로 enrich.

    Returns: (data_or_none, status)
    """
    entries = cache.setdefault("entries", {})
    entry = entries.get(rcept_no)

    if entry:
        status = entry.get("status")
        if status == "resolved":
            return entry.get("data"), "resolved"
        if status == "resolved_unknown":
            return None, "resolved_unknown"
        if status == "pending_retry":
            # _retry_pending_entries 가 이미 처리했어야 함. 도달했다면 미해결로 간주.
            return None, "pending_retry"

    # 캐시 미스 → 첫 시도 (in-process 3초 retry 1회 포함)
    new_fv = _with_retry(lambda: _fetch_piic_face_value(api_key, corp_code, rcept_no, rcept_dt))
    existing_fv = _get_existing_face_value(api_key, corp_code, cache, today) if new_fv is not None else None

    if new_fv is not None and existing_fv is not None:
        entries[rcept_no] = _build_resolved_entry(rcept_no, corp_code, new_fv, existing_fv, retries=0)
        return entries[rcept_no]["data"], "resolved"

    retries = (entry.get("retries", 0) + 1) if entry else 1
    entries[rcept_no] = _build_failure_entry(rcept_no, corp_code, retries)
    return None, entries[rcept_no]["status"]


def _build_schedule_resolved_entry(rcept_no: str, corp_code: str, disclosure_type: str,
                                   data: dict, retries: int) -> dict:
    return {
        "rcept_no": rcept_no,
        "corp_code": corp_code,
        "disclosure_type": disclosure_type,
        "status": "resolved",
        "fetched_at": _now_aware().isoformat(),
        "retries": retries,
        "data": data,
    }


def _build_schedule_failure_entry(rcept_no: str, corp_code: str, disclosure_type: str,
                                  retries: int) -> dict:
    is_unknown = retries >= 2
    return {
        "rcept_no": rcept_no,
        "corp_code": corp_code,
        "disclosure_type": disclosure_type,
        "status": "resolved_unknown" if is_unknown else "pending_retry",
        "fetched_at": _now_aware().isoformat() if is_unknown else None,
        "retries": retries,
        "last_error": "fetch_failed",
        "data": None,
    }


def _enrich_schedule(api_key: str, corp_code: str, rcept_no: str, rcept_dt: date,
                     cache: dict, disclosure_type: str) -> tuple[Optional[dict], str]:
    """무상증자결정·자기주식취득결정 일정을 캐시 또는 상세 API로 enrich.

    Returns: (data_or_none, status)
    """
    fetcher = SCHEDULE_FETCHERS.get(disclosure_type)
    if fetcher is None:
        return None, "unknown_type"

    entries = cache.setdefault("entries", {})
    entry = entries.get(rcept_no)

    if entry:
        status = entry.get("status")
        if status == "resolved":
            return entry.get("data"), "resolved"
        if status == "resolved_unknown":
            return None, "resolved_unknown"
        if status == "pending_retry":
            return None, "pending_retry"

    result = _with_retry(lambda: fetcher(api_key, corp_code, rcept_no, rcept_dt))
    if result is not None:
        entries[rcept_no] = _build_schedule_resolved_entry(
            rcept_no, corp_code, disclosure_type, result, retries=0
        )
        return result, "resolved"

    retries = (entry.get("retries", 0) + 1) if entry else 1
    entries[rcept_no] = _build_schedule_failure_entry(
        rcept_no, corp_code, disclosure_type, retries
    )
    return None, entries[rcept_no]["status"]


def _retry_pending_entries(cache: dict, api_key: str, today: date) -> None:
    """list.json 호출 이전에 pending_retry 엔트리 1회 재시도 (in-process retry 없음).

    성공 → resolved 로 승격. 실패 → retries+1 후 resolved_unknown(2회) 또는 pending_retry 유지.
    disclosure_type 별로 적절한 상세 API 로 디스패치한다.
    """
    entries = cache.setdefault("entries", {})
    pending = [
        (rcept_no, entry)
        for rcept_no, entry in list(entries.items())
        if entry.get("status") == "pending_retry"
    ]
    for rcept_no, entry in pending:
        corp_code = entry.get("corp_code", "")
        if not corp_code:
            continue
        rcept_dt = _parse_rcept_dt(rcept_no[:8])
        if not rcept_dt:
            continue
        disc_type = entry.get("disclosure_type", "유상증자결정")
        retries_so_far = entry.get("retries", 1)

        if disc_type == "유상증자결정":
            new_fv = _fetch_piic_face_value(api_key, corp_code, rcept_no, rcept_dt)
            existing_fv = _get_existing_face_value(api_key, corp_code, cache, today) if new_fv is not None else None
            if new_fv is not None and existing_fv is not None:
                entries[rcept_no] = _build_resolved_entry(
                    rcept_no, corp_code, new_fv, existing_fv,
                    retries=retries_so_far,
                )
            else:
                entries[rcept_no] = _build_failure_entry(rcept_no, corp_code, retries_so_far + 1)
        elif disc_type in SCHEDULE_FETCHERS:
            fetcher = SCHEDULE_FETCHERS[disc_type]
            result = fetcher(api_key, corp_code, rcept_no, rcept_dt)
            if result is not None:
                entries[rcept_no] = _build_schedule_resolved_entry(
                    rcept_no, corp_code, disc_type, result, retries=retries_so_far
                )
            else:
                entries[rcept_no] = _build_schedule_failure_entry(
                    rcept_no, corp_code, disc_type, retries_so_far + 1
                )


# ============================================================
# 공개 진입점
# ============================================================

def fetch_dart_target_events(api_key: str,
                             today: Optional[date] = None,
                             past_window_days: int = 14,
                             max_pages: int = 5) -> list[dict]:
    """
    최근 past_window_days 일간 접수된 B타입 공시 중 타겟 유형만 추출.
    여러 페이지를 순회 (페이지당 100건).

    이벤트 dict 신규 키:
      - flags: list[str]  — 부가 태그. 유상증자 + 신주액면<기존액면이면 "preferred_share_issuance"
      - face_value_meta: dict | None  — {pre, post, decreased, source}
    """
    if today is None:
        today = date.today()
    start = today - timedelta(days=past_window_days)

    cache = _load_detail_cache()
    _purge_stale_resolved(cache, _now_aware())
    _retry_pending_entries(cache, api_key, today)

    hits: list[dict] = []
    for page_no in range(1, max_pages + 1):
        data = _http_get_json(DART_LIST_URL, {
            "crtfc_key": api_key,
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": today.strftime("%Y%m%d"),
            "pblntf_ty": "B",
            "page_no": str(page_no),
            "page_count": "100",
        })
        if not data:
            break
        items = data.get("list", [])
        if not items:
            break
        for it in items:
            report_nm = it.get("report_nm", "")
            matched = _title_matches(report_nm)
            if not matched:
                continue
            matched_type, direction_tag = matched
            rcept_dt = _parse_rcept_dt(it.get("rcept_dt", ""))
            if rcept_dt is None:
                continue
            rcept_no = it.get("rcept_no", "")
            corp_code = (it.get("corp_code") or "").strip()
            corp_name = it.get("corp_name", "")
            stock_code = (it.get("stock_code") or "").strip() or None

            flags: list[str] = []
            face_value_meta: Optional[dict] = None
            schedule_meta: Optional[dict] = None
            body_suffix = ""

            if matched_type == "유상증자결정" and corp_code and rcept_no:
                fv_data, _status = _enrich_rights_issue(
                    api_key, corp_code, rcept_no, rcept_dt, cache, today
                )
                if fv_data:
                    face_value_meta = {
                        "pre": fv_data.get("existing_face_value"),
                        "post": fv_data.get("new_face_value"),
                        "decreased": bool(fv_data.get("decreased")),
                        "source": "detail_api",
                    }
                    if fv_data.get("decreased"):
                        flags.append("preferred_share_issuance")
                else:
                    body_suffix = " (※ 신주 액면가 조회 실패 — 다음 수집 시 재시도)"
            elif matched_type in SCHEDULE_FETCHERS and corp_code and rcept_no:
                sched_data, _status = _enrich_schedule(
                    api_key, corp_code, rcept_no, rcept_dt, cache, matched_type
                )
                if sched_data:
                    schedule_meta = sched_data
                else:
                    body_suffix = " (※ 일정 상세 조회 실패 — 다음 수집 시 재시도)"

            base_body = (
                f"{corp_name} 의 '{report_nm}' DART 공시 접수. "
                f"배정·상장 등 세부 일정은 원본 공시 확인."
            )
            disclosure_url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
            hits.append({
                "type": "DISCLOSURE",
                "event_date": rcept_dt.isoformat(),
                "event_date_label": None,
                "title": f"{corp_name} · {matched_type}",
                "body_snippet": base_body + body_suffix,
                "source_url": disclosure_url,
                "source_label": "DART 공시",
                "news": None,
                "category_hints": [],
                "disclosure_type": matched_type,
                "direction": direction_tag,
                "stock_name_hint": corp_name,
                "stock_code": stock_code,
                "flags": flags,
                "face_value_meta": face_value_meta,
                "schedule_meta": schedule_meta,
            })

            if schedule_meta:
                hits.extend(_build_future_events(
                    matched_type, schedule_meta, today, rcept_dt,
                    corp_name, stock_code, direction_tag, disclosure_url, report_nm,
                ))
        if len(items) < 100:
            break

    _save_detail_cache(cache)
    return hits


def _build_future_events(matched_type: str, schedule_meta: dict, today: date,
                         rcept_dt: date, corp_name: str, stock_code: Optional[str],
                         direction_tag: str, disclosure_url: str,
                         report_nm: str) -> list[dict]:
    """무상증자/자사주취득의 미래 일정을 캘린더 이벤트로 변환.

    오늘 이후 일정만 추가한다 (과거 일정은 캘린더 노이즈).
    """
    events: list[dict] = []
    if matched_type == "무상증자결정":
        future_date_iso = schedule_meta.get("asstn_stdde") or schedule_meta.get("lstg_pln_de")
        label_kind = "신주배정기준일" if schedule_meta.get("asstn_stdde") else "신주상장예정일"
        if future_date_iso:
            future_dt = _safe_iso_to_date(future_date_iso)
            if future_dt and future_dt > today:
                events.append({
                    "type": "DISCLOSURE",
                    "event_date": future_dt.isoformat(),
                    "event_date_label": label_kind,
                    "title": f"{corp_name} · 무상증자 {label_kind}",
                    "body_snippet": (
                        f"{corp_name} '{report_nm}' 의 {label_kind}. "
                        f"공시 접수일: {rcept_dt.isoformat()}."
                    ),
                    "source_url": disclosure_url,
                    "source_label": "DART 공시",
                    "news": None,
                    "category_hints": [],
                    "disclosure_type": matched_type,
                    "direction": direction_tag,
                    "stock_name_hint": corp_name,
                    "stock_code": stock_code,
                    "flags": ["future_schedule"],
                    "face_value_meta": None,
                    "schedule_meta": schedule_meta,
                })
    elif matched_type == "자기주식취득결정":
        future_date_iso = schedule_meta.get("aqexpd_bgd")
        if future_date_iso:
            future_dt = _safe_iso_to_date(future_date_iso)
            if future_dt and future_dt > today:
                end_iso = schedule_meta.get("aqexpd_edd")
                end_suffix = f" (종료 예정: {end_iso})" if end_iso else ""
                events.append({
                    "type": "DISCLOSURE",
                    "event_date": future_dt.isoformat(),
                    "event_date_label": "자사주 취득 시작",
                    "title": f"{corp_name} · 자사주 취득 시작",
                    "body_snippet": (
                        f"{corp_name} '{report_nm}' 의 취득예상기간 시작일.{end_suffix} "
                        f"공시 접수일: {rcept_dt.isoformat()}."
                    ),
                    "source_url": disclosure_url,
                    "source_label": "DART 공시",
                    "news": None,
                    "category_hints": [],
                    "disclosure_type": matched_type,
                    "direction": direction_tag,
                    "stock_name_hint": corp_name,
                    "stock_code": stock_code,
                    "flags": ["future_schedule"],
                    "face_value_meta": None,
                    "schedule_meta": schedule_meta,
                })
    return events


def _safe_iso_to_date(s: str) -> Optional[date]:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None
