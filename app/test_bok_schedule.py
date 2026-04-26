"""bok_schedule 단위 테스트. mock 으로 requests.get 가로채 외부 호출 없이 검증."""

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

import bok_schedule as bs


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_cache_path(monkeypatch, tmp_path):
    cache_file = tmp_path / "bok_schedule_cache.json"
    monkeypatch.setattr(bs, "CACHE_PATH", str(cache_file))
    return cache_file


@pytest.fixture
def mock_get():
    with patch.object(bs.requests, "get") as m:
        yield m


def _resp(text: str, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


# ============================================================
# canonical_title
# ============================================================

def test_canonical_bok_mpc():
    assert bs._canonical_title("한국은행 금융통화위원회") == "bok_mpc"
    assert bs._canonical_title("한은 금통위 5월 회의") == "bok_mpc"


def test_canonical_fed_fomc():
    assert bs._canonical_title("Fed FOMC 기준금리 결정") == "fed_fomc"
    assert bs._canonical_title("FOMC 회의록 공개") == "fed_fomc"


def test_canonical_distinguishes_us_kr_cpi():
    assert bs._canonical_title("미국 4월 소비자물가지수(CPI) 발표") == "us_cpi"
    assert bs._canonical_title("통계청 소비자물가동향 발표") == "kr_cpi"


def test_canonical_employment_ip_bop():
    assert bs._canonical_title("통계청 고용동향 발표") == "employment"
    assert bs._canonical_title("산업활동동향 공표") == "ip"
    assert bs._canonical_title("국제수지 발표") == "bop"


def test_canonical_unknown_passes_through():
    out = bs._canonical_title("기타 거시 이벤트 ABC")
    assert out and out != "bok_mpc" and out != "fed_fomc"


# ============================================================
# _detect_page_year + _parse_bok_mpc_html
# ============================================================

_BOK_HTML_2026 = """
<html><body>
  <h1>2026년 통화정책방향 결정회의</h1>
  <p>2026년 회의 일정은 다음과 같습니다.</p>
  <table>
    <tr><th>회의일자</th><th>결정문</th></tr>
    <tr><td>01월 15일(목)</td><td>국문보도자료(2601).hwp</td></tr>
    <tr><td>02월 26일(목)</td><td>국문보도자료(2602).hwp</td></tr>
    <tr><td>04월 10일(금)</td><td>국문보도자료(2604).hwp</td></tr>
    <tr><td>05월 28일(목)</td><td>국문보도자료(2605).hwp</td></tr>
  </table>
</body></html>
"""


def test_detect_page_year():
    assert bs._detect_page_year(_BOK_HTML_2026) == 2026


def test_parse_bok_mpc_html_extracts_dates():
    events = bs._parse_bok_mpc_html(_BOK_HTML_2026, year=2026)
    assert len(events) == 4
    dates = [e["event_date"] for e in events]
    assert dates == sorted(dates)
    assert "2026-01-15" in dates
    assert "2026-05-28" in dates
    for e in events:
        assert e["type"] == "MACRO"
        assert e["category_hints"] == ["fomc"]
        assert e["_indicator"] == "base_rate"
        assert e["_origin"] == "scraper"


def test_parse_bok_mpc_html_year_mismatch_returns_empty():
    """페이지가 2026 을 보여주는데 year=2025 요청하면 [] (잘못된 연도 부착 방지)."""
    events = bs._parse_bok_mpc_html(_BOK_HTML_2026, year=2025)
    assert events == []


def test_parse_bok_mpc_html_skips_header_row():
    events = bs._parse_bok_mpc_html(_BOK_HTML_2026, year=2026)
    titles = [e["title"] for e in events]
    assert all(t == "한국은행 금융통화위원회" for t in titles)


# ============================================================
# fetch_bok_mpc_schedule (with mocked HTTP)
# ============================================================

def test_fetch_bok_mpc_uses_cache_within_ttl(tmp_cache_path, mock_get):
    """캐시가 TTL 내면 HTTP 호출 안 함."""
    fresh_iso = date.today().isoformat()
    bs._save_cache({
        "version": 1,
        "by_source": {
            "bok_mpc_2026": {
                "fetched_at": fresh_iso,
                "events": [{"type": "MACRO", "event_date": "2026-05-28",
                            "title": "한국은행 금융통화위원회",
                            "category_hints": ["fomc"], "_origin": "scraper",
                            "_indicator": "base_rate"}],
            }
        }
    })
    out = bs.fetch_bok_mpc_schedule(2026)
    assert len(out) == 1
    assert out[0]["event_date"] == "2026-05-28"
    mock_get.assert_not_called()


def test_fetch_bok_mpc_force_refresh_bypasses_cache(tmp_cache_path, mock_get):
    bs._save_cache({"version": 1, "by_source": {
        "bok_mpc_2026": {"fetched_at": date.today().isoformat(), "events": []}
    }})
    mock_get.return_value = _resp(_BOK_HTML_2026)
    out = bs.fetch_bok_mpc_schedule(2026, force_refresh=True)
    assert len(out) == 4
    mock_get.assert_called_once()


def test_fetch_bok_mpc_falls_back_to_cache_on_http_failure(tmp_cache_path, mock_get):
    """fetched_at 이 stale 이라도 HTTP 실패 시 캐시 events 반환."""
    stale_iso = (date.today() - timedelta(days=60)).isoformat()
    bs._save_cache({"version": 1, "by_source": {
        "bok_mpc_2026": {"fetched_at": stale_iso, "events": [
            {"type": "MACRO", "event_date": "2026-05-28",
             "title": "한국은행 금융통화위원회"}]}
    }})
    mock_get.side_effect = bs.requests.RequestException("network down")
    out = bs.fetch_bok_mpc_schedule(2026)
    assert len(out) == 1


def test_fetch_bok_mpc_returns_empty_on_zero_parse_no_cache(tmp_cache_path, mock_get):
    mock_get.return_value = _resp("<html><body>no tables</body></html>")
    assert bs.fetch_bok_mpc_schedule(2026) == []


# ============================================================
# fetch_kostat_release_schedule
# ============================================================

def test_kostat_monthly_pattern_per_year(tmp_cache_path):
    events = bs.fetch_kostat_release_schedule(2026, force_refresh=True)
    # 12 months × 3 categories = 36
    assert len(events) == 36
    indicators = {e["_indicator"] for e in events}
    assert indicators == {"cpi", "employment", "ip"}
    cpi_events = [e for e in events if e["_indicator"] == "cpi"]
    assert all("fomc" in e["category_hints"] and "fx" in e["category_hints"]
               for e in cpi_events)


# ============================================================
# get_macro_events orchestration
# ============================================================

def test_get_macro_events_window_filter(tmp_cache_path):
    """window=10일 이면 그 안에 있는 이벤트만."""
    today = date(2026, 5, 26)
    events = bs.get_macro_events(today=today, window_days=10)
    for e in events:
        assert today.isoformat() <= e["event_date"] <= (today + timedelta(days=10)).isoformat()


def test_get_macro_events_dedupe_prefers_scraped_over_hardcoded(
        tmp_cache_path, mock_get, monkeypatch):
    """scraper 가 한은 금통위 5/28 을 채우면, 같은 canonical 의 hardcoded 한은 금통위는 제외.

    실제 _MACRO_EVENTS_2026 에는 한은 금통위가 빠져있어 (scraper 권위 정책), 이 테스트는
    임시로 hardcoded 한은 금통위 5/22 추정값을 주입해 dedupe 메커니즘을 직접 검증.
    """
    import calendar_page
    fake_hardcoded = list(calendar_page._MACRO_EVENTS_2026) + [
        {"date": "2026-05-22", "title": "한국은행 금융통화위원회 (잘못된 추정)",
         "body": "fake hardcoded entry", "category_hints": ["fomc"], "direction": "neutral"}
    ]
    monkeypatch.setattr(calendar_page, "_MACRO_EVENTS_2026", fake_hardcoded)

    mock_get.return_value = _resp(_BOK_HTML_2026)
    events = bs.get_macro_events(today=date(2026, 4, 25), window_days=60)
    titles = [e["title"] for e in events]
    bok_mpc_count = sum(1 for t in titles if "한국은행 금융통화위원회" in t)
    # scraper 의 5/28 한 건만, hardcoded 의 5/22 (같은 canonical) 은 제거됐어야
    assert bok_mpc_count == 1
    bok_dates = [e["event_date"] for e in events if "한국은행 금융통화위원회" in e["title"]]
    assert "2026-05-22" not in bok_dates
    assert "2026-05-28" in bok_dates


def test_get_macro_events_falls_back_to_hardcoded_when_scraper_fails(
        tmp_cache_path, mock_get):
    """scraper 가 [] 반환하면 hardcoded fallback (Fed FOMC, 미국 CPI) 만 노출.

    한은 금통위는 hardcoded 에 의도적으로 미등록 (잘못된 추정 날짜 노출 방지).
    scraper 실패 시 한은 금통위는 비어있는 게 정책.
    """
    mock_get.return_value = _resp("<html></html>")  # 빈 페이지 → parse 0건
    events = bs.get_macro_events(today=date(2026, 4, 25), window_days=60)
    titles = [e["title"] for e in events]
    assert any("Fed FOMC" in t for t in titles)
    assert any("미국" in t and "CPI" in t for t in titles)
    # 한은 금통위는 scraper 실패 시 등장하지 않음
    assert not any("한국은행 금융통화위원회" in t for t in titles)


def test_get_macro_events_distinguishes_us_kr_cpi(tmp_cache_path, mock_get):
    """미국 CPI(hardcoded) 와 통계청 CPI(KOSTAT pattern) 가 둘 다 살아남아야."""
    mock_get.return_value = _resp(_BOK_HTML_2026)
    events = bs.get_macro_events(today=date(2026, 4, 25), window_days=20)
    cpi_titles = {e["title"] for e in events if "물가" in e["title"] or "CPI" in e["title"]}
    assert any("미국" in t for t in cpi_titles)
    assert any("통계청" in t for t in cpi_titles)


def test_get_macro_events_kostat_indicator_attached(tmp_cache_path):
    events = bs.get_macro_events(today=date(2026, 5, 1), window_days=15)
    cpi = [e for e in events if e.get("_indicator") == "cpi"]
    assert len(cpi) >= 1
    assert all(e["type"] == "MACRO" for e in cpi)
