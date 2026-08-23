"""run_pages 정적 빌드 설정 테스트."""
from datetime import date

import run_pages


def test_calendar_window_subtitle_matches_year_end_window():
    assert run_pages._calendar_window_subtitle(date(2026, 8, 23)) == "연말까지 130일"


def test_supply_max_calls_reads_env(monkeypatch):
    monkeypatch.setenv("PAGES_SUPPLY_MAX_CALLS", "0")

    assert run_pages._supply_max_calls() == 0
