"""기존 README(단일 파일 누적) → 파일 기반 구조 일회성 마이그레이션.

build_migration()은 순수 함수(파일 dict 생성). __main__ 실행부는 이를 github_commit_files로 반영.
"""
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage  # noqa: E402

# 구 README 블록 파서: 카테고리 섹션 + 문제 블록
_SECTION_RE = re.compile(r"^##\s+(?P<cat>.+)$", re.MULTILINE)
_BLOCK_RE = re.compile(
    r"<summary><b>\[(?P<id>Q\d{3,})\]</b>\s*(?P<title>.*?)\s*<i>\((?P<date>[\d-]+)\)</i></summary>"
    r".*?\*\*Q\.\*\*\s*(?P<question>.*?)\s*"
    r"### 🧑‍💻 나의 답변\s*(?P<answer>.*?)\s*"
    r"### 🤖 AI 피드백\s*(?P<feedback>.*?)\s*</details>",
    re.DOTALL,
)
_AI_TAG = storage.AI_AUTO_TAG


def _dedent_block(text):
    """구 README `<details>` 리스트 항목 안 2칸 들여쓰기를 줄 단위로 제거."""
    lines = [line[2:] if line.startswith("  ") else line for line in text.split("\n")]
    return "\n".join(lines).strip()


def _parse_old(old_readme):
    """구 README → [Question]. 카테고리는 직전 ## 섹션 헤더로 판정."""
    # 각 블록의 시작 위치에서 가장 가까운 앞선 섹션 헤더를 카테고리로 사용
    sections = [(m.start(), m.group("cat").strip()) for m in _SECTION_RE.finditer(old_readme)]
    questions = []
    for m in _BLOCK_RE.finditer(old_readme):
        cat = ""
        for pos, name in sections:
            if pos < m.start():
                cat = name
            else:
                break
        if cat not in storage.CATEGORY_SLUGS:
            continue
        answer = _dedent_block(m.group("answer"))
        ai_auto = answer.startswith(_AI_TAG)
        if ai_auto:
            answer = answer[len(_AI_TAG):].strip()
        questions.append(storage.Question(
            id=m.group("id"), slug=storage.slug_for(cat), category=cat,
            title=m.group("title").strip(), date=m.group("date"),
            question=m.group("question").strip(),
            answer=answer,
            feedback=_dedent_block(m.group("feedback")),
            answered=bool(answer) and not ai_auto,
            ai_auto=ai_auto,
        ))
    return questions


def build_migration(old_readme, today_iso):
    """구 README → {path: content}. 문제 파일 + 카테고리 인덱스 + 창 적용 README."""
    questions = _parse_old(old_readme)
    files = {}
    indexes = {}  # slug -> index_text
    for q in questions:
        files[f"{q.slug}/{q.id}.md"] = storage.render_question_file(q)
        indexes[q.slug] = storage.upsert_index_row(
            indexes.get(q.slug, ""), q.slug, q.category, q.id, q.title, q.date,
            storage.status_label(q))
    for slug, text in indexes.items():
        files[f"{slug}/{slug}.md"] = text

    readme = storage.EMPTY_README
    cutoff = (datetime.fromisoformat(today_iso)
              - timedelta(days=storage.README_WINDOW_DAYS - 1)).date().isoformat()
    for q in sorted(questions, key=lambda x: (x.date, x.id)):  # 오래된→최신 삽입 = 최신 먼저
        if q.date >= cutoff:
            readme = storage.insert_toggle(readme, storage.build_readme_toggle(q))
    files["README.md"] = readme
    return files


if __name__ == "__main__":
    import main
    old, _ = main.github_get_file("README.md")
    files = build_migration(old or "", main.today_kst_iso())
    print(f"마이그레이션 대상 파일 {len(files)}개")
    for path in sorted(files):
        print(" -", path)
    if "--commit" in sys.argv:
        main.github_commit_files(files, "chore: 파일 기반 저장소로 마이그레이션")
        print("커밋 완료")
    else:
        print("(dry-run) 실제 반영하려면 --commit 플래그 추가")
