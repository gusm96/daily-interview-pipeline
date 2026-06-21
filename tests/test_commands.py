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
