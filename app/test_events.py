"""events 모듈 단위 테스트. 주로 종목 매칭 정확도 회귀 방지."""

import pytest

from events import (
    _has_word_boundary,
    _is_word_char,
    _strip_press_boilerplate,
    build_event_cards,
    detect_events_in_news,
    find_direct_stocks_in_text,
    render_policy_event_html,
)


# ============================================================
# _is_word_char / _has_word_boundary
# ============================================================

def test_is_word_char_korean():
    assert _is_word_char("가") is True
    assert _is_word_char("힣") is True
    assert _is_word_char("ㄱ") is False  # 자모 단독은 단어 글자로 보지 않음 (실제 본문에서 거의 X)


def test_is_word_char_alphabet_digit():
    assert _is_word_char("A") is True
    assert _is_word_char("z") is True
    assert _is_word_char("3") is True


def test_is_word_char_punctuation():
    assert _is_word_char(" ") is False
    assert _is_word_char(".") is False
    assert _is_word_char("·") is False
    assert _is_word_char("(") is False


def test_has_word_boundary_at_text_edges():
    text = "엠브레인"
    assert _has_word_boundary(text, 0, 4) is True


def test_has_word_boundary_blocked_by_korean_suffix():
    text = "엠브레인퍼블릭"
    # '엠브레인' 매칭 시 우측이 '퍼' (한글) → 거부
    assert _has_word_boundary(text, 0, 4) is False


def test_has_word_boundary_blocked_by_korean_prefix():
    text = "에이엠브레인"
    # '엠브레인' 매칭 시 좌측이 '이' (한글) → 거부
    assert _has_word_boundary(text, 2, 4) is False


def test_has_word_boundary_allowed_by_punctuation():
    text = "엠브레인·케이스탯"
    # '엠브레인' 매칭 시 좌측 시작, 우측이 '·' → 통과
    assert _has_word_boundary(text, 0, 4) is True


# ============================================================
# _strip_press_boilerplate
# ============================================================

def test_strip_kakao_jebo():
    out = _strip_press_boilerplate("이번 박람회는 K로컬. 제보는 카카오톡 '연합뉴스'.")
    assert "카카오톡" not in out


def test_strip_kakao_channel():
    out = _strip_press_boilerplate("춘천시 파크골프장 재개장. 카카오톡 채널을 추가해주세요.")
    assert "카카오톡" not in out


def test_strip_preserves_real_kakao_mention():
    """진짜 '카카오' 언급은 보존되어야."""
    out = _strip_press_boilerplate("카카오 신규 사업 발표.")
    assert "카카오" in out


# ============================================================
# find_direct_stocks_in_text — 보일러플레이트 + 부분 매칭 회귀 방지
# ============================================================

@pytest.fixture
def name_map():
    return {
        "카카오": {"Code": "035720", "Name": "카카오"},
        "카카오게임즈": {"Code": "293490", "Name": "카카오게임즈"},
        "엠브레인": {"Code": "165570", "Name": "엠브레인"},
        "현대차": {"Code": "005380", "Name": "현대차"},
        "삼성생명": {"Code": "032830", "Name": "삼성생명"},
        "포스코": {"Code": "005490", "Name": "포스코"},
    }


def _names(hits):
    return [h["Name"] for h in hits]


def test_kakao_jebo_boilerplate_does_not_match(name_map):
    text = "이번 박람회는 K로컬. 제보는 카카오톡 '연합뉴스'로 보내주세요."
    assert _names(find_direct_stocks_in_text(text, name_map)) == []


def test_embrain_public_does_not_match_embrain(name_map):
    text = "엠브레인퍼블릭·케이스탯리서치·코리아리서치·한국리서치가 진행한 NBS 조사."
    assert _names(find_direct_stocks_in_text(text, name_map)) == []


def test_real_embrain_mention_matches(name_map):
    text = "엠브레인 신규 사업 발표 (Q3 가이던스)."
    assert _names(find_direct_stocks_in_text(text, name_map)) == ["엠브레인"]


def test_embrain_and_embrain_public_mixed_keeps_only_real(name_map):
    text = "엠브레인 본사 이전. 엠브레인퍼블릭은 별도 법인."
    assert _names(find_direct_stocks_in_text(text, name_map)) == ["엠브레인"]


def test_hyundai_full_name_does_not_match_short_form(name_map):
    """'현대자동차' 본문에 '현대차' 매칭 거부 — 별명·축약 매칭 차단으로 일관성."""
    text = "현대자동차의 신차 출시."
    assert _names(find_direct_stocks_in_text(text, name_map)) == []


def test_kakao_and_kakao_games_normal_match(name_map):
    text = "카카오 신규 게임 출시. 카카오게임즈 매출 증가."
    assert set(_names(find_direct_stocks_in_text(text, name_map))) == {"카카오", "카카오게임즈"}


def test_samsung_life_insurance_does_not_match_partial(name_map):
    text = "삼성생명보험 본사 이전 검토."
    assert _names(find_direct_stocks_in_text(text, name_map)) == []


def test_punctuation_separated_names_match(name_map):
    """'카카오·포스코' 같이 가운뎃점으로 구분된 이름은 둘 다 매칭."""
    text = "카카오·포스코·LG가 공동 사업 추진."
    assert set(_names(find_direct_stocks_in_text(text, name_map))) == {"카카오", "포스코"}


def test_ensure_certifi_ca_bundle_sets_ssl_cert_file(monkeypatch):
    import os
    import events

    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    events._ensure_certifi_ca_bundle()

    assert os.environ["SSL_CERT_FILE"].endswith("cacert.pem")


# ============================================================
# render_policy_event_html — signal score badge
# ============================================================

def test_render_policy_event_html_shows_signal_score_badge():
    card = {
        "category": {"label": "AI 반도체·HBM"},
        "matched_news": [
            {"title": "HBM 공급 확대", "link": "https://example.com/a", "matched_keywords": ["HBM"]},
            {"title": "AI 칩 투자", "link": "https://example.com/b", "matched_keywords": ["AI 칩"]},
            {"title": "데이터센터 증설", "link": "https://example.com/c", "matched_keywords": ["데이터센터"]},
        ],
        "direct_stocks": [
            {"name": "SK하이닉스", "code": "000660", "close": 250000, "change_pct": 2.1},
        ],
        "inferred_stocks": [
            {"name": "한미반도체", "code": "042700", "change_pct": 1.2, "theme_name": "반도체"},
        ],
        "resolved_themes": [{"name": "반도체"}],
    }

    html = render_policy_event_html([card], total_news_count=3)

    assert "신호 강도" in html
    assert "70점" in html
    assert "높음" in html


def test_detect_events_marks_keyword_only_match_mode(monkeypatch):
    import category_matcher

    monkeypatch.setattr(category_matcher, "build_category_index", lambda categories: None)
    categories = [{
        "id": "ai_semi",
        "label": "AI 반도체",
        "keywords": ["HBM"],
        "theme_hints": [],
        "examples": [],
    }]
    news = [{"title": "HBM 공급 확대", "content": "", "link": "https://example.com/a"}]

    result = detect_events_in_news(news, categories)

    assert result["ai_semi"][0]["match_mode"] == "keyword"


def test_render_policy_event_html_shows_keyword_only_match_label():
    card = {
        "category": {"label": "AI 반도체·HBM"},
        "matched_news": [{
            "title": "HBM 공급 확대",
            "link": "https://example.com/a",
            "matched_keywords": ["HBM"],
            "match_mode": "keyword",
        }],
        "direct_stocks": [],
        "inferred_stocks": [],
        "resolved_themes": [],
    }

    html = render_policy_event_html([card], total_news_count=1)

    assert "키워드만" in html


def test_render_policy_event_html_warns_low_signal_cards():
    card = {
        "category": {"label": "환율 급등"},
        "matched_news": [{
            "title": "환율 급등 우려",
            "link": "https://example.com/fx",
            "matched_keywords": ["환율"],
            "match_mode": "keyword",
            "low_signal": True,
        }],
        "direct_stocks": [],
        "inferred_stocks": [],
        "resolved_themes": [],
    }

    html = render_policy_event_html([card], total_news_count=1)

    assert "저신뢰 신호" in html
    assert "추천 후보가 아닌 참고용" in html


def test_build_event_cards_sorts_stronger_signal_before_many_weak_news(monkeypatch):
    import events
    import naver_supply

    categories = [
        {"id": "weak_fx", "label": "환율 급등", "keywords": ["환율"], "theme_hints": []},
        {"id": "strong_hbm", "label": "AI 반도체", "keywords": ["HBM"], "theme_hints": []},
    ]
    weak_news = [
        {
            "title": f"환율 급등 우려 {idx}",
            "content": "",
            "link": f"https://example.com/fx-{idx}",
            "matched_keywords": ["환율"],
            "title_match": True,
            "low_signal": True,
        }
        for idx in range(4)
    ]
    strong_news = [{
        "title": "삼성전자 HBM 투자 확대",
        "content": "",
        "link": "https://example.com/hbm",
        "matched_keywords": ["HBM"],
        "title_match": True,
    }]
    monkeypatch.setattr(
        events,
        "detect_events_in_news",
        lambda news_items, categories: {"weak_fx": weak_news, "strong_hbm": strong_news},
    )
    supply_calls = []

    def fake_enrich(*args, **kwargs):
        supply_calls.append(kwargs)

    monkeypatch.setattr(naver_supply, "enrich_stocks_with_supply", fake_enrich)

    cards = build_event_cards(
        news_items=[],
        categories=categories,
        theme_index={},
        name_map={"삼성전자": {"Code": "005930", "Name": "삼성전자"}},
        code_map={},
        supply_max_calls=0,
    )

    assert [card["category"]["id"] for card in cards] == ["strong_hbm", "weak_fx"]
    assert supply_calls[0]["max_calls"] == 0
