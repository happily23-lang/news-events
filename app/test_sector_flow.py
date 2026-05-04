"""sector_flow_page 단위 테스트."""
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

import sector_flow_page as sfp


# ---------------------------------------------------------------------------
# classify_intensity (5-stage)
# ---------------------------------------------------------------------------

def test_classify_strong_buy():
    _, level = sfp.classify_intensity(7.5)
    assert level == "strong_buy"


def test_classify_buy():
    _, level = sfp.classify_intensity(3.0)
    assert level == "buy"


def test_classify_neutral_at_zero():
    _, level = sfp.classify_intensity(0.0)
    assert level == "neutral"


def test_classify_sell():
    _, level = sfp.classify_intensity(-3.0)
    assert level == "sell"


def test_classify_strong_sell():
    _, level = sfp.classify_intensity(-7.0)
    assert level == "strong_sell"


@pytest.mark.parametrize("value,expected", [
    (5.0, "strong_buy"),
    (4.999, "buy"),
    (1.0, "buy"),
    (0.999, "neutral"),
    (-0.999, "neutral"),
    (-1.0, "sell"),
    (-4.999, "sell"),
    (-5.0, "strong_sell"),
])
def test_classify_boundaries(value, expected):
    _, level = sfp.classify_intensity(value)
    assert level == expected


# ---------------------------------------------------------------------------
# aggregate_sector_flows
# ---------------------------------------------------------------------------

def _supply(f_value, i_value, rows=None):
    return {
        "foreign_net_shares": 0,
        "institution_net_shares": 0,
        "foreign_net_value": f_value,
        "institution_net_value": i_value,
        "rows": rows or [],
    }


def test_aggregate_5d_sums_per_sector():
    supply = {
        "005930|2026-05-03|5": _supply(85_000_000_000, -10_000_000_000),
        "000660|2026-05-03|5": _supply(32_000_000_000, -8_000_000_000),
        "035720|2026-05-03|5": _supply(-5_000_000_000, 2_000_000_000),
    }
    sector_map = {
        "005930": {"sector": "전기·전자"},
        "000660": {"sector": "전기·전자"},
        "035720": {"sector": "서비스업"},
    }
    name_map = {"005930": "삼성전자", "000660": "SK하이닉스", "035720": "카카오"}
    flows = sfp.aggregate_sector_flows(supply, sector_map, name_map, window=5, as_of="2026-05-03")
    by_sector = {f["sector"]: f for f in flows}

    semi = by_sector["전기·전자"]
    assert semi["foreign_net_value"] == 85_000_000_000 + 32_000_000_000
    assert semi["institution_net_value"] == -18_000_000_000
    assert semi["combined_net_value"] == 99_000_000_000
    assert semi["stock_count"] == 2
    assert by_sector["서비스업"]["foreign_net_value"] == -5_000_000_000


def test_aggregate_top3_buy_and_sell():
    supply = {
        "001|2026-05-03|5": _supply(50_000_000_000, 10_000_000_000),
        "002|2026-05-03|5": _supply(30_000_000_000, 5_000_000_000),
        "003|2026-05-03|5": _supply(10_000_000_000, 0),
        "004|2026-05-03|5": _supply(-20_000_000_000, -10_000_000_000),
    }
    sector_map = {c: {"sector": "테스트"} for c in ["001", "002", "003", "004"]}
    name_map = {"001": "A", "002": "B", "003": "C", "004": "D"}
    flows = sfp.aggregate_sector_flows(supply, sector_map, name_map, window=5, as_of="2026-05-03")
    f = flows[0]
    assert [s["name"] for s in f["top_buy"]] == ["A", "B", "C"]
    assert f["top_sell"][0]["name"] == "D"


def test_aggregate_unmapped_goes_to_etc():
    supply = {"999|2026-05-03|5": _supply(5_000_000_000, 0)}
    flows = sfp.aggregate_sector_flows(supply, {}, {"999": "X"}, window=5, as_of="2026-05-03")
    assert flows[0]["sector"] == "기타"


def test_aggregate_filters_other_dates():
    supply = {
        "005930|2026-05-03|5": _supply(10_000_000_000, 0),
        "005930|2026-04-30|5": _supply(99_000_000_000, 0),  # ignored
    }
    sector_map = {"005930": {"sector": "전기·전자"}}
    flows = sfp.aggregate_sector_flows(supply, sector_map, {"005930": "삼성전자"},
                                        window=5, as_of="2026-05-03")
    assert flows[0]["foreign_net_value"] == 10_000_000_000


def test_aggregate_intensity_attached():
    supply = {"001|2026-05-03|5": _supply(20_000_000_000, 0)}
    flows = sfp.aggregate_sector_flows(
        supply, {"001": {"sector": "테스트"}}, {"001": "X"}, window=5, as_of="2026-05-03"
    )
    # 20억 / (1 stock × 100억) × 100 = 20% → strong_buy
    assert flows[0]["intensity_level"] == "strong_buy"


def test_aggregate_1d_uses_most_recent_row():
    rows = [
        {"date": "2026-05-03", "close": 100_000, "foreign_net": 1000, "institution_net": 500},
        {"date": "2026-05-02", "close": 99_000, "foreign_net": 9999, "institution_net": 9999},
    ]
    supply = {"001|2026-05-03|5": _supply(0, 0, rows=rows)}
    sector_map = {"001": {"sector": "테스트"}}
    flows = sfp.aggregate_sector_flows(supply, sector_map, {"001": "X"},
                                        window=1, as_of="2026-05-03")
    f = flows[0]
    assert f["foreign_net_value"] == 1000 * 100_000
    assert f["institution_net_value"] == 500 * 100_000


# ---------------------------------------------------------------------------
# group_events_by_sector
# ---------------------------------------------------------------------------

def test_group_events_via_stock_code():
    today = date(2026, 5, 4)
    events = [
        {
            "event_date": (today + timedelta(days=4)).isoformat(),
            "type": "DISCLOSURE",
            "stocks": [{"code": "005930", "name": "삼성전자"}],
            "title": "삼성전자 자기주식취득결정",
        },
        {
            "event_date": (today + timedelta(days=20)).isoformat(),  # outside +14d
            "type": "DISCLOSURE",
            "stocks": [{"code": "005930"}],
            "title": "out of range",
        },
    ]
    sector_map = {"005930": {"sector": "전기·전자"}}
    grouped = sfp.group_events_by_sector(events, sector_map, today=today)
    assert "전기·전자" in grouped
    assert len(grouped["전기·전자"]) == 1
    assert grouped["전기·전자"][0]["title"] == "삼성전자 자기주식취득결정"


def test_group_events_macro_with_sector_hint():
    today = date(2026, 5, 4)
    events = [{
        "event_date": (today + timedelta(days=2)).isoformat(),
        "type": "MACRO",
        "sector_hints": ["반도체"],
        "title": "반도체 진흥법 본회의",
    }]
    grouped = sfp.group_events_by_sector(events, {}, today=today)
    assert "반도체" in grouped


def test_group_events_unmappable_dropped():
    today = date(2026, 5, 4)
    events = [{
        "event_date": (today + timedelta(days=2)).isoformat(),
        "type": "MACRO",
        "title": "no sector hint",
    }]
    grouped = sfp.group_events_by_sector(events, {}, today=today)
    assert grouped == {}


def test_group_events_inferred_stocks_used_when_no_direct():
    today = date(2026, 5, 4)
    events = [{
        "event_date": (today + timedelta(days=3)).isoformat(),
        "type": "NEWS_FUTURE",
        "inferred_stocks": [{"code": "005930", "name": "삼성전자"}],
        "title": "AI 정책 본회의",
    }]
    sector_map = {"005930": {"sector": "전기·전자"}}
    grouped = sfp.group_events_by_sector(events, sector_map, today=today)
    assert "전기·전자" in grouped


# ---------------------------------------------------------------------------
# render_sector_flow_html (smoke)
# ---------------------------------------------------------------------------

def test_render_sector_flow_html_smoke():
    flows = {
        5: [{
            "sector": "전기·전자",
            "stock_count": 2,
            "foreign_net_value": 5_000_000_000_000,
            "institution_net_value": -1_000_000_000_000,
            "combined_net_value": 4_000_000_000_000,
            "intensity_pct": 20.0,
            "intensity_label": "🔥 강매수",
            "intensity_level": "strong_buy",
            "top_buy": [{"code": "005930", "name": "삼성전자",
                         "net_value": 3_500_000_000_000}],
            "top_sell": [],
            "sparkline": [100_000_000_000, 200_000_000_000, 300_000_000_000,
                          400_000_000_000, 500_000_000_000],
        }],
        1: [],
    }
    events_by_sector = {
        "전기·전자": [
            {"event_date": "2026-05-12", "title": "삼성 1Q 실적", "type": "DISCLOSURE"},
        ]
    }
    html = sfp.render_sector_flow_html(flows, events_by_sector, "2026-05-04")
    assert "<!DOCTYPE html>" in html
    assert "전기·전자" in html
    assert "🔥 강매수" in html
    assert "삼성전자" in html
    assert "삼성 1Q 실적" in html
    assert "window.__SECTOR_FLOW__" in html


# ---------------------------------------------------------------------------
# build_sector_flow_page (end-to-end via tmp files)
# ---------------------------------------------------------------------------

def test_build_sector_flow_page_writes_file(tmp_path):
    supply_path = tmp_path / "supply.json"
    sector_path = tmp_path / "sector.json"
    out_path = tmp_path / "out.html"

    supply_path.write_text(json.dumps({
        "005930|2026-05-03|5": _supply(50_000_000_000, 10_000_000_000),
    }))
    sector_path.write_text(json.dumps({
        "_meta": {"source": "test"},
        "005930": {"sector": "전기·전자"},
    }))

    html = sfp.build_sector_flow_page(
        out_path=out_path,
        supply_cache_path=supply_path,
        sector_cache_path=sector_path,
        name_map={"005930": "삼성전자"},
        calendar_events=[],
        as_of="2026-05-03",
    )
    assert out_path.exists()
    assert "전기·전자" in html
    assert "삼성전자" in html
