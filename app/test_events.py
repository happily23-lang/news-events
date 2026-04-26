"""events 모듈 단위 테스트. 주로 종목 매칭 정확도 회귀 방지."""

import pytest

from events import (
    _has_word_boundary,
    _is_word_char,
    _strip_press_boilerplate,
    find_direct_stocks_in_text,
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
