"""Sector money flow page — aggregation and HTML rendering.

Combines:
- naver_supply_cache.json: per-stock 5-day foreign/institution net value + rows
- krx_sector_cache.json:  code → KRX sector (Naver Finance 업종 분류)
- calendar events:         future catalysts (DART/BOK/policy)

Output: public/news_sector_flow.html

The "intensity" label converts an absolute net-value into a comparable
percentage so different sized sectors line up on the same scale:
  intensity_pct = combined_net_value / (stock_count × 100억) × 100
"""
import html as _html_lib
import json
from datetime import date, datetime, timedelta
from pathlib import Path


INTENSITY_LABELS = {
    "strong_buy":  ("🔥 강매수", "strong_buy"),
    "buy":         ("📈 매수",   "buy"),
    "neutral":     ("⚪ 중립",    "neutral"),
    "sell":        ("📉 매도",   "sell"),
    "strong_sell": ("❄ 강매도",  "strong_sell"),
}


def classify_intensity(intensity_pct: float) -> tuple[str, str]:
    """Return (display_label, level_id) for a 5-stage intensity classification.

    Boundary rules:
      intensity >= +5  → strong_buy
      +1 <= intensity < +5 → buy
      -1 < intensity < +1  → neutral
      -5 < intensity <= -1 → sell
      intensity <= -5 → strong_sell
    """
    if intensity_pct >= 5.0:
        return INTENSITY_LABELS["strong_buy"]
    if intensity_pct >= 1.0:
        return INTENSITY_LABELS["buy"]
    if intensity_pct > -1.0:
        return INTENSITY_LABELS["neutral"]
    if intensity_pct > -5.0:
        return INTENSITY_LABELS["sell"]
    return INTENSITY_LABELS["strong_sell"]


def aggregate_sector_flows(
    supply_cache: dict,
    sector_map: dict[str, dict],
    name_map: dict[str, str],
    window: int,
    as_of: str,
) -> list[dict]:
    """Group naver supply entries by sector and produce per-sector aggregates.

    Returns list of dicts sorted by combined_net_value descending.
    """
    suffix = f"|{as_of}|{window}" if window != 1 else f"|{as_of}|5"
    # window=1 reuses the 5-day cache and pulls only rows[0] (most recent day)
    expected_window_in_key = 5 if window == 1 else window
    suffix = f"|{as_of}|{expected_window_in_key}"

    relevant: list[tuple[str, dict]] = []
    for key, entry in supply_cache.items():
        if not isinstance(key, str) or not key.endswith(suffix):
            continue
        code = key.split("|", 1)[0]
        relevant.append((code, entry))

    by_sector: dict[str, list[dict]] = {}
    for code, entry in relevant:
        sector = (sector_map.get(code) or {}).get("sector") or "기타"
        if window == 1:
            rows = entry.get("rows") or []
            if not rows:
                continue
            # rows[] is newest-first per naver_supply.py scrape order
            r0 = rows[0]
            close = r0.get("close") or 0
            f_value = int((r0.get("foreign_net") or 0) * close)
            i_value = int((r0.get("institution_net") or 0) * close)
        else:
            f_value = int(entry.get("foreign_net_value") or 0)
            i_value = int(entry.get("institution_net_value") or 0)
        combined = f_value + i_value
        by_sector.setdefault(sector, []).append({
            "code": code,
            "name": name_map.get(code, code),
            "foreign_net_value": f_value,
            "institution_net_value": i_value,
            "net_value": combined,
            "rows": entry.get("rows") or [],
        })

    results: list[dict] = []
    for sector, stocks in by_sector.items():
        f_total = sum(s["foreign_net_value"] for s in stocks)
        i_total = sum(s["institution_net_value"] for s in stocks)
        combined = f_total + i_total
        count = len(stocks)
        intensity_pct = (combined / max(1, count * 10_000_000_000)) * 100
        label, level = classify_intensity(intensity_pct)

        sorted_stocks = sorted(stocks, key=lambda s: s["net_value"], reverse=True)
        top_buy = [
            {"code": s["code"], "name": s["name"], "net_value": s["net_value"]}
            for s in sorted_stocks[:3] if s["net_value"] > 0
        ]
        top_sell = [
            {"code": s["code"], "name": s["name"], "net_value": s["net_value"]}
            for s in reversed(sorted_stocks[-3:]) if s["net_value"] < 0
        ]
        sparkline = _build_sparkline(stocks)

        results.append({
            "sector": sector,
            "stock_count": count,
            "foreign_net_value": f_total,
            "institution_net_value": i_total,
            "combined_net_value": combined,
            "intensity_pct": round(intensity_pct, 2),
            "intensity_label": label,
            "intensity_level": level,
            "top_buy": top_buy,
            "top_sell": top_sell,
            "sparkline": sparkline,
        })

    results.sort(key=lambda r: r["combined_net_value"], reverse=True)
    return results


def _build_sparkline(stocks: list[dict]) -> list[int]:
    """Sum daily (foreign+institution net shares × close) across stocks.

    Returns list of daily totals oldest→newest.
    """
    by_date: dict[str, int] = {}
    for s in stocks:
        for row in s.get("rows", []):
            d = row.get("date")
            close = row.get("close") or 0
            f_net = row.get("foreign_net") or 0
            i_net = row.get("institution_net") or 0
            day_value = (f_net + i_net) * close
            if d:
                by_date[d] = by_date.get(d, 0) + int(day_value)
    return [by_date[d] for d in sorted(by_date.keys())]


def group_events_by_sector(
    events: list[dict],
    sector_map: dict[str, dict],
    today: date | None = None,
    days_ahead: int = 14,
) -> dict[str, list[dict]]:
    """Map calendar events to sectors. {sector: [event,...]}.

    Mapping rules (V1, conservative):
      1. event.stocks[].code → sector_map → sector
      2. else event.inferred_stocks[].code → sector_map → sector
      3. else event.sector_hints (list of sector names) → directly
      4. else drop (do not assign anywhere)
    Filter: today <= event_date <= today + days_ahead.
    """
    today = today or date.today()
    cutoff = today + timedelta(days=days_ahead)
    grouped: dict[str, list[dict]] = {}

    for ev in events:
        ed_raw = ev.get("event_date")
        if not ed_raw:
            continue
        try:
            ed = date.fromisoformat(str(ed_raw)[:10])
        except (ValueError, TypeError):
            continue
        if ed < today or ed > cutoff:
            continue

        sectors: set[str] = set()
        for key in ("stocks", "inferred_stocks"):
            for s in (ev.get(key) or []):
                code = s.get("code") if isinstance(s, dict) else None
                if not code:
                    continue
                sec = (sector_map.get(code) or {}).get("sector")
                if sec:
                    sectors.add(sec)
            if sectors:
                break

        if not sectors:
            for hint in (ev.get("sector_hints") or []):
                if hint:
                    sectors.add(hint)

        for sec in sectors:
            grouped.setdefault(sec, []).append(ev)

    for sec in grouped:
        grouped[sec].sort(key=lambda e: e.get("event_date") or "")
    return grouped


def _format_eok(value: int) -> str:
    """Format won amount as 억 with sign. e.g., 850000000000 → '+8,500억'."""
    eok = value / 100_000_000
    if eok > 0:
        return f"+{eok:,.0f}억"
    if eok < 0:
        return f"−{abs(eok):,.0f}억"
    return "0억"


def _sparkline_svg(points: list[int], width: int = 140, height: int = 28) -> str:
    if not points or len(points) < 2:
        return ""
    lo = min(points)
    hi = max(points)
    rng = hi - lo or 1
    step = width / (len(points) - 1)
    coords = []
    for i, v in enumerate(points):
        x = i * step
        y = height - ((v - lo) / rng * height)
        coords.append(f"{x:.1f},{y:.1f}")
    pts = " ".join(coords)
    return (
        f'<svg class="sparkline" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
        f'<polyline fill="none" stroke="#888" stroke-width="1.5" points="{pts}"/>'
        f"</svg>"
    )


def _render_event_line(ev: dict) -> str:
    d = _html_lib.escape(str(ev.get("event_date") or "")[:10])
    title = _html_lib.escape(str(ev.get("title") or ""))
    src = ev.get("type") or ""
    src_label = {
        "DISCLOSURE": "DART",
        "MACRO": "BOK",
        "NEWS_FUTURE": "정책",
    }.get(src, src or "")
    return (
        f'<div class="cat-row">'
        f'<span class="cat-date">{d}</span>'
        f'<span class="cat-title">{title}</span>'
        f'<span class="cat-src">{_html_lib.escape(src_label)}</span>'
        f"</div>"
    )


def _render_card(flow: dict, events: list[dict]) -> str:
    sector = _html_lib.escape(flow["sector"])
    label = _html_lib.escape(flow["intensity_label"])
    level = flow["intensity_level"]
    f_str = _format_eok(flow["foreign_net_value"])
    i_str = _format_eok(flow["institution_net_value"])
    combined_str = _format_eok(flow["combined_net_value"])
    bar_pct = max(0.0, min(100.0, abs(flow["intensity_pct"]) * 5))
    bar_class = "bar-up" if flow["combined_net_value"] >= 0 else "bar-down"

    spark = _sparkline_svg(flow.get("sparkline") or [])

    cat_html = ""
    if events:
        head = f'<div class="cat-head">📅 다가올 카탈리스트 (+14일, {len(events)}건)</div>'
        rows = "".join(_render_event_line(e) for e in events[:3])
        more = ""
        if len(events) > 3:
            more = (
                f'<div class="cat-more">'
                f'<a href="news_calendar.html">외 {len(events) - 3}건 더 →</a>'
                f"</div>"
            )
        cat_html = f'<div class="cat-block">{head}{rows}{more}</div>'

    def _stock_rows(items: list[dict]) -> str:
        return "".join(
            f'<div class="stock-row">'
            f'<span class="stock-name">{_html_lib.escape(s["name"])}</span>'
            f'<span class="stock-val">{_format_eok(s["net_value"])}</span>'
            f"</div>"
            for s in items
        )

    buy_html = ""
    if flow.get("top_buy"):
        buy_html = (
            f'<div class="stock-block">'
            f'<div class="stock-head">📈 매수 TOP{len(flow["top_buy"])}</div>'
            f'{_stock_rows(flow["top_buy"])}'
            f"</div>"
        )
    sell_html = ""
    if flow.get("top_sell"):
        sell_html = (
            f'<div class="stock-block">'
            f'<div class="stock-head">📉 매도 TOP{len(flow["top_sell"])}</div>'
            f'{_stock_rows(flow["top_sell"])}'
            f"</div>"
        )

    return (
        f'<article class="sector-card sector-{level}">'
        f'<div class="sector-head">'
        f'<h3 class="sector-name">{sector}</h3>'
        f'<span class="sector-label sector-label-{level}">{label}</span>'
        f"</div>"
        f'<div class="sector-flow">'
        f'<span class="flow-foreign">외인 {_html_lib.escape(f_str)}</span>'
        f'<span class="flow-sep">·</span>'
        f'<span class="flow-institution">기관 {_html_lib.escape(i_str)}</span>'
        f'<span class="flow-count">· {flow["stock_count"]}종목</span>'
        f"</div>"
        f'<div class="flow-bar">'
        f'<div class="{bar_class}" style="width:{bar_pct:.0f}%"></div>'
        f'<span class="flow-bar-val">{_html_lib.escape(combined_str)}</span>'
        f"</div>"
        f"{spark}"
        f"{cat_html}"
        f'<div class="stocks-grid">{buy_html}{sell_html}</div>'
        f"</article>"
    )


def render_sector_flow_html(
    flows_by_window: dict[int, list[dict]],
    events_by_sector: dict[str, list[dict]],
    as_of: str,
) -> str:
    """Render the dashboard. flows_by_window keys = 1, 5."""
    cards_5 = "\n".join(
        _render_card(f, events_by_sector.get(f["sector"], []))
        for f in flows_by_window.get(5, [])
    )
    cards_1 = "\n".join(
        _render_card(f, events_by_sector.get(f["sector"], []))
        for f in flows_by_window.get(1, [])
    )
    embedded = json.dumps(
        {"as_of": as_of, "sector_count_5": len(flows_by_window.get(5, [])),
         "sector_count_1": len(flows_by_window.get(1, []))},
        ensure_ascii=False,
    )

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>섹터 자금흐름 — {as_of}</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect x='10' y='30' width='18' height='50' fill='%230071e3'/><rect x='34' y='15' width='18' height='65' fill='%23e74c3c'/><rect x='58' y='45' width='18' height='35' fill='%230080ff'/></svg>">
<style>
  :root {{
    --bg:#f5f5f7; --card:#fff; --border:#e5e5ea;
    --text:#1d1d1f; --muted:#86868b; --accent:#0071e3;
    --up:#e74c3c; --up-soft:#fdecea;
    --down:#0080ff; --down-soft:#e8f2ff;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    font-family:-apple-system,'Apple SD Gothic Neo','Pretendard','Segoe UI',sans-serif;
    background:var(--bg); color:var(--text); margin:0;
    padding:48px 20px; font-size:15px; line-height:1.55;
  }}
  .container {{ max-width:1200px; margin:0 auto; }}
  .page-header {{ margin-bottom:20px; padding-bottom:16px; border-bottom:1px solid var(--border); }}
  .page-header h1 {{ font-size:28px; font-weight:700; margin:0 0 6px; letter-spacing:-0.02em; }}
  .page-meta {{ color:var(--muted); font-size:13px; }}
  .toolbar {{ display:flex; gap:8px; flex-wrap:wrap; margin:14px 0 22px; }}
  .toolbar button {{
    padding:6px 14px; border-radius:999px; border:1px solid var(--border);
    background:#fff; cursor:pointer; font-size:13px; color:var(--muted);
  }}
  .toolbar button.active {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
  .grid {{
    display:grid; grid-template-columns:repeat(auto-fill, minmax(340px, 1fr)); gap:14px;
  }}
  .grid-deck {{ display:grid; }}
  .grid-deck.hidden {{ display:none; }}
  .sector-card {{
    background:var(--card); border:1px solid var(--border); border-radius:12px;
    padding:16px 16px 14px;
  }}
  .sector-card.sector-strong_buy {{ border-color:#f8b8b1; }}
  .sector-card.sector-strong_sell {{ border-color:#a3c4ff; }}
  .sector-head {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:6px; }}
  .sector-name {{ margin:0; font-size:17px; font-weight:700; letter-spacing:-0.01em; }}
  .sector-label {{ font-size:12px; padding:2px 8px; border-radius:999px; white-space:nowrap; }}
  .sector-label-strong_buy {{ background:#fde2e0; color:#c0392b; }}
  .sector-label-buy {{ background:var(--up-soft); color:var(--up); }}
  .sector-label-neutral {{ background:#eee; color:#555; }}
  .sector-label-sell {{ background:var(--down-soft); color:var(--down); }}
  .sector-label-strong_sell {{ background:#cfe1ff; color:#003c80; }}
  .sector-flow {{ font-size:12px; color:var(--muted); margin-bottom:8px; }}
  .flow-sep, .flow-count {{ margin:0 4px; }}
  .flow-bar {{
    position:relative; height:18px; background:#f0f0f4;
    border-radius:6px; overflow:hidden; margin-bottom:6px;
  }}
  .flow-bar .bar-up {{ background:var(--up); height:100%; }}
  .flow-bar .bar-down {{ background:var(--down); height:100%; }}
  .flow-bar-val {{
    position:absolute; right:8px; top:0; font-size:12px; line-height:18px;
    color:#fff; font-weight:600; mix-blend-mode:difference;
  }}
  .sparkline {{ display:block; margin:4px 0 6px; }}
  .cat-block {{
    margin:10px 0 8px; padding:10px 12px; background:#fff8e1;
    border-radius:8px; font-size:12.5px;
  }}
  .cat-head {{ font-weight:600; margin-bottom:4px; color:#7a5b00; }}
  .cat-row {{ display:flex; gap:8px; padding:1px 0; align-items:baseline; }}
  .cat-date {{ color:var(--muted); min-width:60px; font-variant-numeric:tabular-nums; }}
  .cat-title {{ flex:1; }}
  .cat-src {{ font-size:10px; color:var(--muted); padding:1px 5px; border:1px solid #e0d4a8; border-radius:4px; }}
  .cat-more {{ margin-top:4px; }}
  .cat-more a {{ color:var(--accent); text-decoration:none; font-size:11.5px; }}
  .stocks-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px; }}
  .stock-block {{ font-size:12.5px; }}
  .stock-head {{ font-weight:600; margin-bottom:3px; }}
  .stock-row {{ display:flex; justify-content:space-between; padding:1px 0; }}
  .stock-name {{ color:var(--text); }}
  .stock-val {{ font-variant-numeric:tabular-nums; color:var(--muted); }}
  .empty {{
    text-align:center; padding:60px 20px; color:var(--muted);
    background:var(--card); border:1px dashed var(--border); border-radius:12px;
  }}
  @media (max-width:640px) {{
    body {{ padding:24px 12px; }}
    .grid {{ grid-template-columns:1fr; }}
    .stocks-grid {{ grid-template-columns:1fr; }}
    .page-header h1 {{ font-size:24px; }}
  }}
</style></head>
<body>
<div class="container">
  <header class="page-header">
    <h1>섹터 자금흐름</h1>
    <div class="page-meta">데이터 기준일: {_html_lib.escape(as_of)} · 외인 + 기관 합산 · 다가올 카탈리스트 +14일</div>
  </header>
  <div class="toolbar">
    <button class="active" data-window="5">5일 누적</button>
    <button data-window="1">1일</button>
  </div>
  <div id="grid-5" class="grid grid-deck">{cards_5 or '<div class="empty">5일 데이터가 없습니다.</div>'}</div>
  <div id="grid-1" class="grid grid-deck hidden">{cards_1 or '<div class="empty">1일 데이터가 없습니다.</div>'}</div>
</div>
<script>
window.__SECTOR_FLOW__ = {embedded};
document.querySelectorAll('.toolbar button').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.toolbar button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const w = btn.dataset.window;
    document.getElementById('grid-5').classList.toggle('hidden', w !== '5');
    document.getElementById('grid-1').classList.toggle('hidden', w !== '1');
  }});
}});
</script>
</body></html>
"""


def _latest_as_of(supply_cache: dict) -> str:
    dates = set()
    for k in supply_cache.keys():
        if isinstance(k, str) and k.count("|") == 2:
            dates.add(k.split("|")[1])
    return max(dates) if dates else date.today().isoformat()


def build_sector_flow_page(
    out_path: Path | str,
    supply_cache_path: Path | str,
    sector_cache_path: Path | str,
    name_map: dict[str, str] | None = None,
    calendar_events: list[dict] | None = None,
    as_of: str | None = None,
) -> str:
    """End-to-end build: load caches, aggregate, render. Writes file. Returns html string."""
    supply = json.loads(Path(supply_cache_path).read_text())
    if Path(sector_cache_path).exists():
        sector_raw = json.loads(Path(sector_cache_path).read_text())
    else:
        sector_raw = {}
    sector_map = {k: v for k, v in sector_raw.items() if not k.startswith("_")}
    name_map = name_map or {}
    as_of = as_of or _latest_as_of(supply)

    flows_5 = aggregate_sector_flows(supply, sector_map, name_map, window=5, as_of=as_of)
    flows_1 = aggregate_sector_flows(supply, sector_map, name_map, window=1, as_of=as_of)
    events_by_sector = group_events_by_sector(calendar_events or [], sector_map)

    html = render_sector_flow_html(
        flows_by_window={1: flows_1, 5: flows_5},
        events_by_sector=events_by_sector,
        as_of=as_of,
    )
    Path(out_path).write_text(html, encoding="utf-8")
    return html
