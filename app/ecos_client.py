"""
한국은행 ECOS OpenAPI 클라이언트.

목적:
1. MACRO 이벤트(`type='MACRO'`)에 ECOS 시계열의 현재값/변동을 컨텍스트로 부착
2. 뉴스 카테고리 매칭(fomc/fx/oil)을 ECOS 실측 변동과 cross-check 해 오탐 강등

ECOS 자체는 historical 시계열만 제공 — 미래 일정은 bok_schedule 모듈 담당.
이 모듈은 "값"만 다룬다.

URL pattern (V1):
  https://ecos.bok.or.kr/api/StatisticSearch/{KEY}/json/kr/{S}/{E}/{STAT}/{CYCLE}/{S_TIME}/{E_TIME}[/ITEM1[/ITEM2[/ITEM3[/ITEM4]]]]

Cycle codes (V2 confirmed):
  A = annual, S = semi-annual, Q = quarterly, M = monthly, SM = semi-monthly, D = daily

Response (success):
  {"StatisticSearch": {"list_total_count": N, "row": [{"TIME": "202403", "DATA_VALUE": "3.50", "UNIT_NAME": "%", ...}]}}
Response (error):
  {"RESULT": {"CODE": "INFO-200", "MESSAGE": "..."}}
"""

import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from typing import Optional
from urllib.parse import quote

import requests


log = logging.getLogger(__name__)


ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ecos_cache.json")

ECOS_BASE_URL = "https://ecos.bok.or.kr/api/StatisticSearch"
ECOS_TIMEOUT = 15
ECOS_HEADERS = {"User-Agent": "Mozilla/5.0 (calendar-fixture-enrichment)"}


# ============================================================
# Indicator 레지스트리 — single source of truth
# ============================================================
# stat_code/items 는 ECOS DevGuide 의 "통계코드검색" 으로 확정. V2 미검증 항목은
# 라이브 호출 후 row 가 비면 사용자에게 보고하고 비활성.

INDICATORS: dict[str, dict] = {
    "base_rate": {
        "stat_code": "722Y001",
        "freq": "M",
        "items": ("0101000",),  # 한국은행 기준금리. 미지정 시 다른 정책금리들이 섞여 반환됨.
        "label": "한은 기준금리",
        "unit": "%",
        "movement_kind": "absolute",  # base_rate 는 pp 단위 차이가 의미. % 변동률 부적절.
    },
    "fx_usd_krw": {
        "stat_code": "731Y001",
        "freq": "D",
        "items": ("0000001",),  # 원·달러
        "label": "원·달러 환율",
        "unit": "원",
        "movement_kind": "pct",
    },
    "cpi": {
        "stat_code": "901Y009",
        "freq": "M",
        "items": ("0",),  # 총지수
        "label": "소비자물가지수",
        "unit": "지수",
        "movement_kind": "pct",
    },
    "dubai_oil": {
        "stat_code": "902Y003",
        "freq": "M",
        "items": ("010102",),  # 원유- Dubai (010101=WTI, 010103=Brent)
        "label": "두바이유 가격",
        "unit": "달러/배럴",
        "movement_kind": "pct",
    },
    # employment, ip, bop 등은 stat_code 가 아이템 코드 조합에 따라 다양해
    # MACRO 이벤트에 직접 매핑보다는 추후 별도 enrichment 로 확장.
}

# 카테고리 → indicator 매핑 (Module 3 cross-check 가 사용).
CATEGORY_TO_INDICATORS: dict[str, list[str]] = {
    "fomc": ["base_rate"],
    "fx": ["fx_usd_krw"],
    "oil": ["dubai_oil"],
}


# ============================================================
# API 키
# ============================================================

def load_ecos_key() -> Optional[str]:
    """환경변수 우선, 없으면 .env 파일에서 ECOS_API_KEY 읽기. dart 패턴 미러."""
    key = os.environ.get("ECOS_API_KEY")
    if key:
        return key.strip()
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("ECOS_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# ============================================================
# 캐시 I/O — 일일 TTL
# ============================================================

def _empty_cache() -> dict:
    return {"version": 1, "entries": {}}


def _load_cache() -> dict:
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
    return data


def _save_cache(cache: dict) -> None:
    cache_dir = os.path.dirname(CACHE_PATH) or "."
    os.makedirs(cache_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".ecos_cache_", dir=cache_dir)
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


# ============================================================
# 시간 포맷
# ============================================================

def _format_time(d: date, freq: str) -> str:
    """ECOS START_TIME / END_TIME 포맷."""
    if freq == "D":
        return d.strftime("%Y%m%d")
    if freq == "M":
        return d.strftime("%Y%m")
    if freq == "Q":
        return f"{d.year}{(d.month - 1) // 3 + 1}"
    if freq == "A":
        return f"{d.year}"
    if freq == "SM":  # 반월
        return d.strftime("%Y%m") + ("01" if d.day <= 15 else "02")
    if freq == "S":  # 반년
        return f"{d.year}{1 if d.month <= 6 else 2}"
    return d.strftime("%Y%m%d")


def _step_back(d: date, freq: str, n: int) -> date:
    """주기 단위로 n 만큼 과거로 이동."""
    if freq == "D":
        return d - timedelta(days=n)
    if freq == "M":
        y, m = d.year, d.month - n
        while m <= 0:
            m += 12
            y -= 1
        return date(y, m, 1)
    if freq == "Q":
        return date(d.year - (n // 4) - 1, max(1, d.month - 3 * (n % 4) or 1), 1)
    if freq == "A":
        return date(d.year - n, 1, 1)
    return d - timedelta(days=n)


def _normalize_time(t: str, freq: str) -> Optional[str]:
    """ECOS row TIME 을 ISO date 로 정규화."""
    if not t:
        return None
    try:
        if freq == "D" and len(t) == 8:
            return f"{t[:4]}-{t[4:6]}-{t[6:8]}"
        if freq == "M" and len(t) == 6:
            return f"{t[:4]}-{t[4:6]}-01"
        if freq == "A" and len(t) == 4:
            return f"{t}-01-01"
        if freq == "Q" and len(t) == 5:
            q = int(t[4])
            return f"{t[:4]}-{(q - 1) * 3 + 1:02d}-01"
        if freq == "S" and len(t) == 5:
            half = int(t[4])
            return f"{t[:4]}-{1 if half == 1 else 7:02d}-01"
    except (ValueError, IndexError):
        return None
    return None


# ============================================================
# fetch_indicator — 실 API 호출
# ============================================================

def _build_url(stat_code: str, freq: str, n: int, end_dt: date,
               item_codes: tuple[str, ...]) -> str:
    end_time = _format_time(end_dt, freq)
    start_time = _format_time(_step_back(end_dt, freq, max(n - 1, 0)), freq)
    key = load_ecos_key() or "TEST_KEY"
    parts = [ECOS_BASE_URL, quote(key, safe=""), "json", "kr", "1",
             str(max(n, 1)), stat_code, freq, start_time, end_time]
    parts.extend(quote(c, safe="") for c in item_codes)
    return "/".join(parts)


def fetch_indicator(stat_code: str, freq: str, n: int = 12,
                    item_codes: tuple[str, ...] = (),
                    *, end_date: Optional[date] = None,
                    use_cache: bool = True) -> list[dict]:
    """ECOS 시계열 최근 n 개 데이터 반환.

    실패(키 없음 / HTTP error / RESULT.CODE != INFO-000) 시 빈 리스트 + 1줄 로그.
    예외 raise 안 함.

    캐싱 정책: 같은 (stat_code, freq, items, end_date) 는 오늘 1회만 호출.
    """
    if not load_ecos_key():
        log.info("ECOS_API_KEY 없음 — fetch_indicator skip")
        return []

    end_dt = end_date or date.today()
    cache_key = "|".join([stat_code, freq, ",".join(item_codes), end_dt.isoformat()])

    cache = _load_cache() if use_cache else _empty_cache()
    entry = cache["entries"].get(cache_key)
    today_iso = date.today().isoformat()
    if entry and entry.get("fetched_at") == today_iso:
        return entry.get("rows", [])

    url = _build_url(stat_code, freq, n, end_dt, item_codes)
    try:
        r = requests.get(url, headers=ECOS_HEADERS, timeout=ECOS_TIMEOUT)
    except requests.RequestException as exc:
        log.warning("ECOS HTTP error %s: %s", stat_code, exc)
        return entry.get("rows", []) if entry else []

    if r.status_code != 200:
        log.warning("ECOS HTTP %s for %s", r.status_code, stat_code)
        return entry.get("rows", []) if entry else []

    try:
        payload = r.json()
    except (ValueError, json.JSONDecodeError):
        log.warning("ECOS non-JSON response for %s", stat_code)
        return entry.get("rows", []) if entry else []

    if "RESULT" in payload:
        code = payload["RESULT"].get("CODE", "?")
        msg = payload["RESULT"].get("MESSAGE", "")
        log.warning("ECOS error %s: %s (%s)", code, msg, stat_code)
        return entry.get("rows", []) if entry else []

    container = payload.get("StatisticSearch", {})
    rows_raw = container.get("row", []) if isinstance(container, dict) else []

    rows: list[dict] = []
    for raw in rows_raw:
        time_str = raw.get("TIME", "")
        value_str = raw.get("DATA_VALUE", "")
        if not value_str or value_str.strip() in ("-", "."):
            continue
        try:
            value = float(value_str)
        except ValueError:
            continue
        norm_date = _normalize_time(time_str, freq)
        if not norm_date:
            continue
        rows.append({
            "date": norm_date,
            "value": value,
            "unit": raw.get("UNIT_NAME", ""),
        })

    if use_cache and rows:
        cache["entries"][cache_key] = {
            "fetched_at": today_iso,
            "rows": rows,
        }
        try:
            _save_cache(cache)
        except OSError as exc:
            log.warning("ECOS cache save failed: %s", exc)

    return rows


# ============================================================
# get_indicator_movement — 최근 변동 요약
# ============================================================

def get_indicator_movement(indicator_id: str, *,
                           ref_date: Optional[date] = None) -> Optional[dict]:
    """레지스트리에 등록된 indicator 의 최근 2개 datapoint 변동.

    return: {"latest", "prior", "change_pct", "change_abs", "as_of", "kind"}
            또는 None (등록 안 됨 / 데이터 부족 / fetch 실패).
    """
    spec = INDICATORS.get(indicator_id)
    if spec is None:
        return None

    # 월/분기 통계는 발표 lag 으로 최근 1행만 잡힐 수 있어 여유분 요청.
    rows = fetch_indicator(
        stat_code=spec["stat_code"],
        freq=spec["freq"],
        n=6,
        item_codes=spec.get("items", ()),
        end_date=ref_date,
    )
    if len(rows) < 2:
        return None

    rows_sorted = sorted(rows, key=lambda r: r["date"])
    prior, latest = rows_sorted[-2], rows_sorted[-1]
    change_abs = latest["value"] - prior["value"]
    change_pct = (change_abs / prior["value"] * 100) if prior["value"] else 0.0

    return {
        "latest": latest["value"],
        "prior": prior["value"],
        "change_abs": change_abs,
        "change_pct": change_pct,
        "as_of": latest["date"],
        "kind": spec.get("movement_kind", "pct"),
    }


# ============================================================
# 이벤트 인리치먼트 — calendar_page.build_calendar_events 가 호출
# ============================================================

def _format_value(value: float, unit: str) -> str:
    """단위에 맞춰 보기 좋은 숫자 포맷."""
    if unit in ("원",):
        return f"{value:,.2f}{unit}"
    if unit == "%":
        return f"{value:.2f}%"
    return f"{value:,.2f}{unit}".rstrip()


def _format_change(movement: dict, label: str, unit: str) -> str:
    """human-readable 변동 tail 문구. movement_kind 에 따라 pp 또는 % 표기."""
    latest_str = _format_value(movement["latest"], unit)
    if movement["kind"] == "absolute":
        sign = "+" if movement["change_abs"] >= 0 else ""
        # 단위가 % 면 pp, 아니면 unit 원형
        suffix = "pp" if unit == "%" else unit
        delta = f"{sign}{movement['change_abs']:.2f}{suffix}"
    else:
        sign = "+" if movement["change_pct"] >= 0 else ""
        delta = f"{sign}{movement['change_pct']:.2f}%"
    return f"(현재 {label}: {latest_str}, 직전 대비 {delta})"


def enrich_event_with_ecos_context(event: dict) -> dict:
    """MACRO 이벤트의 body_snippet 에 ECOS 현재값/변동 tail 부착.

    indicator 는 event['_indicator'] (bok_schedule 가 명시한 키) 만 사용.
    category_hints 만으로는 KR/US 구분이 안 돼 (e.g. '미국 CPI' 와 '통계청 CPI'
    둘 다 fomc 힌트), 잘못된 한국 지표 컨텍스트를 부착할 위험. 따라서
    bok_schedule.get_macro_events 가 명시한 _indicator 가 있는 항목만 enrich.

    하드코딩 fallback (_MACRO_EVENTS_2026 의 미국 FOMC/CPI 등) 은 _indicator 가
    없어 자연스럽게 skip.
    """
    indicator_id = event.get("_indicator")
    if not indicator_id or indicator_id not in INDICATORS:
        return event

    # 미래 이벤트의 컨텍스트는 항상 "오늘 기준 최신값". event_date 가 미래면 ECOS 가
    # "데이터 없음" 응답하므로 today() 로 cap.
    try:
        ev_dt = date.fromisoformat(event.get("event_date", ""))
    except (ValueError, TypeError):
        ev_dt = date.today()
    ref = min(ev_dt, date.today())

    movement = get_indicator_movement(indicator_id, ref_date=ref)
    if movement is None:
        return event

    spec = INDICATORS[indicator_id]
    label = spec["label"]
    unit = spec.get("unit", "")

    event["ecos_context"] = {
        "indicator": indicator_id,
        "label": label,
        "latest": movement["latest"],
        "prior": movement["prior"],
        "change_abs": movement["change_abs"],
        "change_pct": movement["change_pct"],
        "as_of": movement["as_of"],
        "unit": unit,
        "kind": movement["kind"],
    }

    tail = " " + _format_change(movement, label, unit)
    body = event.get("body_snippet") or ""
    # 동일 tail 이 이미 붙어있으면 중복 방지 (재호출 안전)
    if tail.strip() not in body:
        event["body_snippet"] = body + tail
    return event


# ============================================================
# Cross-check — 뉴스 카테고리 매칭이 실제 ECOS 변동과 정합인지 검증
# ============================================================
# 임계값: 시작값. 데이터 보면서 튜닝.
CATEGORY_VERIFY_RULES: dict[str, dict] = {
    "fx": {
        "indicator": "fx_usd_krw",
        "window_days": 5,
        "abs_pct_threshold": 0.5,   # 5일 누적 |변동률| >= 0.5%
    },
    "fomc": {
        "indicator": "base_rate",
        "window_days": 60,
        "abs_pp_threshold": 0.25,   # 60일 내 절대 차 >= 0.25pp
        "proximity_days": 14,        # OR 14일 내 한은 금통위 임박
    },
    "oil": {
        "indicator": "dubai_oil",
        "window_days": 30,           # 두바이유는 월별 시계열 → 한 달 윈도
        "abs_pct_threshold": 2.0,    # |Δ| >= 2% (유가는 노이즈 커서 fx 보다 높게)
    },
}


def _is_indicator_unavailable(indicator_id: str) -> bool:
    """INDICATORS 에 등록 안 된 indicator → unavailable. 페널티 안 줌."""
    return indicator_id not in INDICATORS


def _has_proximate_bok_mpc(news_pub_date: date, proximity_days: int) -> bool:
    """뉴스 발행일 기준 proximity_days 이내 한은 금통위 일정이 있는지 확인.

    bok_schedule 모듈을 import 해 fetch_bok_mpc_schedule(year) 사용.
    실패 시 False — 보수적으로 "임박성 신호 없음" 처리.
    """
    try:
        from bok_schedule import fetch_bok_mpc_schedule
    except ImportError:
        return False
    try:
        events = fetch_bok_mpc_schedule(news_pub_date.year)
        if news_pub_date.year != (news_pub_date + timedelta(days=proximity_days)).year:
            events = events + fetch_bok_mpc_schedule(news_pub_date.year + 1)
    except Exception as exc:
        log.warning("BOK MPC fetch in cross-check failed: %s", exc)
        return False
    for e in events:
        try:
            ev_date = date.fromisoformat(e["event_date"])
        except (ValueError, KeyError):
            continue
        if 0 <= (ev_date - news_pub_date).days <= proximity_days:
            return True
    return False


def verify_category_with_ecos(category_id: str, news_pub_date: date,
                              window_days: Optional[int] = None) -> dict:
    """뉴스 카테고리 매칭이 ECOS 실측 변동과 정합인지 검증.

    Returns: {"verified": bool, "indicator": str, "movement_pct": float,
              "movement_abs": float, "reason": str}

    정책:
    - 매핑 규칙 없는 카테고리 → verified=True (페널티 없음)
    - indicator 미등록 / 데이터 부족 → verified=True (인프라 실패에 페널티 X)
    - fomc: 60일 내 |금리 변동| >= 0.25pp OR 14일 내 한은 금통위 임박이면 verified
    - fx:   5일 내 |환율 변동률| >= 0.5% 면 verified
    """
    rule = CATEGORY_VERIFY_RULES.get(category_id)
    if rule is None:
        return {"verified": True, "indicator": "", "movement_pct": 0.0,
                "movement_abs": 0.0, "reason": f"no rule for {category_id}"}

    indicator_id = rule["indicator"]
    if _is_indicator_unavailable(indicator_id):
        return {"verified": True, "indicator": indicator_id, "movement_pct": 0.0,
                "movement_abs": 0.0, "reason": f"{indicator_id} not registered"}

    spec = INDICATORS[indicator_id]
    eff_window = window_days if window_days is not None else rule["window_days"]

    # 5일/60일 윈도 안의 datapoint 모아 |누적 변동| 계산
    n_periods = eff_window if spec["freq"] == "D" else max(2, eff_window // 30 + 1)
    rows = fetch_indicator(stat_code=spec["stat_code"], freq=spec["freq"],
                           n=n_periods + 2, item_codes=spec.get("items", ()),
                           end_date=news_pub_date)
    if len(rows) < 2:
        return {"verified": True, "indicator": indicator_id, "movement_pct": 0.0,
                "movement_abs": 0.0, "reason": "insufficient data"}

    rows_sorted = sorted(rows, key=lambda r: r["date"])
    # window 안의 첫 점 vs 마지막 점
    first = rows_sorted[0]
    latest = rows_sorted[-1]
    movement_abs = latest["value"] - first["value"]
    movement_pct = (movement_abs / first["value"] * 100) if first["value"] else 0.0

    if category_id == "fomc":
        threshold = rule["abs_pp_threshold"]
        moved = abs(movement_abs) >= threshold
        if moved:
            return {"verified": True, "indicator": indicator_id,
                    "movement_pct": movement_pct, "movement_abs": movement_abs,
                    "reason": f"base_rate Δ{movement_abs:+.2f}pp in {eff_window}d (>= {threshold}pp)"}
        # proximity check
        if _has_proximate_bok_mpc(news_pub_date, rule["proximity_days"]):
            return {"verified": True, "indicator": indicator_id,
                    "movement_pct": 0.0, "movement_abs": movement_abs,
                    "reason": f"한은 금통위 임박 (≤{rule['proximity_days']}일)"}
        return {"verified": False, "indicator": indicator_id,
                "movement_pct": movement_pct, "movement_abs": movement_abs,
                "reason": f"base_rate Δ{movement_abs:+.2f}pp (< {threshold}pp), 금통위 임박 X"}

    # fx (default pct rule)
    threshold = rule.get("abs_pct_threshold", 0.5)
    if abs(movement_pct) >= threshold:
        return {"verified": True, "indicator": indicator_id,
                "movement_pct": movement_pct, "movement_abs": movement_abs,
                "reason": f"{indicator_id} Δ{movement_pct:+.2f}% in {eff_window}d (>= {threshold}%)"}
    return {"verified": False, "indicator": indicator_id,
            "movement_pct": movement_pct, "movement_abs": movement_abs,
            "reason": f"{indicator_id} Δ{movement_pct:+.2f}% (< {threshold}%)"}
