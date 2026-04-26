"""dart_disclosure 단위 테스트.

mock 으로 requests.get 을 가로채 외부 호출 없이 검증한다.
"""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import dart_disclosure as dd


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def fast_sleep():
    """모든 테스트에서 time.sleep 을 무력화."""
    with patch("dart_disclosure.time.sleep"):
        yield


@pytest.fixture
def tmp_cache_path(monkeypatch, tmp_path):
    """테스트마다 임시 캐시 경로 사용."""
    cache_file = tmp_path / "dart_detail_cache.json"
    monkeypatch.setattr(dd, "CACHE_PATH", str(cache_file))
    return cache_file


@pytest.fixture
def mock_get():
    with patch.object(dd.requests, "get") as m:
        yield m


def _resp(payload):
    r = MagicMock()
    r.json.return_value = payload
    return r


def _ok(rows):
    return _resp({"status": "000", "list": rows})


def _no_data():
    return _resp({"status": "013", "list": []})


def _list_item(rcept_no, corp_code, corp_name, report_nm, rcept_dt="20260424",
               stock_code=""):
    return {
        "rcept_no": rcept_no,
        "rcept_dt": rcept_dt,
        "corp_code": corp_code,
        "corp_name": corp_name,
        "stock_code": stock_code,
        "report_nm": report_nm,
    }


def _route(handlers):
    """URL 부분 문자열 → response 매핑. 매칭 안 되면 _no_data()."""
    def side_effect(url, params=None, timeout=15):
        for needle, response in handlers.items():
            if needle in url:
                return response
        return _no_data()
    return side_effect


# ============================================================
# Title matching
# ============================================================

def test_title_matches_excludes_capital_reduction():
    assert dd._title_matches("주식회사 ○○ 감자결정") is None


def test_title_matches_includes_rights_issue():
    result = dd._title_matches("유상증자결정")
    assert result == ("유상증자결정", "negative")


def test_title_matches_excludes_stock_consolidation():
    assert dd._title_matches("주식병합결정 공시") is None


def test_title_matches_excludes_combined_paid_unpaid():
    assert dd._title_matches("유무상증자결정") is None


# ============================================================
# Face value parsing
# ============================================================

def test_parse_face_value_normal():
    assert dd._parse_face_value("500") == 500


def test_parse_face_value_with_comma():
    assert dd._parse_face_value("5,000") == 5000


def test_parse_face_value_dash_returns_none():
    assert dd._parse_face_value("-") is None


def test_parse_face_value_empty_returns_none():
    assert dd._parse_face_value("") is None
    assert dd._parse_face_value(None) is None


def test_parse_face_value_zero_returns_none():
    assert dd._parse_face_value("0") is None


# ============================================================
# Cache I/O
# ============================================================

def test_cache_round_trip(tmp_cache_path):
    cache = {
        "version": 1,
        "entries": {
            "20250115000123": {"status": "resolved", "data": {"new_face_value": 500}}
        },
        "face_value_by_corp": {
            "00126380": {"face_value": 500, "synced_at": "2025-01-10"}
        },
    }
    dd._save_detail_cache(cache)
    loaded = dd._load_detail_cache()
    assert loaded["entries"]["20250115000123"]["data"]["new_face_value"] == 500
    assert loaded["face_value_by_corp"]["00126380"]["face_value"] == 500


def test_cache_load_missing_returns_default(tmp_cache_path):
    assert not tmp_cache_path.exists()
    cache = dd._load_detail_cache()
    assert cache == {"version": 1, "entries": {}, "face_value_by_corp": {}}


def test_cache_load_corrupt_returns_default(tmp_cache_path):
    tmp_cache_path.write_text("not valid json")
    cache = dd._load_detail_cache()
    assert cache["entries"] == {}


# ============================================================
# preferred_share_issuance flag attachment
# ============================================================

def test_flag_attached_when_new_lt_existing(tmp_cache_path, mock_get):
    today = date(2026, 4, 24)
    mock_get.side_effect = _route({
        "list.json": _ok([_list_item("20260424000001", "00126380", "테스트사", "유상증자결정")]),
        "piicDecsn": _ok([{"rcept_no": "20260424000001", "fv_ps": "100"}]),
        "stockTotqyItr": _ok([{"se": "보통주", "stk_fv": "500"}]),
    })

    events = dd.fetch_dart_target_events("FAKE_KEY", today=today)
    assert len(events) == 1
    e = events[0]
    assert e["disclosure_type"] == "유상증자결정"
    assert "preferred_share_issuance" in e["flags"]
    assert e["face_value_meta"]["pre"] == 500
    assert e["face_value_meta"]["post"] == 100
    assert e["face_value_meta"]["decreased"] is True


def test_flag_not_attached_when_new_eq_existing(tmp_cache_path, mock_get):
    today = date(2026, 4, 24)
    mock_get.side_effect = _route({
        "list.json": _ok([_list_item("20260424000002", "00126380", "테스트사", "유상증자결정")]),
        "piicDecsn": _ok([{"rcept_no": "20260424000002", "fv_ps": "500"}]),
        "stockTotqyItr": _ok([{"se": "보통주", "stk_fv": "500"}]),
    })

    events = dd.fetch_dart_target_events("FAKE_KEY", today=today)
    assert len(events) == 1
    assert events[0]["flags"] == []
    assert events[0]["face_value_meta"]["decreased"] is False


def test_rights_issue_emitted_on_detail_failure(tmp_cache_path, mock_get):
    """piicDecsn 실패 → 이벤트는 emit, 플래그 없음, body 접미 추가."""
    today = date(2026, 4, 24)
    mock_get.side_effect = _route({
        "list.json": _ok([_list_item("20260424000003", "00126380", "테스트사", "유상증자결정")]),
        # piicDecsn / stockTotqyItr 매칭 없으면 _no_data() (status=013) 반환 → 실패
    })

    events = dd.fetch_dart_target_events("FAKE_KEY", today=today)
    assert len(events) == 1
    assert events[0]["flags"] == []
    assert events[0]["face_value_meta"] is None
    assert "신주 액면가 조회 실패" in events[0]["body_snippet"]


# ============================================================
# Cross-run retry queue
# ============================================================

def test_pending_retry_promoted_to_resolved(tmp_cache_path, mock_get):
    """캐시에 pending_retry 가 있으면 list.json 전에 재시도 → 성공 시 resolved."""
    today = date(2026, 4, 24)
    pre_seed = {
        "version": 1,
        "entries": {
            "20260420000001": {
                "rcept_no": "20260420000001",
                "corp_code": "00126380",
                "disclosure_type": "유상증자결정",
                "status": "pending_retry",
                "retries": 1,
                "fetched_at": None,
                "data": None,
            }
        },
        "face_value_by_corp": {},
    }
    dd._save_detail_cache(pre_seed)

    mock_get.side_effect = _route({
        "list.json": _no_data(),
        "piicDecsn": _ok([{"rcept_no": "20260420000001", "fv_ps": "100"}]),
        "stockTotqyItr": _ok([{"se": "보통주", "stk_fv": "500"}]),
    })

    dd.fetch_dart_target_events("FAKE_KEY", today=today)

    cache = dd._load_detail_cache()
    entry = cache["entries"]["20260420000001"]
    assert entry["status"] == "resolved"
    assert entry["data"]["decreased"] is True


def test_pending_retry_to_resolved_unknown(tmp_cache_path, mock_get):
    """pending_retry retries=1 → 다음 run 도 실패 → resolved_unknown."""
    today = date(2026, 4, 24)
    pre_seed = {
        "version": 1,
        "entries": {
            "20260420000002": {
                "rcept_no": "20260420000002",
                "corp_code": "00126380",
                "disclosure_type": "유상증자결정",
                "status": "pending_retry",
                "retries": 1,
                "fetched_at": None,
                "data": None,
            }
        },
        "face_value_by_corp": {},
    }
    dd._save_detail_cache(pre_seed)

    mock_get.side_effect = _route({"list.json": _no_data()})

    dd.fetch_dart_target_events("FAKE_KEY", today=today)

    cache = dd._load_detail_cache()
    entry = cache["entries"]["20260420000002"]
    assert entry["status"] == "resolved_unknown"
    assert entry["retries"] == 2


# ============================================================
# Face value cache TTL
# ============================================================

def test_face_value_cache_refreshes_after_ttl(tmp_cache_path, mock_get):
    today = date(2026, 4, 24)
    old_synced = (today - timedelta(days=100)).isoformat()
    pre_seed = {
        "version": 1,
        "entries": {},
        "face_value_by_corp": {
            "00126380": {"face_value": 5000, "synced_at": old_synced}
        },
    }
    dd._save_detail_cache(pre_seed)

    mock_get.side_effect = _route({
        "list.json": _ok([_list_item("20260424000005", "00126380", "테스트사", "유상증자결정")]),
        "piicDecsn": _ok([{"rcept_no": "20260424000005", "fv_ps": "100"}]),
        "stockTotqyItr": _ok([{"se": "보통주", "stk_fv": "500"}]),  # 새 액면가
    })

    dd.fetch_dart_target_events("FAKE_KEY", today=today)

    cache = dd._load_detail_cache()
    fv_entry = cache["face_value_by_corp"]["00126380"]
    assert fv_entry["face_value"] == 500
    assert fv_entry["synced_at"] == today.isoformat()


def test_face_value_cache_within_ttl_uses_cached(tmp_cache_path, mock_get):
    today = date(2026, 4, 24)
    fresh_synced = (today - timedelta(days=10)).isoformat()
    pre_seed = {
        "version": 1,
        "entries": {},
        "face_value_by_corp": {
            "00126380": {"face_value": 5000, "synced_at": fresh_synced}
        },
    }
    dd._save_detail_cache(pre_seed)

    # stockTotqyItr 호출되면 안 됨 → 라우팅에서 일부러 빼고 호출 시 fail 단언
    stock_call_count = {"n": 0}

    def side_effect(url, params=None, timeout=15):
        if "stockTotqyItr" in url:
            stock_call_count["n"] += 1
        if "list.json" in url:
            return _ok([_list_item("20260424000010", "00126380", "테스트사", "유상증자결정")])
        if "piicDecsn" in url:
            return _ok([{"rcept_no": "20260424000010", "fv_ps": "100"}])
        return _no_data()

    mock_get.side_effect = side_effect
    events = dd.fetch_dart_target_events("FAKE_KEY", today=today)

    assert stock_call_count["n"] == 0
    assert events[0]["face_value_meta"]["pre"] == 5000


# ============================================================
# 회귀 방지: crDecsn 호출 0회 + 감자 제외
# ============================================================

def test_no_crDecsn_calls_and_capital_reduction_excluded(tmp_cache_path, mock_get):
    today = date(2026, 4, 24)

    def side_effect(url, params=None, timeout=15):
        assert "crDecsn" not in url, "crDecsn URL 호출 금지 (감자 제외 회귀 방지)"
        if "list.json" in url:
            return _ok([
                _list_item("20260424000006", "00111111", "감자사", "감자결정"),
                _list_item("20260424000007", "00222222", "유증사", "유상증자결정"),
            ])
        if "piicDecsn" in url:
            return _ok([{"rcept_no": "20260424000007", "fv_ps": "500"}])
        if "stockTotqyItr" in url:
            return _ok([{"se": "보통주", "stk_fv": "500"}])
        return _no_data()

    mock_get.side_effect = side_effect
    events = dd.fetch_dart_target_events("FAKE_KEY", today=today)
    types = {e["disclosure_type"] for e in events}
    assert types == {"유상증자결정"}


# ============================================================
# 무액면 silent skip
# ============================================================

def test_no_par_silent_skip(tmp_cache_path, mock_get):
    """fv_ps='-' (무액면주식) → 비교 없이 skip, 이벤트는 emit (실패 처리)."""
    today = date(2026, 4, 24)
    mock_get.side_effect = _route({
        "list.json": _ok([_list_item("20260424000008", "00333333", "무액면사", "유상증자결정")]),
        "piicDecsn": _ok([{"rcept_no": "20260424000008", "fv_ps": "-"}]),
    })

    events = dd.fetch_dart_target_events("FAKE_KEY", today=today)
    assert len(events) == 1
    assert events[0]["flags"] == []
    assert events[0]["face_value_meta"] is None
    assert "신주 액면가 조회 실패" in events[0]["body_snippet"]
