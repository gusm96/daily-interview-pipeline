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


def test_upsert_index_creates_when_empty():
    out = storage.upsert_index_row("", "CS", storage.category_for_slug("CS"),
                                   "Q001", "TCP", "2026-07-05", "⬜ 미답변")
    assert "총 1개" in out
    assert "| [Q001](./Q001.md) | TCP | 2026-07-05 | ⬜ 미답변 |" in out


def test_upsert_index_appends_and_sorts_desc():
    t = storage.upsert_index_row("", "CS", storage.category_for_slug("CS"),
                                 "Q001", "A", "2026-07-01", "⬜ 미답변")
    t = storage.upsert_index_row(t, "CS", storage.category_for_slug("CS"),
                                 "Q003", "B", "2026-07-03", "⬜ 미답변")
    assert "총 2개" in t
    # Q003이 Q001보다 위(최신 먼저)
    assert t.index("Q003") < t.index("Q001")


def test_upsert_index_updates_existing_status_no_dup():
    t = storage.upsert_index_row("", "CS", storage.category_for_slug("CS"),
                                 "Q001", "A", "2026-07-01", "⬜ 미답변")
    t = storage.upsert_index_row(t, "CS", storage.category_for_slug("CS"),
                                 "Q001", "A", "2026-07-01", "✅ 답변완료")
    assert "총 1개" in t                 # 중복 추가 아님
    assert "✅ 답변완료" in t
    assert "⬜ 미답변" not in t


def test_next_question_ids_scans_multiple_indexes():
    idx1 = storage.upsert_index_row("", "CS", storage.category_for_slug("CS"),
                                    "Q005", "A", "d", "⬜ 미답변")
    idx2 = storage.upsert_index_row("", "Java", storage.category_for_slug("Java"),
                                    "Q009", "B", "d", "⬜ 미답변")
    assert storage.next_question_ids([idx1, idx2, ""], 2) == ["Q010", "Q011"]


def test_next_question_ids_from_empty():
    assert storage.next_question_ids(["", ""], 2) == ["Q001", "Q002"]


def test_toggle_has_marker_and_summary():
    q = _q(id="Q015")
    tog = storage.build_readme_toggle(q)
    assert tog.startswith("- <!-- q Q015 CS 2026-07-05 -->")
    assert "<summary><b>[Q015]</b> 🖥️ CS (네트워크/OS) | TCP 흐름제어 " \
           "<i>(2026-07-05)</i></summary>" in tog
    assert "📄 [전체 보기](./CS/Q015.md)" in tog
    assert tog.rstrip().endswith("</details>")


def test_insert_toggle_places_newest_first():
    r = storage.EMPTY_README
    r = storage.insert_toggle(r, storage.build_readme_toggle(_q(id="Q001")))
    r = storage.insert_toggle(r, storage.build_readme_toggle(_q(id="Q002")))
    # 최신(Q002)이 start 마커 바로 아래 = Q001보다 위
    assert r.index("q Q002") < r.index("q Q001")
    assert "<!-- questions:start -->" in r and "<!-- questions:end -->" in r


def _readme_with(*questions):
    r = storage.EMPTY_README
    for q in questions:
        r = storage.insert_toggle(r, storage.build_readme_toggle(q))
    return r


def test_patch_toggle_body_fills_answer_feedback():
    r = _readme_with(_q(id="Q001"))
    r2 = storage.patch_toggle_body(r, "Q001", "내 답변", "AI 피드백", ai_auto=False)
    assert "내 답변" in r2 and "AI 피드백" in r2
    # 마커/요약은 보존
    assert "q Q001 CS 2026-07-05" in r2


def test_patch_toggle_body_only_targets_qid():
    r = _readme_with(_q(id="Q001", question="질문1"), _q(id="Q002", question="질문2"))
    r2 = storage.patch_toggle_body(r, "Q002", "답변2", "피드백2", ai_auto=True)
    # Q001 토글 본문은 그대로(빈 답변 유지)
    seg = r2[r2.index("q Q001"):r2.index("q Q002") if r2.index("q Q001") < r2.index("q Q002") else len(r2)]
    assert "답변2" not in seg


def test_scan_window_unanswered_lists_empty_only():
    r = _readme_with(_q(id="Q001"), _q(id="Q002"))
    r = storage.patch_toggle_body(r, "Q001", "답함", "피드백", ai_auto=False)
    out = storage.scan_window_unanswered(r)
    ids = [t[0] for t in out]
    assert ids == ["Q002"]           # Q001은 답변 있음 → 제외
    qid, slug, date, title, question = out[0]
    assert slug == "CS" and date == "2026-07-05"
    assert question == "흐름제어와 혼잡제어의 차이는?"


def test_scan_window_excludes_ai_auto():
    r = _readme_with(_q(id="Q001"))
    r = storage.patch_toggle_body(r, "Q001", "AI답", "(AI 자동 작성 - 검토 필요)", ai_auto=True)
    assert storage.scan_window_unanswered(r) == []


def test_prune_expired_removes_old_toggles():
    r = _readme_with(_q(id="Q001", date="2026-06-01"), _q(id="Q009", date="2026-07-05"))
    r2 = storage.prune_expired(r, "2026-06-29")   # cutoff 이전 = 만료
    assert "q Q001" not in r2
    assert "q Q009" in r2


def test_marker_info_and_has_toggle():
    r = _readme_with(_q(id="Q007", date="2026-07-04"))
    assert storage.marker_info(r, "Q007") == ("CS", "2026-07-04")
    assert storage.marker_info(r, "Q999") is None
    assert storage.has_toggle(r, "Q007") is True
    assert storage.has_toggle(r, "Q999") is False
