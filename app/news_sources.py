"""
원 publisher RSS 피드에서 뉴스 수집.

Naver 뉴스 aggregator 외에 직접 연합뉴스·매경 경제/증권 섹션을 긁어
캘린더의 미래형 이벤트 추출 원재료 다양화.
"""

import re
import ssl
import xml.etree.ElementTree as ET
from typing import Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter


HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


class _LegacyTLSAdapter(HTTPAdapter):
    """구식 TLS 사용 서버(이데일리 RSS 등) 호환."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1
        except (AttributeError, ValueError):
            pass
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


_session = requests.Session()
_session.mount("https://rss.edaily.co.kr/", _LegacyTLSAdapter())
_session.mount("https://www.edaily.co.kr/", _LegacyTLSAdapter())
_session.mount("https://m.edaily.co.kr/", _LegacyTLSAdapter())


# (label, rss_url)
RSS_FEEDS: list[tuple[str, str]] = [
    ("연합뉴스 경제",  "https://www.yna.co.kr/rss/economy.xml"),
    ("연합뉴스 산업",  "https://www.yna.co.kr/rss/industry.xml"),
    ("매경 증권",      "https://www.mk.co.kr/rss/50200011/"),
    ("매경 경제",      "https://www.mk.co.kr/rss/30000001/"),
    ("매경 기업·산업",  "https://www.mk.co.kr/rss/50100032/"),
    ("이데일리 증권",  "https://rss.edaily.co.kr/stock_news.xml"),
    ("이데일리 경제",  "https://rss.edaily.co.kr/economy_news.xml"),
    # 머니투데이는 통합 피드만 살아있어 URL 섹션으로 경제·증권만 골라냄 (_keep_url 참조)
    ("머니투데이",      "https://rss.mt.co.kr/mt_news.xml"),
]


# 매체별 본문 셀렉터 우선순위
_BODY_SELECTORS: dict[str, list[str]] = {
    "yna.co.kr": [".story-news", "#articleWrap", "article"],
    "mk.co.kr":  ["#article_body", ".news_cnt_detail_wrap", "article"],
    "edaily.co.kr": ["#Conts_Area", ".news_body", ".view_body", "article"],
    "mt.co.kr":  ["#articleBody", ".article-container", "article"],
}


# 머니투데이 통합 RSS 에서 경제/증권/산업 외 섹션은 컷 (스포츠·연예·문화 등)
_MT_KEEP_SECTIONS = {"economy", "stock", "bizfin", "industry", "finance", "money"}


def _keep_url(url: str, source_label: str) -> bool:
    """source 별 URL 화이트리스트. 머니투데이 통합 피드 노이즈 제거용."""
    if source_label != "머니투데이":
        return True
    m = re.search(r"https?://www\.mt\.co\.kr/([a-z]+)/", url)
    if not m:
        return False
    return m.group(1) in _MT_KEEP_SECTIONS


def _select_body_area(soup: BeautifulSoup, url: str):
    host_m = re.match(r"https?://([^/]+)", url)
    if not host_m:
        return None
    host = host_m.group(1).lower()
    for domain, selectors in _BODY_SELECTORS.items():
        if host == domain or host.endswith("." + domain):
            for sel in selectors:
                area = soup.select_one(sel)
                if area:
                    return area
    return None


def fetch_article_body(url: str, max_len: int = 2500) -> str:
    verify = "edaily.co.kr" not in url
    try:
        r = _session.get(url, headers=HEADERS, timeout=10, verify=verify)
    except requests.RequestException:
        return ""
    # EUC-KR 레거시 사이트도 있어 fallback
    r.encoding = r.apparent_encoding or r.encoding
    soup = BeautifulSoup(r.text, "html.parser")
    area = _select_body_area(soup, url)
    if area is None:
        return ""
    text = area.get_text(separator=" ", strip=True)
    return text[:max_len]


def _parse_rss_items(xml_data, source_label: str) -> list[dict]:
    """xml_data: bytes 또는 str. bytes이면 XML 선언의 encoding 따라 파싱."""
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        # fallback: bytes → utf-8 강제 디코딩 후 재시도
        if isinstance(xml_data, bytes):
            try:
                root = ET.fromstring(xml_data.decode("utf-8", errors="ignore"))
            except ET.ParseError:
                return []
        else:
            return []
    items = root.findall(".//item")
    out: list[dict] = []
    for it in items:
        title_el = it.find("title")
        link_el = it.find("link")
        pub_el = it.find("pubDate")
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        pub = (pub_el.text or "").strip() if pub_el is not None else ""
        if not title or not link:
            continue
        out.append({
            "title": title,
            "link": link,
            "pub_date": pub,
            "source": source_label,
        })
    return out


def fetch_rss_news(per_feed: int = 20, fetch_body: bool = True) -> list[dict]:
    """
    등록된 모든 RSS 피드에서 상위 per_feed 건씩 수집.
    fetch_body=True 면 각 기사 본문도 긁어서 `content` 필드 채움.
    반환 포맷은 fetch_policy_news 와 동일 (title/link/content/section).
    """
    news: list[dict] = []
    seen_links: set[str] = set()

    for label, rss_url in RSS_FEEDS:
        # 이데일리는 구식 TLS 서버라 SSL 검증 생략
        verify = "edaily.co.kr" not in rss_url
        try:
            r = _session.get(rss_url, headers=HEADERS, timeout=10, verify=verify)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        # bytes 로 넘겨 XML 선언의 encoding 을 ET 가 해석하게 함
        items = _parse_rss_items(r.content, label)
        # source 별 URL 화이트리스트 적용 후 per_feed 만큼만 채택
        items = [it for it in items if _keep_url(it["link"], label)][:per_feed]

        for it in items:
            if it["link"] in seen_links:
                continue
            seen_links.add(it["link"])
            body = fetch_article_body(it["link"]) if fetch_body else ""
            news.append({
                "title": it["title"],
                "link": it["link"],
                "content": body,
                "section": label,
            })
    return news


def merge_news_dedupe(lists: list[list[dict]]) -> list[dict]:
    """여러 뉴스 리스트 병합 + 제목 기반 중복 제거."""
    seen_titles: set[str] = set()
    merged: list[dict] = []
    for lst in lists:
        for n in lst:
            key = _norm_title(n.get("title", ""))
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            merged.append(n)
    return merged


def _norm_title(t: str) -> str:
    t = re.sub(r"[\s\[\]\(\)<>『』「」‘’“”·・\.,…]", "", t)
    return t.lower()
