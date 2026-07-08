import storage
from scripts.migrate_readme import build_migration

OLD = (
    "# daily-interview-pipeline\n설명\n\n"
    "## 🖥️ CS (네트워크/OS)\n\n"
    "- <details><summary><b>[Q001]</b> TCP vs UDP <i>(2026-06-17)</i></summary>\n\n"
    "  **Q.** TCP와 UDP 차이는?\n\n  ### 🧑‍💻 나의 답변\n  연결지향 여부.\n\n"
    "  ### 🤖 AI 피드백\n  좋음.\n  </details>\n\n"
    "- <details><summary><b>[Q002]</b> OSI <i>(2026-06-17)</i></summary>\n\n"
    "  **Q.** OSI 7계층?\n\n  ### 🧑‍💻 나의 답변\n\n  ### 🤖 AI 피드백\n\n  </details>\n\n"
    "## ☕ Java\n\n"
)


def test_migration_creates_question_files():
    files = build_migration(OLD)
    assert "CS/Q001.md" in files and "CS/Q002.md" in files
    q1 = storage.parse_question_file(files["CS/Q001.md"])
    assert q1.answer == "연결지향 여부." and q1.answered is True
    q2 = storage.parse_question_file(files["CS/Q002.md"])
    assert q2.answered is False and q2.ai_auto is False


def test_migration_creates_index_and_readme():
    files = build_migration(OLD)
    assert "CS/CS.md" in files
    assert "총 2개" in files["CS/CS.md"]
    assert "README.md" in files
    assert "<!-- questions:CS:start -->" in files["README.md"]


def _old_block(qid, date, title="제목"):
    return (
        f"- <details><summary><b>[{qid}]</b> {title} <i>({date})</i></summary>\n\n"
        f"  **Q.** 질문?\n\n  ### 🧑‍💻 나의 답변\n  답변\n\n"
        f"  ### 🤖 AI 피드백\n  피드백\n  </details>\n\n"
    )


def test_migration_keeps_only_top_n_per_category_in_readme():
    old = "## 🖥️ CS (네트워크/OS)\n\n" + "".join(
        _old_block(f"Q{i:03d}", f"2026-06-{i:02d}") for i in range(1, 8)  # Q001~Q007, 7개
    )
    files = build_migration(old)
    readme = files["README.md"]
    for qid in ["Q003", "Q004", "Q005", "Q006", "Q007"]:
        assert f"q {qid}" in readme
    for qid in ["Q001", "Q002"]:
        assert f"q {qid}" not in readme
        assert f"CS/{qid}.md" in files  # 문제 파일엔 영구 보존


# 나의 답변이 AI 자동 태그로 시작하는 문제(미응시 자동 모범답안)
OLD_AI_AUTO = (
    "## 🖥️ CS (네트워크/OS)\n\n"
    "- <details><summary><b>[Q003]</b> 캐시 <i>(2026-07-04)</i></summary>\n\n"
    "  **Q.** 캐시란?\n\n  ### 🧑‍💻 나의 답변\n"
    f"  {storage.AI_AUTO_TAG}\n  캐시는 임시 저장소입니다.\n\n"
    "  ### 🤖 AI 피드백\n  (AI 자동 작성 - 검토 필요)\n  </details>\n\n"
)


def test_migration_detects_ai_auto_and_strips_tag():
    files = build_migration(OLD_AI_AUTO)
    q = storage.parse_question_file(files["CS/Q003.md"])
    assert q.ai_auto is True
    assert q.answered is False
    assert storage.AI_AUTO_TAG not in q.answer
    assert "캐시는 임시 저장소입니다." in q.answer


# 실제 README는 <details> 리스트 항목 안에 있어 답변/피드백 각 줄에 2칸 들여쓰기가 붙는다
OLD_INDENTED = (
    "## 🖥️ CS (네트워크/OS)\n\n"
    "- <details><summary><b>[Q004]</b> 스레드 풀 <i>(2026-07-05)</i></summary>\n\n"
    "  **Q.** 스레드 풀이란?\n\n  ### 🧑‍💻 나의 답변\n"
    "  ## 핵심 정의\n  \n  스레드 풀은 재사용 가능한 스레드 집합입니다.\n\n"
    "  ### 🤖 AI 피드백\n  총평입니다.\n  \n  세부 피드백입니다.\n  </details>\n\n"
)


def test_migration_dedents_indented_answer_and_feedback():
    files = build_migration(OLD_INDENTED)
    q = storage.parse_question_file(files["CS/Q004.md"])
    assert q.answer == "## 핵심 정의\n\n스레드 풀은 재사용 가능한 스레드 집합입니다."
    assert q.feedback == "총평입니다.\n\n세부 피드백입니다."
