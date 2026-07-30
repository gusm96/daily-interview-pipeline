import state


def test_parse_state_none_returns_defaults():
    assert state.parse_state(None) == state.DEFAULTS


def test_parse_state_empty_text_returns_defaults():
    assert state.parse_state("") == state.DEFAULTS


def test_parse_state_broken_json_returns_defaults():
    assert state.parse_state('{ "daily_count": ') == state.DEFAULTS


def test_parse_state_non_object_returns_defaults():
    # 최상위가 객체가 아니면(리스트·숫자) 설정으로 쓸 수 없다
    assert state.parse_state("[1, 2]") == state.DEFAULTS


def test_parse_state_returns_copy_not_shared_defaults():
    # 반환값을 수정해도 DEFAULTS가 오염되면 안 된다(모듈 전역 공유 사고 방지)
    s = state.parse_state(None)
    s["daily_count"] = 99
    assert state.DEFAULTS["daily_count"] == 5


def test_parse_state_missing_key_filled_with_default():
    assert state.parse_state('{"other": 1}')["daily_count"] == 5


def test_parse_state_preserves_unknown_keys():
    # 구버전 배포가 신버전 필드를 지우고 커밋하는 사고를 막는 규칙
    s = state.parse_state('{"paused_until": "2026-07-30"}')
    assert s["paused_until"] == "2026-07-30"


def test_parse_state_wrong_type_falls_back_per_key():
    s = state.parse_state('{"daily_count": "7", "other": 1}')
    assert s["daily_count"] == 5   # 타입 불일치 키만 기본값으로
    assert s["other"] == 1         # 나머지는 유지


def test_parse_state_null_value_falls_back():
    assert state.parse_state('{"daily_count": null}')["daily_count"] == 5


def test_parse_state_bool_is_not_accepted_as_int():
    # 파이썬에서 isinstance(True, int)가 참이므로 명시적으로 막는다
    assert state.parse_state('{"daily_count": true}')["daily_count"] == 5


def test_render_state_is_human_readable():
    assert state.render_state({"daily_count": 3}) == '{\n  "daily_count": 3\n}\n'


def test_render_state_keeps_hangul_unescaped():
    text = state.render_state({"category_counts": {"☕ Java": 1}})
    assert "☕ Java" in text


def test_render_state_is_deterministic_regardless_of_key_order():
    # 키 순서만 다른 dict가 같은 텍스트를 내야 헛된 diff가 안 생긴다
    a = state.render_state({"daily_count": 5, "paused_until": "2026-07-30"})
    b = state.render_state({"paused_until": "2026-07-30", "daily_count": 5})
    assert a == b


def test_round_trip():
    original = {"daily_count": 7, "paused_until": "2026-07-30"}
    assert state.parse_state(state.render_state(original)) == original
