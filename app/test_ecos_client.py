"""ecos_client 단위 테스트."""

import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import ecos_client as ec


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_cache_path(monkeypatch, tmp_path):
    cache_file = tmp_path / "ecos_cache.json"
    monkeypatch.setattr(ec, "CACHE_PATH", str(cache_file))
    return cache_file


@pytest.fixture
def env_key(monkeypatch):
    """ECOS_API_KEY 가 설정된 상태."""
    monkeypatch.setenv("ECOS_API_KEY", "TEST_KEY")
    yield "TEST_KEY"


@pytest.fixture
def mock_get():
    with patch.object(ec.requests, "get") as m:
        yield m


def _resp(payload, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload
    return r


def _stat_search(rows):
    return {"StatisticSearch": {"list_total_count": len(rows), "row": rows}}


# ============================================================
# load_ecos_key
# ============================================================

def test_load_ecos_key_from_env(env_key):
    assert ec.load_ecos_key() == "TEST_KEY"


def test_load_ecos_key_returns_none_when_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    fake_env = tmp_path / ".env"
    fake_env.write_text("DART_API_KEY=somekey\n")
    monkeypatch.setattr(ec, "ENV_PATH", str(fake_env))
    assert ec.load_ecos_key() is None


def test_load_ecos_key_from_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    fake_env = tmp_path / ".env"
    fake_env.write_text('ECOS_API_KEY="abc123"\nDART_API_KEY=xyz\n')
    monkeypatch.setattr(ec, "ENV_PATH", str(fake_env))
    assert ec.load_ecos_key() == "abc123"


# ============================================================
# Time formatting
# ============================================================

def test_format_time_daily():
    assert ec._format_time(date(2026, 4, 25), "D") == "20260425"


def test_format_time_monthly():
    assert ec._format_time(date(2026, 4, 25), "M") == "202604"


def test_format_time_quarterly():
    assert ec._format_time(date(2026, 4, 25), "Q") == "20262"
    assert ec._format_time(date(2026, 1, 15), "Q") == "20261"
    assert ec._format_time(date(2026, 12, 31), "Q") == "20264"


def test_format_time_annual():
    assert ec._format_time(date(2026, 4, 25), "A") == "2026"


def test_step_back_monthly_crosses_year():
    out = ec._step_back(date(2026, 3, 15), "M", 5)
    assert out == date(2025, 10, 1)


def test_normalize_time_daily():
    assert ec._normalize_time("20260425", "D") == "2026-04-25"


def test_normalize_time_monthly():
    assert ec._normalize_time("202604", "M") == "2026-04-01"


def test_normalize_time_quarterly():
    assert ec._normalize_time("20262", "Q") == "2026-04-01"


def test_normalize_time_invalid():
    assert ec._normalize_time("garbage", "D") is None


# ============================================================
# fetch_indicator
# ============================================================

def test_fetch_indicator_no_key_returns_empty(monkeypatch, tmp_cache_path):
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    monkeypatch.setattr(ec, "ENV_PATH", "/nonexistent")
    assert ec.fetch_indicator("722Y001", "M", 3) == []


def test_fetch_indicator_parses_response(env_key, tmp_cache_path, mock_get):
    rows = [
        {"TIME": "202602", "DATA_VALUE": "2.75", "UNIT_NAME": "%"},
        {"TIME": "202603", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
        {"TIME": "202604", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
    ]
    mock_get.return_value = _resp(_stat_search(rows))
    out = ec.fetch_indicator("722Y001", "M", 3)
    assert len(out) == 3
    assert out[0] == {"date": "2026-02-01", "value": 2.75, "unit": "%"}


def test_fetch_indicator_handles_result_error(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp({"RESULT": {"CODE": "INFO-200", "MESSAGE": "no data"}})
    assert ec.fetch_indicator("BADCODE", "M", 3) == []


def test_fetch_indicator_handles_http_error(env_key, tmp_cache_path, mock_get):
    mock_get.side_effect = ec.requests.RequestException("network down")
    assert ec.fetch_indicator("722Y001", "M", 3) == []


def test_fetch_indicator_skips_dash_value(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202602", "DATA_VALUE": "-", "UNIT_NAME": "%"},
        {"TIME": "202603", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
    ]))
    out = ec.fetch_indicator("722Y001", "M", 3)
    assert len(out) == 1
    assert out[0]["date"] == "2026-03-01"


def test_fetch_indicator_caches_within_day(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202604", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
    ]))
    ec.fetch_indicator("722Y001", "M", 1, end_date=date(2026, 4, 25))
    ec.fetch_indicator("722Y001", "M", 1, end_date=date(2026, 4, 25))
    assert mock_get.call_count == 1


# ============================================================
# get_indicator_movement
# ============================================================

def test_get_indicator_movement_absolute_kind(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202603", "DATA_VALUE": "2.75", "UNIT_NAME": "%"},
        {"TIME": "202604", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
    ]))
    mv = ec.get_indicator_movement("base_rate", ref_date=date(2026, 4, 25))
    assert mv["latest"] == 2.50
    assert mv["prior"] == 2.75
    assert mv["change_abs"] == pytest.approx(-0.25)
    assert mv["kind"] == "absolute"


def test_get_indicator_movement_unknown_id(env_key, tmp_cache_path):
    assert ec.get_indicator_movement("unknown") is None


def test_get_indicator_movement_insufficient_data(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([]))
    assert ec.get_indicator_movement("base_rate") is None


# ============================================================
# enrich_event_with_ecos_context
# ============================================================

def test_enrich_with_explicit_indicator(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202603", "DATA_VALUE": "2.75", "UNIT_NAME": "%"},
        {"TIME": "202604", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
    ]))
    event = {"type": "MACRO", "event_date": "2026-05-28",
             "title": "한국은행 금융통화위원회",
             "body_snippet": "한은 금통위 본문.",
             "category_hints": ["fomc"], "_indicator": "base_rate"}
    ec.enrich_event_with_ecos_context(event)
    assert "ecos_context" in event
    assert event["ecos_context"]["indicator"] == "base_rate"
    assert "한은 기준금리" in event["body_snippet"]
    assert "-0.25pp" in event["body_snippet"]


def test_enrich_skips_when_no_indicator(env_key, tmp_cache_path, mock_get):
    """category_hints 만 있고 _indicator 명시 X → enrich skip (US/KR 혼동 방지)."""
    event = {"type": "MACRO", "event_date": "2026-05-13",
             "title": "미국 4월 소비자물가지수(CPI) 발표",
             "body_snippet": "원본",
             "category_hints": ["fomc", "fx"]}
    ec.enrich_event_with_ecos_context(event)
    assert "ecos_context" not in event
    assert event["body_snippet"] == "원본"


def test_enrich_skips_when_indicator_unknown(env_key, tmp_cache_path, mock_get):
    event = {"type": "MACRO", "event_date": "2026-05-14",
             "title": "통계청 고용동향 발표",
             "body_snippet": "원본",
             "category_hints": ["fomc"], "_indicator": "employment"}
    ec.enrich_event_with_ecos_context(event)
    assert "ecos_context" not in event
    assert event["body_snippet"] == "원본"


def test_enrich_idempotent(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202603", "DATA_VALUE": "2.75", "UNIT_NAME": "%"},
        {"TIME": "202604", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
    ]))
    event = {"type": "MACRO", "event_date": "2026-05-28",
             "title": "한국은행 금융통화위원회",
             "body_snippet": "한은 금통위 본문.",
             "_indicator": "base_rate"}
    ec.enrich_event_with_ecos_context(event)
    body_after_first = event["body_snippet"]
    ec.enrich_event_with_ecos_context(event)
    assert event["body_snippet"] == body_after_first


def test_enrich_skips_when_no_api_key(monkeypatch, tmp_cache_path):
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    monkeypatch.setattr(ec, "ENV_PATH", "/nonexistent")
    event = {"type": "MACRO", "event_date": "2026-05-28",
             "title": "한국은행 금융통화위원회",
             "body_snippet": "원본", "_indicator": "base_rate"}
    ec.enrich_event_with_ecos_context(event)
    assert "ecos_context" not in event
    assert event["body_snippet"] == "원본"


# ============================================================
# verify_category_with_ecos
# ============================================================

def _fx_rows(values):
    today = date.today()
    return [{"TIME": (today - timedelta(days=len(values) - 1 - i)).strftime("%Y%m%d"),
             "DATA_VALUE": str(v), "UNIT_NAME": "원"}
            for i, v in enumerate(values)]


def test_verify_fx_above_threshold(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search(_fx_rows([1380.0, 1390.0, 1395.0, 1400.0, 1401.0])))
    r = ec.verify_category_with_ecos("fx", date.today())
    assert r["verified"] is True
    assert r["movement_pct"] > 0.5


def test_verify_fx_below_threshold_marks_unverified(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search(_fx_rows([1380.0, 1380.5, 1381.0, 1380.7, 1381.0])))
    r = ec.verify_category_with_ecos("fx", date.today())
    assert r["verified"] is False
    assert abs(r["movement_pct"]) < 0.5


def test_verify_fomc_with_rate_change(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202602", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
        {"TIME": "202604", "DATA_VALUE": "2.75", "UNIT_NAME": "%"},
    ]))
    r = ec.verify_category_with_ecos("fomc", date.today())
    assert r["verified"] is True


def test_verify_fomc_flat_with_proximate_mpc(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202603", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
        {"TIME": "202604", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
    ]))
    fake_mpc = [{"event_date": (date.today() + timedelta(days=10)).isoformat(),
                 "title": "한국은행 금융통화위원회"}]
    with patch("bok_schedule.fetch_bok_mpc_schedule", return_value=fake_mpc):
        r = ec.verify_category_with_ecos("fomc", date.today())
    assert r["verified"] is True
    assert "임박" in r["reason"]


def test_verify_fomc_flat_no_proximate_mpc(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202603", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
        {"TIME": "202604", "DATA_VALUE": "2.50", "UNIT_NAME": "%"},
    ]))
    with patch("bok_schedule.fetch_bok_mpc_schedule", return_value=[]):
        r = ec.verify_category_with_ecos("fomc", date.today())
    assert r["verified"] is False


def test_verify_unknown_category_defaults_verified(env_key, tmp_cache_path):
    r = ec.verify_category_with_ecos("nuclear", date.today())
    assert r["verified"] is True


def test_verify_oil_above_threshold(env_key, tmp_cache_path, mock_get):
    """oil rule 활성화 후, |Δ| >= 2% 면 verified."""
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202602", "DATA_VALUE": "60.0", "UNIT_NAME": "달러/배럴"},
        {"TIME": "202603", "DATA_VALUE": "65.0", "UNIT_NAME": "달러/배럴"},  # +8.3%
    ]))
    r = ec.verify_category_with_ecos("oil", date.today())
    assert r["verified"] is True
    assert r["movement_pct"] > 2.0


def test_verify_oil_below_threshold(env_key, tmp_cache_path, mock_get):
    mock_get.return_value = _resp(_stat_search([
        {"TIME": "202602", "DATA_VALUE": "60.00", "UNIT_NAME": "달러/배럴"},
        {"TIME": "202603", "DATA_VALUE": "60.50", "UNIT_NAME": "달러/배럴"},  # +0.83%
    ]))
    r = ec.verify_category_with_ecos("oil", date.today())
    assert r["verified"] is False
    assert abs(r["movement_pct"]) < 2.0


def test_verify_returns_neutral_on_indicator_fetch_failure(env_key, tmp_cache_path, mock_get):
    mock_get.side_effect = ec.requests.RequestException("down")
    r = ec.verify_category_with_ecos("fx", date.today())
    assert r["verified"] is True  # 인프라 실패에 페널티 안 줌
