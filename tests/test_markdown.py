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
    readme = "## CS\n- <details><summary><b>[Q005]</b> 질문 <i>(d)</i></summary>\n  본문에 [Q999] 언급\n"
    assert main.next_question_ids(readme, 1) == ["Q006"]


def test_build_question_block_format():
    block = main.build_question_block(
        "Q016", "TCP 3-way handshake", "TCP 3-way handshake를 설명하라.", "2026-06-18"
    )
    assert "<summary><b>[Q016]</b> TCP 3-way handshake <i>(2026-06-18)</i></summary>" in block
    assert "**Q.** TCP 3-way handshake를 설명하라." in block
    assert "<details>" in block
    assert "### 🧑‍💻 나의 답변" in block
    assert "### 🤖 AI 피드백" in block
    assert block.strip().endswith("</details>")


AI_TAG = "[⚠️ AI 자동 작성 답변 - 미응시]"


def test_find_unanswered_returns_empty_answer_only(sample_readme):
    # Q001=답변있음(제외), Q002=공백(포함)
    result = main.find_unanswered_questions(sample_readme)
    ids = [qid for qid, _ in result]
    assert "Q002" in ids
    assert "Q001" not in ids


def test_find_unanswered_includes_question_text(sample_readme):
    result = dict(main.find_unanswered_questions(sample_readme))
    assert result["Q002"] == "OSI 7계층을 설명하라."


def test_find_unanswered_excludes_ai_tagged():
    readme = (
        "## CS\n- <details><summary><b>[Q003]</b> 질문3 <i>(d)</i></summary>\n\n"
        "  **Q.** 질문3\n\n  ### 🧑‍💻 나의 답변\n"
        f"  {AI_TAG}\n  AI가 쓴 답.\n\n  ### 🤖 AI 피드백\n\n  </details>\n"
    )
    # AI 태그가 있으면 미답변으로 보지 않음 (재처리 제외)
    assert main.find_unanswered_questions(readme) == []


def test_update_answer_block_fills_and_returns_tuple(sample_readme):
    new_content, result = main.update_answer_block(
        sample_readme, "Q002", "내 답변입니다", "AI 피드백입니다"
    )
    assert result is None
    assert "내 답변입니다" in new_content
    assert "AI 피드백입니다" in new_content
    # 다른 질문(Q001)은 그대로
    assert "TCP는 연결지향이고" in new_content


def test_update_answer_block_removes_ai_tag():
    readme = (
        "## CS\n- <details><summary><b>[Q003]</b> 질문3 <i>(d)</i></summary>\n\n"
        "  **Q.** 질문3\n\n  ### 🧑‍💻 나의 답변\n"
        f"  {main.AI_AUTO_TAG}\n  AI가 쓴 답.\n\n  ### 🤖 AI 피드백\n\n  </details>\n"
    )
    new_content, _ = main.update_answer_block(readme, "Q003", "진짜 답변", "피드백")
    assert main.AI_AUTO_TAG not in new_content
    assert "진짜 답변" in new_content


def test_update_answer_block_missing_id_raises(sample_readme):
    import pytest
    with pytest.raises(ValueError):
        main.update_answer_block(sample_readme, "Q999", "x", "y")


def test_append_questions_assigns_ids_and_returns_them(sample_readme):
    questions = [
        ("🖥️ CS (네트워크/OS)", "제목A", "질문A"),
        ("☕ Java", "제목B", "질문B"),
    ]
    new_content, assigned = main.append_questions(questions, sample_readme, date_str="2026-06-18")
    # sample 최대 Q002 → 다음 Q003, Q004
    assert assigned == ["Q003", "Q004"]
    assert "<b>[Q003]</b> 제목A" in new_content
    assert "**Q.** 질문A" in new_content
    assert "<b>[Q004]</b> 제목B" in new_content
    assert "**Q.** 질문B" in new_content


def test_append_questions_creates_missing_section():
    readme = "# 제목\n## ☕ Java\n"
    questions = [("🖥️ CS (네트워크/OS)", "새 CS 제목", "새 CS 질문")]
    new_content, assigned = main.append_questions(questions, readme, date_str="2026-06-18")
    assert assigned == ["Q001"]
    assert "## 🖥️ CS (네트워크/OS)" in new_content
    assert "<b>[Q001]</b> 새 CS 제목" in new_content
    assert "**Q.** 새 CS 질문" in new_content


def test_append_questions_reapply_recomputes_ids():
    # 재적용(409) 시뮬레이션: 이미 Q003이 추가된 최신 content에 다시 append
    readme = "## ☕ Java\n- <details><summary><b>[Q003]</b> 기존 <i>(d)</i></summary>\n\n  **Q.** 기존\n\n  ### 🧑‍💻 나의 답변\n\n  ### 🤖 AI 피드백\n\n  </details>\n"
    questions = [("☕ Java", "제목B", "질문B")]
    _, assigned = main.append_questions(questions, readme, date_str="2026-06-18")
    assert assigned == ["Q004"]  # 최신 기준 재계산


def test_fill_unanswered_injects_tagged_answer(sample_readme):
    answer_map = {"Q002": "AI 모범답안 본문"}
    new_content, filled = main.fill_unanswered_questions(answer_map, sample_readme)
    assert filled == ["Q002"]
    assert main.AI_AUTO_TAG in new_content
    assert "AI 모범답안 본문" in new_content


def test_fill_unanswered_idempotent_skips_answered(sample_readme):
    # Q001은 이미 답변 있음 → 맵에 있어도 건너뜀(멱등)
    answer_map = {"Q001": "덮어쓰면 안됨", "Q002": "정상"}
    new_content, filled = main.fill_unanswered_questions(answer_map, sample_readme)
    assert filled == ["Q002"]
    assert "덮어쓰면 안됨" not in new_content
    assert "TCP는 연결지향이고" in new_content


def test_update_answer_block_multiline():
    readme = (
        "## CS\n- <details><summary><b>[Q003]</b> 질문3 <i>(d)</i></summary>\n\n"
        "  **Q.** 질문3\n\n  ### 🧑‍💻 나의 답변\n\n  ### 🤖 AI 피드백\n\n  </details>\n"
    )
    answer = "답변 라인1\n답변 라인2"
    feedback = "피드백 라인1\n피드백 라인2"
    new_content, _ = main.update_answer_block(readme, "Q003", answer, feedback)
    expected = (
        "## CS\n- <details><summary><b>[Q003]</b> 질문3 <i>(d)</i></summary>\n\n"
        "  **Q.** 질문3\n\n  ### 🧑‍💻 나의 답변\n  답변 라인1\n  답변 라인2\n\n  ### 🤖 AI 피드백\n  피드백 라인1\n  피드백 라인2\n\n  </details>\n"
    )
    assert new_content == expected


def test_update_answer_block_empty_and_blank_lines():
    readme = (
        "## CS\n- <details><summary><b>[Q003]</b> 질문3 <i>(d)</i></summary>\n\n"
        "  **Q.** 질문3\n\n  ### 🧑‍💻 나의 답변\n\n  ### 🤖 AI 피드백\n\n  </details>\n"
    )
    # 1. Empty strings should yield two spaces indentation on the blank line
    new_content, _ = main.update_answer_block(readme, "Q003", "", "")
    expected_empty = (
        "## CS\n- <details><summary><b>[Q003]</b> 질문3 <i>(d)</i></summary>\n\n"
        "  **Q.** 질문3\n\n  ### 🧑‍💻 나의 답변\n  \n\n  ### 🤖 AI 피드백\n  \n\n  </details>\n"
    )
    assert new_content == expected_empty

    # 2. Blank lines inside multiline strings should also be indented by 2 spaces
    answer = "답변1\n\n답변2"
    feedback = "피드백1\n\n피드백2"
    new_content, _ = main.update_answer_block(readme, "Q003", answer, feedback)
    expected_blank = (
        "## CS\n- <details><summary><b>[Q003]</b> 질문3 <i>(d)</i></summary>\n\n"
        "  **Q.** 질문3\n\n  ### 🧑‍💻 나의 답변\n  답변1\n  \n  답변2\n\n  ### 🤖 AI 피드백\n  피드백1\n  \n  피드백2\n\n  </details>\n"
    )
    assert new_content == expected_blank









