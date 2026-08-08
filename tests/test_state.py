import pytest
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
    # 구버전 배포가 신버전 필드를 지우고 커밋하는 사고를 막는 규칙.
    # paused_until은 이제 실제로 쓰이는 키라 '모르는 키'의 예시가 될 수 없다.
    s = state.parse_state('{"future_field": "2026-07-30"}')
    assert s["future_field"] == "2026-07-30"


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
    original = {"daily_count": 7, "readme_top_n": 3,
                "auto_stop_threshold": 30, "paused_until": "2026-07-30"}
    assert state.parse_state(state.render_state(original)) == original


def test_parse_state_new_keys_have_defaults():
    s = state.parse_state(None)
    assert s["auto_stop_threshold"] == 20
    assert s["readme_top_n"] == 5


def test_parse_state_preserves_removed_max_fill_per_run():
    # 배포 시점에 구버전 state.json이 남아 있어도 깨지지 않아야 한다.
    # 이제 읽는 코드는 없지만 '모르는 키 보존' 규칙이 값을 지킨다.
    s = state.parse_state('{"daily_count": 5, "max_fill_per_run": 3}')
    assert s["max_fill_per_run"] == 3
    assert s["daily_count"] == 5


def test_parse_state_clamps_below_minimum():
    assert state.parse_state('{"readme_top_n": 0}')["readme_top_n"] == 1
    assert state.parse_state('{"daily_count": 0}')["daily_count"] == 1


def test_parse_state_clamps_above_maximum():
    assert state.parse_state('{"readme_top_n": 99}')["readme_top_n"] == 15
    assert state.parse_state('{"auto_stop_threshold": 500}')["auto_stop_threshold"] == 100


def test_parse_state_keeps_boundary_values():
    assert state.parse_state('{"readme_top_n": 15}')["readme_top_n"] == 15
    assert state.parse_state('{"readme_top_n": 1}')["readme_top_n"] == 1


def test_warnings_records_clamped_key_only():
    w = []
    state.parse_state('{"readme_top_n": 0, "daily_count": 5}', w)
    assert len(w) == 1
    assert "readme_top_n" in w[0]


def test_warnings_empty_for_valid_state():
    w = []
    state.parse_state('{"daily_count": 5, "auto_stop_threshold": 20, "readme_top_n": 5}', w)
    assert w == []


def test_warnings_records_parse_failure():
    w = []
    assert state.parse_state('{ "daily_count": ', w) == state.DEFAULTS
    assert len(w) == 1


def test_warnings_records_non_object_top_level():
    w = []
    assert state.parse_state("[1, 2]", w) == state.DEFAULTS
    assert len(w) == 1


def test_warnings_records_type_error():
    w = []
    state.parse_state('{"readme_top_n": "5"}', w)
    assert len(w) == 1
    assert "readme_top_n" in w[0]


def test_type_error_takes_precedence_over_range():
    # "0"은 타입 오류로 기본값 5가 된다. 범위 클램프 대상이 아니므로 경고는 1건이다.
    w = []
    s = state.parse_state('{"readme_top_n": "0"}', w)
    assert s["readme_top_n"] == 5
    assert len(w) == 1


def test_in_range_boundaries():
    assert state.in_range("daily_count", 1)
    assert state.in_range("daily_count", 10)
    assert not state.in_range("daily_count", 0)
    assert not state.in_range("daily_count", 11)


def test_in_range_unknown_key_is_true():
    assert state.in_range("paused_until", "forever")


def test_range_text_formats_bounds():
    assert state.range_text("daily_count") == "1~10"
    assert state.range_text("readme_top_n") == "1~15"



# --- 정지 상태 (paused_until) ---

def test_get_paused_until_absent_returns_none():
    assert state.get_paused_until({"daily_count": 5}) is None


def test_get_paused_until_reads_date_and_forever():
    assert state.get_paused_until({"paused_until": "2026-08-10"}) == "2026-08-10"
    assert state.get_paused_until({"paused_until": "forever"}) == "forever"


@pytest.mark.parametrize("bad", [
    "invalid", "2026_07_28", "2026-13-99", 20260728, None, True, "",
])
def test_get_paused_until_rejects_corrupt_values(bad):
    # 훼손된 값이 '정지 아님'으로 떨어져야 한다.
    # is_paused는 ISO 문자열 비교라, 검증이 없으면 "2026-07-27" <= "invalid" 가 True가 되어
    # 사용자에게 아무 신호 없이 영구 정지된다. 이 테스트가 그 회귀를 막는다.
    assert state.get_paused_until({"paused_until": bad}) is None
    assert state.is_paused({"paused_until": bad}, "2026-07-27") is False


def test_set_paused_until_inserts_and_replaces():
    s1 = state.set_paused_until({"daily_count": 5}, "2026-08-10")
    assert s1["paused_until"] == "2026-08-10"
    s2 = state.set_paused_until(s1, "forever")
    assert s2["paused_until"] == "forever"


def test_set_paused_until_none_removes_key():
    s = state.set_paused_until({"daily_count": 5, "paused_until": "forever"}, None)
    assert "paused_until" not in s
    assert s["daily_count"] == 5


def test_set_paused_until_on_missing_key_is_safe():
    # 루틴 A의 만료 정리가 조건 없이 호출하므로 키가 없어도 터지면 안 된다
    assert state.set_paused_until({"daily_count": 5}, None) == {"daily_count": 5}


def test_set_paused_until_does_not_mutate_original():
    original = {"daily_count": 5}
    state.set_paused_until(original, "forever")
    assert "paused_until" not in original


def test_is_paused_boundary_on_last_paused_day():
    # paused_until은 '마지막 정지일'이라 그날까지 정지, 다음 날 재개
    s = {"paused_until": "2026-08-10"}
    assert state.is_paused(s, "2026-08-09") is True
    assert state.is_paused(s, "2026-08-10") is True
    assert state.is_paused(s, "2026-08-11") is False


def test_is_paused_forever_always_true():
    assert state.is_paused({"paused_until": "forever"}, "2099-01-01") is True


def test_is_paused_absent_is_false():
    assert state.is_paused({"daily_count": 5}, "2026-08-10") is False


def test_pause_days_limit_present():
    assert state.LIMITS["pause_days"] == (1, 30)
    assert state.range_text("pause_days") == "1~30"


# --- 자동 정지 임계값 ---

def test_auto_stop_threshold_default_is_20():
    assert state.parse_state(None)["auto_stop_threshold"] == 20


def test_auto_stop_threshold_clamped():
    # 하한 5: 그 아래면 하루치 생성만으로 즉시 자동 정지돼 기능이 무의미해진다
    assert state.parse_state('{"auto_stop_threshold": 0}')["auto_stop_threshold"] == 5
    assert state.parse_state('{"auto_stop_threshold": 999}')["auto_stop_threshold"] == 100


def test_auto_stop_threshold_keeps_boundary_values():
    assert state.parse_state('{"auto_stop_threshold": 5}')["auto_stop_threshold"] == 5
    assert state.parse_state('{"auto_stop_threshold": 100}')["auto_stop_threshold"] == 100


def test_auto_stop_threshold_wrong_type_falls_back():
    assert state.parse_state('{"auto_stop_threshold": "20"}')["auto_stop_threshold"] == 20
