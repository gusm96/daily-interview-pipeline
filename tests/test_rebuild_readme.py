import storage
from scripts.rebuild_readme import build_readme_files, _qids_from_index


def _q(**kw):
    base = dict(id="Q001", slug="CS", category=storage.category_for_slug("CS"),
               title="TCP 흐름제어", date="2026-07-05", question="질문?")
    base.update(kw)
    return storage.Question(**base)


def test_build_readme_files_returns_readme_only():
    files = build_readme_files([_q()])
    assert list(files.keys()) == ["README.md"]
    assert "q Q001" in files["README.md"]


def test_build_readme_files_keeps_top_n_per_category():
    qs = [_q(id=f"Q{i:03d}", date=f"2026-07-{i:02d}") for i in range(1, 8)]  # CS 7개
    files = build_readme_files(qs)
    readme = files["README.md"]
    for qid in ["Q003", "Q004", "Q005", "Q006", "Q007"]:
        assert f"q {qid}" in readme
    for qid in ["Q001", "Q002"]:
        assert f"q {qid}" not in readme


def test_qids_from_index_dedupes_bracket_and_filename_matches():
    # 인덱스 행 하나에 "[Q061]"와 "Q061.md" 두 곳에 qid가 등장 -> 한 번만 잡혀야 함
    idx = "| [Q061](./Q061.md) | 제목 | 2026-07-08 | ⬜ 미답변 |\n"
    assert _qids_from_index(idx) == ["Q061"]


def test_qids_from_index_preserves_order_multiple_rows():
    idx = ("| [Q061](./Q061.md) | a | 2026-07-08 | ⬜ 미답변 |\n"
           "| [Q056](./Q056.md) | b | 2026-07-07 | 🤖 자동답안 |\n")
    assert _qids_from_index(idx) == ["Q061", "Q056"]
