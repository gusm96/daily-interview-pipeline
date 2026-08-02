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
