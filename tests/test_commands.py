import main


def test_parse_help():
    assert main.parse_mention_command("<@UBOT> help") == ("help", None)


def test_parse_config_show():
    assert main.parse_mention_command("<@UBOT> config") == ("config_show", None)


def test_parse_config_set():
    assert main.parse_mention_command("<@UBOT> config --default=8") == ("config_set", 8)


def test_parse_question_with_number():
    assert main.parse_mention_command("<@UBOT> 질문 3") == ("question", 3)


def test_parse_question_without_number():
    assert main.parse_mention_command("<@UBOT> 질문") == ("question", None)


def test_parse_question_english():
    assert main.parse_mention_command("<@UBOT> question 5") == ("question", 5)


def test_parse_negative_number_passed_through():
    # 범위 검증은 호출부 담당 → 음수도 그대로 전달
    assert main.parse_mention_command("<@UBOT> 질문 -3") == ("question", -3)


def test_parse_unknown():
    assert main.parse_mention_command("<@UBOT> 안녕") == ("unknown", None)


def test_build_help_text_lists_commands():
    text = main.build_help_text()
    assert "질문" in text
    assert "config --default=" in text
    assert "help" in text
    assert "스레드" in text  # 답변 방법 안내 포함


def test_build_help_text_range_follows_limits(monkeypatch):
    # 도움말의 범위 안내가 LIMITS를 따라가야 한다. 하드코딩으로 되돌아가면 여기서 잡힌다.
    import state
    monkeypatch.setitem(state.LIMITS, "daily_count", (1, 20))
    assert "1~20" in main.build_help_text()


# --- 정지/재개 명령 ---

def test_parse_stop_without_arg_is_indefinite():
    assert main.parse_mention_command("<@UBOT> stop") == ("stop", None)


def test_parse_stop_with_days():
    assert main.parse_mention_command("<@UBOT> stop 3") == ("stop", 3)


def test_parse_stop_korean_alias():
    assert main.parse_mention_command("<@UBOT> 정지 5") == ("stop", 5)
    assert main.parse_mention_command("<@UBOT> 정지") == ("stop", None)


def test_parse_stop_with_date_returns_string_not_int():
    # 날짜를 숫자보다 먼저 검사해야 한다. _FIRST_INT_RE를 먼저 돌리면
    # "2026-08-10"에서 2026을 잡아 '2026일간 정지'가 된다.
    result = main.parse_mention_command("<@UBOT> stop 2026-08-10")
    assert result == ("stop", "2026-08-10")
    assert isinstance(result[1], str)


def test_parse_stop_allows_unit_suffix():
    # `stop 3일`, `정지 5 days` 처럼 단위가 붙어도 숫자를 읽는다
    assert main.parse_mention_command("<@UBOT> stop 3일") == ("stop", 3)
    assert main.parse_mention_command("<@UBOT> 정지 5 days") == ("stop", 5)


def test_parse_stop_typo_is_invalid_not_indefinite():
    # 오타가 무기한 정지로 새면 봇이 영구 정지된다. 이 테스트가 유일한 방어선이다.
    assert main.parse_mention_command("<@UBOT> stop tomorow") == ("stop_invalid", None)


def test_parse_stop_malformed_date_passes_through_to_handler():
    # 실재 여부 검증은 호출부(핸들러) 담당 — 기존 파서가 음수를 그대로 넘기는 것과 같은 분담
    assert main.parse_mention_command("<@UBOT> stop 2026-13-99") == ("stop", "2026-13-99")


def test_parse_start_and_korean_alias():
    assert main.parse_mention_command("<@UBOT> start") == ("start", None)
    assert main.parse_mention_command("<@UBOT> 재개") == ("start", None)


def test_existing_commands_not_hijacked_by_new_branches():
    assert main.parse_mention_command("<@UBOT> help") == ("help", None)
    assert main.parse_mention_command("<@UBOT> config") == ("config_show", None)
    assert main.parse_mention_command("<@UBOT> config --default=8") == ("config_set", 8)
    assert main.parse_mention_command("<@UBOT> 질문 3") == ("question", 3)


def test_build_help_text_lists_pause_commands():
    text = main.build_help_text()
    assert "stop" in text
    assert "start" in text
    assert "2026-08-10" in text          # 날짜 지정 예시
    assert "1~30" in text                # LIMITS["pause_days"] 기반


def test_build_help_text_pause_range_follows_limits(monkeypatch):
    # 범위 숫자가 하드코딩으로 되돌아가면 여기서 잡힌다
    import state
    monkeypatch.setitem(state.LIMITS, "pause_days", (1, 20))
    assert "1~20" in main.build_help_text()
