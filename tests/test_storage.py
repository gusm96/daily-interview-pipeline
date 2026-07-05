import storage
from prompts import CATEGORIES


def test_every_category_has_slug():
    for c in CATEGORIES:
        assert storage.slug_for(c) in {"CS", "Java", "SpringBoot", "Database", "Etc"}


def test_slug_roundtrip():
    for c in CATEGORIES:
        assert storage.category_for_slug(storage.slug_for(c)) == c


def test_slug_for_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        storage.slug_for("없는 카테고리")


def test_status_label_priority():
    q = storage.Question("Q1", "CS", CATEGORIES[0], "t", "2026-07-05", "질문")
    assert storage.status_label(q) == "⬜ 미답변"
    q.ai_auto = True
    assert storage.status_label(q) == "🤖 자동답안"
    q.answered = True  # answered가 ai_auto보다 우선
    assert storage.status_label(q) == "✅ 답변완료"


def test_window_days_is_seven():
    assert storage.README_WINDOW_DAYS == 7
