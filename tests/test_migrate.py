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
    files = build_migration(OLD, "2026-07-05")
    assert "CS/Q001.md" in files and "CS/Q002.md" in files
    q1 = storage.parse_question_file(files["CS/Q001.md"])
    assert q1.answer == "연결지향 여부." and q1.answered is True
    q2 = storage.parse_question_file(files["CS/Q002.md"])
    assert q2.answered is False and q2.ai_auto is False


def test_migration_creates_index_and_readme():
    files = build_migration(OLD, "2026-07-05")
    assert "CS/CS.md" in files
    assert "총 2개" in files["CS/CS.md"]
    assert "README.md" in files
    assert "<!-- questions:start -->" in files["README.md"]


def test_migration_prunes_readme_to_window():
    # 2026-06-17 문제는 2026-07-05 기준 7일 창 밖 → README에는 없음(파일엔 존재)
    files = build_migration(OLD, "2026-07-05")
    assert "q Q001" not in files["README.md"]
    assert "CS/Q001.md" in files


# 나의 답변이 AI 자동 태그로 시작하는 문제(미응시 자동 모범답안)
OLD_AI_AUTO = (
    "## 🖥️ CS (네트워크/OS)\n\n"
    "- <details><summary><b>[Q003]</b> 캐시 <i>(2026-07-04)</i></summary>\n\n"
    "  **Q.** 캐시란?\n\n  ### 🧑‍💻 나의 답변\n"
    f"  {storage.AI_AUTO_TAG}\n  캐시는 임시 저장소입니다.\n\n"
    "  ### 🤖 AI 피드백\n  (AI 자동 작성 - 검토 필요)\n  </details>\n\n"
)


def test_migration_detects_ai_auto_and_strips_tag():
    files = build_migration(OLD_AI_AUTO, "2026-07-05")
    q = storage.parse_question_file(files["CS/Q003.md"])
    assert q.ai_auto is True
    assert q.answered is False
    assert storage.AI_AUTO_TAG not in q.answer
    assert "캐시는 임시 저장소입니다." in q.answer
