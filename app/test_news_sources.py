"""news_sources 보안 회귀 테스트."""

import news_sources as ns


class _Resp:
    status_code = 200
    apparent_encoding = "utf-8"
    encoding = "utf-8"
    text = "<html><body><article>본문 내용</article></body></html>"
    content = (
        b"<rss><channel><item><title>T</title>"
        b"<link>https://www.edaily.co.kr/news/read</link></item></channel></rss>"
    )


def test_fetch_article_body_does_not_disable_tls_verification_for_edaily(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return _Resp()

    monkeypatch.setattr(ns._session, "get", fake_get)

    ns.fetch_article_body("https://www.edaily.co.kr/news/read")

    assert calls[0].get("verify") is not False


def test_fetch_rss_news_does_not_disable_tls_verification_for_edaily_feed(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        return _Resp()

    monkeypatch.setattr(ns._session, "get", fake_get)
    monkeypatch.setattr(ns, "RSS_FEEDS", [("이데일리 증권", "https://rss.edaily.co.kr/stock_news.xml")])

    ns.fetch_rss_news(per_feed=1, fetch_body=False)

    assert calls[0].get("verify") is not False
