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


def _q(**kw):
    base = dict(id="Q001", slug="CS", category=storage.CATEGORY_SLUGS and
               storage.category_for_slug("CS"), title="TCP 흐름제어",
               date="2026-07-05", question="흐름제어와 혼잡제어의 차이는?")
    base.update(kw)
    return storage.Question(**base)


def test_question_file_roundtrip_unanswered():
    q = _q()
    parsed = storage.parse_question_file(storage.render_question_file(q))
    assert parsed == q


def test_question_file_roundtrip_answered_multiline():
    q = _q(answer="답변 라인1\n답변 라인2", feedback="피드백1\n피드백2",
           answered=True)
    parsed = storage.parse_question_file(storage.render_question_file(q))
    assert parsed == q


def test_question_file_roundtrip_ai_auto():
    q = _q(answer="AI 모범답안", feedback="(AI 자동 작성 - 검토 필요)", ai_auto=True)
    parsed = storage.parse_question_file(storage.render_question_file(q))
    assert parsed == q


def test_question_file_has_meta_and_heading():
    text = storage.render_question_file(_q())
    assert text.startswith("<!-- meta id=Q001 slug=CS date=2026-07-05 "
                           "answered=false ai_auto=false -->")
    assert "# [Q001] TCP 흐름제어" in text
    assert "**Q.** 흐름제어와 혼잡제어의 차이는?" in text
