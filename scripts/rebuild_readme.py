"""문제 파일 + 카테고리 인덱스로부터 README를 처음부터 재생성(드리프트 복구용 정식 도구).

README/인덱스는 파생 뷰이고 문제 파일이 원본이라는 원칙에 따라, 필요할 때 언제든
README만 원본에서 다시 만들 수 있어야 한다는 설계 목표를 지원한다.

build_readme_files()는 순수 함수. __main__ 실행부는 GitHub에서 조회해 github_commit_files로 반영.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import storage  # noqa: E402


def build_readme_files(questions):
    """전체 Question 목록 → {"README.md": 카테고리별 상위 N개로 재구성된 README}."""
    return {"README.md": storage.build_readme_window(questions)}


if __name__ == "__main__":
    import main

    questions = []
    for slug in storage.SLUGS:
        idx_text, _ = main.github_get_file(f"{slug}/{slug}.md")
        for qid in re.findall(r"Q\d{3,}", idx_text or ""):
            text, _ = main.github_get_file(f"{slug}/{qid}.md")
            if text:
                questions.append(storage.parse_question_file(text))

    files = build_readme_files(questions)
    print(f"재구성 대상 파일 {len(files)}개")
    for path in sorted(files):
        print(" -", path)
    if "--commit" in sys.argv:
        main.github_commit_files(files, "chore: README를 문제 파일 기준으로 재구성")
        print("커밋 완료")
    else:
        print("(dry-run) 실제 반영하려면 --commit 플래그 추가")
