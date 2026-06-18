import main


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
    assert main.parse_question_id("오늘의 질문 [Q016] 입니다") == "Q016"


def test_parse_question_id_ignores_bare_text():
    # 대괄호 없는 Q001은 무시 (오탐 방지)
    assert main.parse_question_id("Q001 이 뭔가요?") is None


def test_parse_question_id_none_when_absent():
    assert main.parse_question_id("ID 없는 메시지") is None


def test_next_question_ids_continues_from_max(sample_readme):
    # sample_readme의 최대 ID는 Q002 → 다음은 Q003~Q005
    assert main.next_question_ids(sample_readme, 3) == ["Q003", "Q004", "Q005"]


def test_next_question_ids_from_empty():
    assert main.next_question_ids("# 제목\n## CS\n", 2) == ["Q001", "Q002"]


def test_next_question_ids_only_scans_headers():
    # 본문/코드블록의 [Q999]는 무시, 헤더 라인만 스캔
    readme = "## CS\n- **[Q005] Q. 질문** _(d)_\n  본문에 [Q999] 언급\n"
    assert main.next_question_ids(readme, 1) == ["Q006"]


