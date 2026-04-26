"""
정책·외부 이벤트 수혜 종목 대시보드 모듈.

파이프라인:
  1. 네이버 경제 뉴스 여러 섹션 크롤링
  2. 카테고리 키워드로 이벤트 탐지 (뉴스 → 카테고리 매핑)
  3. 매칭 기사 본문에서 KRX 종목 직접 추출  → 📌 direct_stocks
  4. 카테고리의 theme_hints 로 네이버 테마 해석 후 주도주 → 🔗 inferred_stocks
  5. 매칭된 카테고리만 카드 렌더
"""

import json
import os
import re
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup


KRX_CACHE_PATH = "/tmp/krx_listing_full.json"
THEME_INDEX_CACHE_PATH = "/tmp/naver_theme_index.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

EXCLUDE_NAME_PATTERNS = ['스팩', 'SPAC', '리츠', 'REIT', '1호', '2호', '3호', '4호', '5호']


# ============================================================
# KRX 캐시 / 종목 매칭
# ============================================================

def load_krx_listings(refresh: bool = False) -> list[dict]:
    if not refresh and os.path.exists(KRX_CACHE_PATH):
        with open(KRX_CACHE_PATH) as f:
            return json.load(f)
    import FinanceDataReader as fdr
    df = fdr.StockListing('KRX')
    cols = ['Code', 'Name', 'Market', 'Close', 'Changes', 'ChagesRatio']
    stocks = df[cols].fillna(0).to_dict('records')
    stocks = [s for s in stocks if s.get('Name') and isinstance(s['Name'], str)]
    with open(KRX_CACHE_PATH, 'w') as f:
        json.dump(stocks, f, ensure_ascii=False)
    return stocks


def build_name_map(stocks: list[dict]) -> dict[str, dict]:
    name_map: dict[str, dict] = {}
    for s in stocks:
        name = s['Name'].strip()
        if len(name) < 2:
            continue
        if name.endswith(('우', '우B', '우C')) and len(name) >= 3:
            continue
        if any(x in name for x in EXCLUDE_NAME_PATTERNS):
            continue
        name_map[name] = s
    return name_map


def build_code_map(stocks: list[dict]) -> dict[str, dict]:
    return {s['Code']: s for s in stocks}


def count_mentions(texts: list[str], target_names: set[str]) -> dict[str, int]:
    counter = {n: 0 for n in target_names}
    for text in texts:
        if not text:
            continue
        for name in target_names:
            if len(name) == 2:
                for m in re.finditer(re.escape(name), text):
                    s, e = m.start(), m.end()
                    before = text[s - 1] if s > 0 else ' '
                    after = text[e] if e < len(text) else ' '
                    if not ('가' <= before <= '힣' or '가' <= after <= '힣'):
                        counter[name] += 1
            else:
                counter[name] += text.count(name)
    return counter


_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?])\s+|(?<=[다요함음됨])\s|[\n\r]+')


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _anchor_window(text: str, anchors: list[str], neighbor: int = 1) -> str:
    """키워드(anchor)가 포함된 문장 + 양옆 neighbor개만 모아서 반환."""
    if not anchors:
        return text
    sentences = _split_sentences(text)
    if not sentences:
        return ""
    picked: set[int] = set()
    for i, s in enumerate(sentences):
        if any(a in s for a in anchors):
            for k in range(max(0, i - neighbor), min(len(sentences), i + neighbor + 1)):
                picked.add(k)
    if not picked:
        return ""
    return " ".join(sentences[i] for i in sorted(picked))


# 종목 매칭 단계에서 무시해야 하는 신문사 보일러플레이트 패턴.
# 예: "제보는 카카오톡 '연합뉴스'..." 가 본문에 들어가면 KRX '카카오'가 오탐됨.
# 매칭 직전에만 mask 처리 (본문 표시에는 영향 X).
_BOILERPLATE_PATTERNS = [
    re.compile(r"제보는\s*카카오톡[^\n]*"),
    re.compile(r"카카오톡\s*채널[^\n]*"),
    re.compile(r"카카오톡으로[^\n]*"),
    re.compile(r"카카오톡에서[^\n]*"),
    re.compile(r"카카오톡\s*친구[^\n]*"),
]


def _strip_press_boilerplate(text: str) -> str:
    """매칭 직전 신문사 보일러플레이트(특히 카카오톡 제보 안내) 제거."""
    for pat in _BOILERPLATE_PATTERNS:
        text = pat.sub(" ", text)
    return text


def _is_word_char(ch: str) -> bool:
    """한글(가-힣)·영문 알파벳·숫자면 단어 글자로 간주.

    종목명 매칭 시 "엠브레인퍼블릭" 안에서 "엠브레인" 만 잘라 매칭하는 부분 일치 방지.
    """
    if "가" <= ch <= "힣":
        return True
    o = ord(ch)
    if 0x41 <= o <= 0x5A or 0x61 <= o <= 0x7A:  # A-Z, a-z
        return True
    if 0x30 <= o <= 0x39:  # 0-9
        return True
    return False


def _has_word_boundary(text: str, idx: int, nlen: int) -> bool:
    """text[idx:idx+nlen] 이 단어 경계로 둘러싸여 있는지.

    좌우 인접 글자가 한글/영문/숫자면 부분 단어 매칭으로 보고 거부.
    예: '엠브레인퍼블릭' 의 '엠브레인' 매칭은 우측이 '퍼' → 거부.
    """
    left_ok = idx == 0 or not _is_word_char(text[idx - 1])
    right_end = idx + nlen
    right_ok = right_end == len(text) or not _is_word_char(text[right_end])
    return left_ok and right_ok


def find_direct_stocks_in_text(
    text: str,
    name_map: dict[str, dict],
    limit: int = 8,
    min_len: int = 3,
    anchors: Optional[list[str]] = None,
) -> list[dict]:
    """
    본문에서 KRX 종목명을 추출.
    - anchors: 키워드 리스트. 제공되면 키워드가 포함된 문장(±1) 내 종목만 채택
    - min_len: 이 길이 미만의 종목명은 스킵 (기본 3 — 2글자 오탐 제거)
    """
    if not text:
        return []

    text = _strip_press_boilerplate(text)

    if anchors:
        text = _anchor_window(text, anchors, neighbor=1)
        if not text:
            return []

    sorted_names = sorted(
        (n for n in name_map.keys() if len(n) >= min_len),
        key=len,
        reverse=True,
    )
    mask = [False] * len(text)
    hits: list[tuple[int, dict]] = []
    for name in sorted_names:
        start = 0
        nlen = len(name)
        while True:
            idx = text.find(name, start)
            if idx < 0:
                break
            end = idx + nlen
            # 단어 경계 검증: '엠브레인퍼블릭' 안의 '엠브레인' 같은 부분 매칭 거부
            if not _has_word_boundary(text, idx, nlen):
                start = idx + 1
                continue
            if not any(mask[idx:end]):
                hits.append((idx, name_map[name]))
                for i in range(idx, end):
                    mask[i] = True
            start = idx + 1
    hits.sort(key=lambda x: x[0])
    seen_codes: set[str] = set()
    result: list[dict] = []
    for _, s in hits:
        if s['Code'] in seen_codes:
            continue
        seen_codes.add(s['Code'])
        result.append(s)
        if len(result) >= limit:
            break
    return result


# ============================================================
# 네이버 테마 인덱스 (이름 ↔ no)
# ============================================================

def fetch_naver_theme_index(refresh: bool = False, pages: int = 8) -> dict[str, dict]:
    """
    네이버 테마 전체 목록을 이름 → {"no", "change_pct"} dict 로 반환.
    페이지 1..pages 순회. 당일 캐시.
    """
    if not refresh and os.path.exists(THEME_INDEX_CACHE_PATH):
        mtime = datetime.fromtimestamp(os.path.getmtime(THEME_INDEX_CACHE_PATH)).date()
        if mtime == date.today():
            with open(THEME_INDEX_CACHE_PATH) as f:
                return json.load(f)

    index: dict[str, dict] = {}
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/sise/theme.naver?page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
        except requests.RequestException:
            continue
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.find("table", class_="type_1 theme")
        if table is None:
            continue
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            a = tds[0].find("a")
            if a is None:
                continue
            name = a.get_text(strip=True)
            if not name:
                continue
            href = a.get("href", "")
            m = re.search(r"no=(\d+)", href)
            if not m:
                continue
            try:
                change_txt = tds[1].get_text(strip=True).replace("%", "").replace("+", "")
                change_pct = float(change_txt)
            except ValueError:
                change_pct = 0.0
            index[name] = {"no": m.group(1), "change_pct": change_pct}

    with open(THEME_INDEX_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    return index


def resolve_category_themes(category: dict, theme_index: dict[str, dict]) -> list[dict]:
    """
    카테고리의 theme_hints 가 포함된 네이버 테마 이름을 모두 수집.
    대소문자/공백 무시 부분 매칭.
    """
    hints = [h.lower().replace(" ", "") for h in category.get("theme_hints", [])]
    if not hints:
        return []
    matched: list[dict] = []
    seen: set[str] = set()
    for theme_name, info in theme_index.items():
        norm = theme_name.lower().replace(" ", "")
        for h in hints:
            if h and h in norm:
                if info["no"] not in seen:
                    seen.add(info["no"])
                    matched.append({"name": theme_name, "no": info["no"],
                                    "change_pct": info["change_pct"]})
                break
    return matched


def _fetch_theme_constituents(theme_no: str, limit: int = 4) -> list[dict]:
    """테마 상세 페이지에서 구성종목 (등락률 내림차순 상위 limit)."""
    url = f"https://finance.naver.com/sise/sise_group_detail.naver?type=theme&no={theme_no}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
    except requests.RequestException:
        return []
    r.encoding = "euc-kr"
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table", class_="type_5")
    if table is None:
        return []

    def _num(txt: str) -> Optional[float]:
        txt = txt.replace(",", "").replace("%", "").replace("+", "").strip()
        try:
            return float(txt)
        except ValueError:
            return None

    rows: list[dict] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue
        a = tds[0].find("a")
        if a is None:
            continue
        name = a.get_text(strip=True)
        href = a.get("href", "")
        m = re.search(r"code=(\d+)", href)
        if not m:
            continue
        code = m.group(1)
        close = _num(tds[1].get_text(strip=True))
        change_pct = None
        for td in tds[2:]:
            txt = td.get_text(strip=True)
            if "%" in txt:
                change_pct = _num(txt)
                break
        rows.append({"name": name, "code": code, "close": close, "change_pct": change_pct})
    rows.sort(key=lambda x: (x.get("change_pct") or -1e9), reverse=True)
    return rows[:limit]


def collect_inferred_stocks(category: dict, theme_index: dict[str, dict],
                            stocks_per_theme: int = 4, cap: int = 8) -> tuple[list[dict], list[dict]]:
    """
    카테고리 → 테마 해석 → 주도주 리스트.
    return: (inferred_stocks, resolved_themes)
    """
    themes = resolve_category_themes(category, theme_index)
    inferred: list[dict] = []
    seen_codes: set[str] = set()
    for t in themes:
        constituents = _fetch_theme_constituents(t["no"], limit=stocks_per_theme)
        for s in constituents:
            if s["code"] in seen_codes:
                continue
            seen_codes.add(s["code"])
            s["theme_name"] = t["name"]
            inferred.append(s)
        if len(inferred) >= cap:
            break
    inferred.sort(key=lambda x: (x.get("change_pct") or -1e9), reverse=True)
    return inferred[:cap], themes


def enrich_with_krx(items: list[dict], name_map: dict[str, dict],
                    code_map: dict[str, dict]) -> list[dict]:
    """KRX 시세로 보강 (close/change_pct/market)."""
    for it in items:
        code = it.get("code") or it.get("stock_code")
        if not code:
            hit = name_map.get(it.get("name") or it.get("stock_name") or "")
            if hit:
                code = hit["Code"]
                it["code"] = code
        if code and code in code_map:
            krx = code_map[code]
            if it.get("close") in (None, 0):
                it["close"] = krx.get("Close")
            if it.get("change_pct") is None:
                it["change_pct"] = krx.get("ChagesRatio")
            it["market"] = krx.get("Market")
    return items


# ============================================================
# 뉴스 크롤링 (멀티 섹션)
# ============================================================

def fetch_policy_news(sections: Optional[list[int]] = None,
                      per_section: int = 15,
                      fetch_body: bool = True) -> list[dict]:
    """
    네이버 경제 뉴스 여러 섹션에서 기사 수집.
    본문 추출은 JS 리다이렉트 처리를 포함 (기존 stock_news_alert 로직과 동일).
    """
    if sections is None:
        sections = [258, 259, 261, 263]

    news_list: list[dict] = []
    seen_urls: set[str] = set()

    for section_id2 in sections:
        list_url = (
            f"https://finance.naver.com/news/news_list.naver"
            f"?mode=LSS2D&section_id=101&section_id2={section_id2}"
        )
        try:
            r = requests.get(list_url, headers=HEADERS, timeout=10)
        except requests.RequestException:
            continue
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        articles = soup.select("dl dd.articleSubject a")

        for a in articles[:per_section]:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            aid = re.search(r"article_id=(\d+)", href)
            oid = re.search(r"office_id=(\d+)", href)
            if not (title and aid and oid):
                continue
            link = f"https://n.news.naver.com/mnews/article/{oid.group(1)}/{aid.group(1)}"
            if link in seen_urls:
                continue
            seen_urls.add(link)

            body = _fetch_article_body(link) if fetch_body else ""
            news_list.append({
                "title": title,
                "link": link,
                "content": body,
                "section": section_id2,
            })
    return news_list


def _fetch_article_body(url: str, max_len: int = 2500) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
    except requests.RequestException:
        return ""
    soup = BeautifulSoup(r.text, "html.parser")
    area = soup.select_one("#dic_area") or soup.select_one("#newsct_article")
    if area is None:
        return ""
    text = area.get_text(separator=" ", strip=True)
    return text[:max_len]


# ============================================================
# 카테고리 매칭 / 카드 빌드
# ============================================================

BLACKLIST_TITLE_PATTERNS = [
    "증시", "시황", "마감", "종가", "개장", "전장", "장세",
    "뉴욕증시", "장중", "코스피 하락", "코스피 상승",
]


def detect_events_in_news(news_items: list[dict], categories: list[dict],
                          embed_threshold: Optional[float] = None) -> dict[str, list[dict]]:
    """
    카테고리 id → 매칭 기사 리스트.
    - 1단계: 제목 시황성 단어 필터 (시황 잡글 제외)
    - 2단계: 제목에 카테고리 키워드 substring 매칭 (recall)
    - 3단계: 임베딩 유사도 < threshold 면 컷 (precision filter, false positive 제거)
              모델/라이브러리 사용 불가 시 자동 스킵.
    embed_threshold=None 이면 category_matcher.DEFAULT_THRESHOLD 사용.
    """
    from category_matcher import (
        DEFAULT_THRESHOLD,
        build_category_index,
        score_article_categories,
    )
    threshold = DEFAULT_THRESHOLD if embed_threshold is None else embed_threshold
    cat_index = build_category_index(categories)

    result: dict[str, list[dict]] = {c["id"]: [] for c in categories}
    for news in news_items:
        title = news.get("title", "")
        if any(pat in title for pat in BLACKLIST_TITLE_PATTERNS):
            continue

        scores: Optional[dict[str, float]] = None  # lazy: 키워드 매칭이 1건이라도 있을 때만 계산
        for c in categories:
            in_title = [k for k in c["keywords"] if k in title]
            if not in_title:
                continue
            if cat_index is not None:
                if scores is None:
                    scores = score_article_categories(
                        title, news.get("content", ""), cat_index,
                    )
                if scores.get(c["id"], 1.0) < threshold:
                    continue
            enriched = dict(news)
            enriched["matched_keywords"] = in_title
            enriched["title_match"] = True
            if scores is not None:
                enriched["embed_score"] = scores.get(c["id"])
            result[c["id"]].append(enriched)

    # ECOS cross-check: fomc/fx/oil 카테고리 매칭이 실제 ECOS 변동과 정합인지 검증.
    # 키 미설정 / 인프라 실패 시 graceful no-op (페널티 X). RSS 에 pub_date 가
    # 없으면 today() 로 폴백 (가장 최근 데이터로 검증).
    try:
        from ecos_client import load_ecos_key, verify_category_with_ecos
    except ImportError:
        return result
    if not load_ecos_key():
        return result
    from datetime import date
    today = date.today()
    for cid, items in result.items():
        if cid not in ("fomc", "fx", "oil"):
            continue
        for n in items:
            check = verify_category_with_ecos(cid, today)
            n["ecos_verified"] = check["verified"]
            n["ecos_reason"] = check["reason"]
            n["ecos_movement_pct"] = check["movement_pct"]
            if not check["verified"]:
                n["low_signal"] = True
    return result


def build_event_cards(news_items: list[dict],
                      categories: list[dict],
                      theme_index: dict[str, dict],
                      name_map: dict[str, dict],
                      code_map: dict[str, dict]) -> list[dict]:
    """
    카테고리별 EventCard 생성. 매칭 뉴스 0건인 카테고리는 제외.
    """
    matches = detect_events_in_news(news_items, categories)

    cards: list[dict] = []
    for c in categories:
        matched_news = matches[c["id"]]
        if not matched_news:
            continue

        # direct: 제목에 카테고리 키워드가 있는 기사(이벤트 중심 기사)에서만 추출
        title_matched = [n for n in matched_news if n.get("title_match")]
        if title_matched:
            big_text = " ".join(n.get("title", "") + " " + n.get("content", "") for n in title_matched)
            direct_stocks = find_direct_stocks_in_text(
                big_text, name_map, limit=10, min_len=3, anchors=c["keywords"],
            )
        else:
            direct_stocks = []
        direct_enriched = enrich_with_krx(
            [{"name": s["Name"], "code": s["Code"], "close": s.get("Close"),
              "change_pct": s.get("ChagesRatio"), "market": s.get("Market")}
             for s in direct_stocks],
            name_map, code_map,
        )

        # inferred: 카테고리 theme_hints → 테마 주도주
        inferred_stocks, resolved_themes = collect_inferred_stocks(c, theme_index)
        inferred_stocks = enrich_with_krx(inferred_stocks, name_map, code_map)

        # direct 에 있는 코드는 inferred 에서 제외
        direct_codes = {s["code"] for s in direct_enriched if s.get("code")}
        inferred_stocks = [s for s in inferred_stocks if s.get("code") not in direct_codes]

        cards.append({
            "category": c,
            "matched_news": matched_news,
            "direct_stocks": direct_enriched,
            "inferred_stocks": inferred_stocks,
            "resolved_themes": resolved_themes,
        })

    # 스코어로 정렬: 뉴스 수 × 2 + direct 종목 수
    cards.sort(
        key=lambda card: -(len(card["matched_news"]) * 2 + len(card["direct_stocks"]))
    )

    # 외국인·기관 5일 수급 보강 (NAVER 스크래핑) — direct 종목만, 카드당 호출 비용 관리
    from naver_supply import enrich_stocks_with_supply
    all_chips: list[dict] = []
    for card in cards:
        all_chips.extend(card["direct_stocks"])
    enrich_stocks_with_supply(all_chips, days=5, max_calls=80)

    return cards



# ============================================================
# HTML 렌더
# ============================================================

def render_policy_event_html(cards: list[dict], total_news_count: int) -> str:
    import html as html_lib

    today = date.today().isoformat()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    def _stock_chip(s: dict, kind: str) -> str:
        """kind: 'direct' | 'inferred'"""
        name = html_lib.escape(s.get("name") or "")
        code = s.get("code") or ""
        pct = s.get("change_pct")
        close = s.get("close")
        pct_str = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        close_str = f"{close:,.0f}" if isinstance(close, (int, float)) and close > 0 else ""
        cls = "up" if isinstance(pct, (int, float)) and pct > 0 else (
            "down" if isinstance(pct, (int, float)) and pct < 0 else "flat"
        )
        link = f"https://m.stock.naver.com/domestic/stock/{code}/total" if code else "#"
        theme_tag = ""
        if kind == "inferred" and s.get("theme_name"):
            theme_tag = f'<span class="theme-tag">{html_lib.escape(s["theme_name"])}</span>'
        from naver_supply import supply_badge_html
        supply_html = supply_badge_html(s.get("supply"), days=5)
        return (
            f'<a class="stock-chip {cls} {kind}" href="{link}" target="_blank" rel="noopener">'
            f'<span class="s-name">{name}</span>'
            + (f'<span class="s-close">{close_str}</span>' if close_str else '')
            + (f'<span class="s-pct">{pct_str}</span>' if pct_str else '')
            + supply_html
            + theme_tag
            + '</a>'
        )

    def _render_card(card: dict) -> str:
        c = card["category"]
        label = html_lib.escape(c["label"])
        news_count = len(card["matched_news"])

        # 뉴스 리스트 (최대 5건)
        news_lis = []
        for n in card["matched_news"][:5]:
            title = html_lib.escape(n["title"])
            link = html_lib.escape(n["link"])
            kws = ", ".join(n.get("matched_keywords", []))
            news_lis.append(
                f'<li><a href="{link}" target="_blank" rel="noopener">{title}</a>'
                f'<span class="kw">🔎 {html_lib.escape(kws)}</span></li>'
            )
        news_html = "<ul class='news-list'>" + "".join(news_lis) + "</ul>"

        # direct 종목
        if card["direct_stocks"]:
            direct_chips = "".join(_stock_chip(s, "direct") for s in card["direct_stocks"])
            direct_block = (
                '<div class="stocks-block">'
                '<div class="label label-direct">📌 기사에서 직접 언급 (고신뢰)</div>'
                f'<div class="chips">{direct_chips}</div>'
                '</div>'
            )
        else:
            direct_block = (
                '<div class="stocks-block">'
                '<div class="label label-direct">📌 기사에서 직접 언급 (고신뢰)</div>'
                '<div class="empty-chip">(기사 본문에서 추출된 KRX 종목 없음)</div>'
                '</div>'
            )

        # inferred 종목
        if card["inferred_stocks"]:
            inferred_chips = "".join(_stock_chip(s, "inferred") for s in card["inferred_stocks"])
            themes_summary = ", ".join(
                html_lib.escape(t["name"]) for t in card["resolved_themes"][:3]
            )
            inferred_block = (
                '<div class="stocks-block">'
                '<div class="label label-inferred">🔗 관련주 추정 (테마 매핑)</div>'
                f'<div class="chips">{inferred_chips}</div>'
                f'<div class="theme-source">매핑 테마: {themes_summary}</div>'
                '</div>'
            )
        else:
            inferred_block = ""  # theme_hints 없거나 해석 실패 시 섹션 자체 숨김

        return (
            '<article>'
            f'<header><h2>{label}</h2>'
            f'<span class="news-badge">📰 {news_count}건 기사</span></header>'
            f'{news_html}'
            f'{direct_block}'
            f'{inferred_block}'
            '</article>'
        )

    if not cards:
        body_html = (
            '<section class="empty-state">오늘 뉴스에서 탐지된 정책·외부 이벤트가 없습니다. '
            '카테고리 키워드가 너무 좁거나 증권 섹션에서 해당 이슈를 다루지 않았을 수 있습니다.</section>'
        )
    else:
        body_html = "".join(_render_card(c) for c in cards)

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<title>정책·이벤트 수혜 대시보드 — {today}</title>
<style>
  :root {{
    --bg:#f5f5f7; --card:#fff; --border:#e5e5ea;
    --text:#1d1d1f; --muted:#86868b; --accent:#0071e3; --accent-soft:#f0f6ff;
    --up:#e74c3c; --up-soft:#fdecea;
    --down:#0080ff; --down-soft:#e8f2ff;
    --flat:#888;
    --direct:#2e7d32; --direct-soft:#e8f5e9;
    --inferred:#6a1b9a; --inferred-soft:#f3e5f5;
  }}
  * {{ box-sizing:border-box; }}
  body {{
    font-family:-apple-system,'Apple SD Gothic Neo','Pretendard','Segoe UI',sans-serif;
    background:var(--bg); color:var(--text); margin:0;
    padding:48px 20px; font-size:16px; line-height:1.6;
  }}
  .container {{ max-width:900px; margin:0 auto; }}
  .page-header {{ margin-bottom:36px; padding-bottom:20px; border-bottom:1px solid var(--border); }}
  .page-header h1 {{ font-size:30px; font-weight:700; margin:0 0 6px; letter-spacing:-0.02em; }}
  .page-meta {{ color:var(--muted); font-size:14px; }}
  .page-meta strong {{ color:var(--text); font-weight:600; }}

  article {{
    background:var(--card); border:1px solid var(--border); border-radius:16px;
    padding:22px 26px; margin-bottom:14px;
  }}
  article header {{
    display:flex; align-items:baseline; gap:10px; margin-bottom:14px;
    padding-bottom:10px; border-bottom:1px solid var(--border);
  }}
  article h2 {{ font-size:18px; font-weight:700; margin:0; letter-spacing:-0.01em; }}
  .news-badge {{
    margin-left:auto; font-size:12px; font-weight:600; color:var(--accent);
    background:var(--accent-soft); padding:3px 10px; border-radius:12px;
  }}

  .news-list {{ list-style:none; padding:0; margin:0 0 16px; }}
  .news-list li {{ padding:6px 0; font-size:13.5px; }}
  .news-list li + li {{ border-top:1px dashed #f0f0f5; }}
  .news-list a {{ color:#333; text-decoration:none; }}
  .news-list a:hover {{ color:var(--accent); text-decoration:underline; }}
  .kw {{ color:var(--muted); font-size:11px; margin-left:8px; }}

  .stocks-block {{ margin-top:12px; }}
  .label {{ font-size:12px; font-weight:700; margin-bottom:8px; letter-spacing:-0.01em; }}
  .label-direct {{ color:var(--direct); }}
  .label-inferred {{ color:var(--inferred); }}
  .chips {{ display:flex; flex-wrap:wrap; gap:6px; }}
  .empty-chip {{ color:var(--muted); font-size:13px; font-style:italic; padding:4px 0; }}

  .stock-chip {{
    display:inline-flex; align-items:center; gap:8px;
    background:#f8f8fa; border:1px solid var(--border);
    padding:5px 10px; border-radius:8px;
    text-decoration:none; color:var(--text); font-size:13px;
  }}
  .stock-chip.direct {{ border-left:3px solid var(--direct); }}
  .stock-chip.inferred {{ border-left:3px solid var(--inferred); background:#faf7fc; }}
  .stock-chip.up {{ background:var(--up-soft); border-color:#f5c6cb; }}
  .stock-chip.up.inferred {{ background:#faf0f0; }}
  .stock-chip.up .s-pct {{ color:var(--up); font-weight:700; }}
  .stock-chip.down {{ background:var(--down-soft); border-color:#b8d4ff; }}
  .stock-chip.down.inferred {{ background:#f0f2fb; }}
  .stock-chip.down .s-pct {{ color:var(--down); font-weight:700; }}
  .s-name {{ font-weight:600; }}
  .s-close {{ font-size:11px; color:#555; }}
  .s-pct {{ font-size:12px; }}
  .supply-tag {{
    font-size:10px; font-weight:700;
    padding:1px 5px; border-radius:4px; margin-left:3px;
    white-space:nowrap; cursor:help;
  }}
  .supply-buy {{ background:#fdecea; color:#c0392b; }}
  .supply-sell {{ background:#e8f2ff; color:#1565c0; }}
  .theme-tag {{
    background:var(--inferred-soft); color:var(--inferred);
    font-size:10px; padding:2px 6px; border-radius:4px;
  }}
  .theme-source {{
    margin-top:8px; font-size:11px; color:var(--muted); font-style:italic;
  }}

  .empty-state {{
    background:var(--card); border:1px dashed var(--border); border-radius:14px;
    padding:40px 24px; text-align:center; color:var(--muted);
  }}

  @media (max-width:640px) {{
    body {{ padding:24px 16px; }}
    article {{ padding:18px 20px; }}
  }}
</style></head><body>
<div class="container">
  <header class="page-header">
    <h1>🚀 정책·이벤트 수혜 대시보드</h1>
    <div class="page-meta">
      오늘 <strong>{today}</strong> · 뉴스 <strong>{total_news_count}건</strong> 분석 · 매칭 카테고리 <strong>{len(cards)}개</strong> · 생성 {now}
    </div>
  </header>
  {body_html}
</div>
</body></html>"""
