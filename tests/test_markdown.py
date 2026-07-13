import main
import slack_client


def test_strip_outer_markdown_fence():
    text = "```markdown\n# Hello\n내용\n```"
    assert main.strip_markdown_fence(text) == "# Hello\n내용"


def test_strip_plain_fence():
    assert main.strip_markdown_fence("```\nhi\n```") == "hi"


def test_strip_preserves_inner_code_block():
    text = "```markdown\n답변\n```java\nint x;\n```\n```"
    # 최외곽 펜스만 제거, 내부 java 코드블록은 보존
    assert main.strip_markdown_fence(text) == "답변\n```java\nint x;\n```"


def test_strip_no_fence_returns_trimmed():
    assert main.strip_markdown_fence("  그냥 텍스트  ") == "그냥 텍스트"


def test_parse_question_id_bracket_anchor():
    assert slack_client.parse_question_id("[Q001] 제목") == "Q001"
    assert slack_client.parse_question_id("[Q999] 제목") == "Q999"
    assert slack_client.parse_question_id(" *[Q012]* 제목 ") == "Q012"


def test_parse_question_id_ignores_bare_text():
    assert slack_client.parse_question_id("Q001 제목") is None


def test_parse_question_id_none_when_absent():
    assert slack_client.parse_question_id(None) is None
    assert slack_client.parse_question_id("") is None
    assert slack_client.parse_question_id("제목만 있는 질문") is None
