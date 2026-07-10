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


def test_top_n_per_category_is_five():
    assert storage.README_TOP_N_PER_CATEGORY == 5


def test_empty_readme_has_category_sections_and_links():
    r = storage.EMPTY_README
    assert "최근 7일 문제" not in r
    assert "카테고리별 전체 문제" not in r  # 상단 요약 링크 삭제됨
    for slug in storage.SLUGS:
        category = storage.category_for_slug(slug)
        assert f"## {category}" in r
        assert f"<!-- questions:{slug}:start -->" in r
        assert f"<!-- questions:{slug}:end -->" in r
        assert f"📄 [{slug} 모든 문제 보기](./{slug}/{slug}.md)" in r
    assert r.count("(이번 주 등록된 문제 없음)") == len(storage.SLUGS)


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


def test_render_question_file_shows_ai_auto_tag():
    # 자동 모범답안임을 눈에 보이게 표시해야 "나의 답변"으로 오인되지 않는다
    q = _q(answer="AI 모범답안", feedback="(AI 자동 작성 - 검토 필요)", ai_auto=True)
    text = storage.render_question_file(q)
    assert storage.AI_AUTO_TAG in text
    parsed = storage.parse_question_file(text)
    assert storage.AI_AUTO_TAG not in parsed.answer  # Question.answer 필드 자체는 태그 없이 깨끗


def test_render_question_file_no_tag_when_answered_by_human():
    q = _q(answer="내가 직접 쓴 답변", answered=True)
    assert storage.AI_AUTO_TAG not in storage.render_question_file(q)


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


def test_existing_titles_block_lists_all_titles_across_categories():
    java = storage.upsert_index_row("", "Java", storage.category_for_slug("Java"),
                                    "Q037", "옛날 Java 제목", "2026-07-03", "🤖 자동답안")
    java = storage.upsert_index_row(java, "Java", storage.category_for_slug("Java"),
                                    "Q062", "최신 Java 제목", "2026-07-08", "⬜ 미답변")
    cs = storage.upsert_index_row("", "CS", storage.category_for_slug("CS"),
                                  "Q001", "CS 제목", "2026-07-01", "🤖 자동답안")
    block = storage.existing_titles_block({"Java": java, "CS": cs})
    # 오래돼서 README 윈도우 밖으로 밀려난 제목도 반드시 포함
    assert "옛날 Java 제목" in block
    assert "최신 Java 제목" in block
    assert "CS 제목" in block


def test_normalize_title_ignores_case_space_punctuation():
    a = storage.normalize_title("Java GC 동작 원리와 튜닝!")
    b = storage.normalize_title("java gc 동작원리와, 튜닝")
    assert a == b


def test_normalize_title_distinguishes_different_titles():
    assert storage.normalize_title("Java GC 동작 원리") != storage.normalize_title("Java Stream API")


def test_existing_titles_returns_flat_list_across_categories():
    java = storage.upsert_index_row("", "Java", storage.category_for_slug("Java"),
                                    "Q037", "옛날 Java 제목", "2026-07-03", "🤖 자동답안")
    cs = storage.upsert_index_row("", "CS", storage.category_for_slug("CS"),
                                  "Q001", "CS 제목", "2026-07-01", "🤖 자동답안")
    titles = storage.existing_titles({"Java": java, "CS": cs})
    assert "옛날 Java 제목" in titles
    assert "CS 제목" in titles


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
    # 카테고리 섹션 안에 있으므로 요약에 카테고리를 반복하지 않고 [ID] 제목 (날짜)만 표시
    assert "<summary><b>[Q015]</b> TCP 흐름제어 <i>(2026-07-05)</i></summary>" in tog
    assert "🖥️ CS (네트워크/OS)" not in tog
    assert "📄 [전체 보기](./CS/Q015.md)" in tog
    assert tog.rstrip().endswith("</details>")


def test_build_readme_toggle_shows_ai_auto_tag():
    q = _q(id="Q015", answer="AI 모범답안", feedback="(AI 자동 작성 - 검토 필요)", ai_auto=True)
    tog = storage.build_readme_toggle(q)
    assert storage.AI_AUTO_TAG in tog


def test_insert_toggle_places_newest_first():
    r = storage.EMPTY_README
    r = storage.insert_toggle(r, storage.build_readme_toggle(_q(id="Q001")))
    r = storage.insert_toggle(r, storage.build_readme_toggle(_q(id="Q002")))
    # 최신(Q002)이 start 마커 바로 아래 = Q001보다 위
    assert r.index("q Q002") < r.index("q Q001")
    assert "<!-- questions:CS:start -->" in r and "<!-- questions:CS:end -->" in r


def test_insert_toggle_replaces_empty_placeholder_in_own_category_only():
    r = storage.EMPTY_README
    r2 = storage.insert_toggle(r, storage.build_readme_toggle(_q(id="Q001")))  # slug=CS
    cs_start = r2.index("<!-- questions:CS:start -->")
    cs_end = r2.index("<!-- questions:CS:end -->")
    assert "이번 주 등록된 문제 없음" not in r2[cs_start:cs_end]
    # 다른(Java 등) 카테고리 섹션엔 플레이스홀더가 그대로 남아있음
    java_start = r2.index("<!-- questions:Java:start -->")
    java_end = r2.index("<!-- questions:Java:end -->")
    assert "이번 주 등록된 문제 없음" in r2[java_start:java_end]


def test_insert_toggle_inserts_into_matching_category_from_marker():
    r = storage.EMPTY_README
    java_q = _q(id="Q009", slug="Java", category=storage.category_for_slug("Java"))
    r2 = storage.insert_toggle(r, storage.build_readme_toggle(java_q))
    java_start = r2.index("<!-- questions:Java:start -->")
    java_end = r2.index("<!-- questions:Java:end -->")
    assert "q Q009" in r2[java_start:java_end]


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
    assert title == "TCP 흐름제어"
    assert question == "흐름제어와 혼잡제어의 차이는?"


def test_scan_window_excludes_ai_auto():
    r = _readme_with(_q(id="Q001"))
    r = storage.patch_toggle_body(r, "Q001", "AI답", "(AI 자동 작성 - 검토 필요)", ai_auto=True)
    assert storage.scan_window_unanswered(r) == []


def test_prune_overflow_keeps_top_n_per_category():
    r = storage.EMPTY_README
    for i in range(1, 8):  # Q001~Q007, 카테고리당 상한(5) 초과
        q = _q(id=f"Q{i:03d}", date=f"2026-07-{i:02d}")
        r = storage.insert_toggle(r, storage.build_readme_toggle(q))
    r2 = storage.prune_overflow(r, limit=5)
    for qid in ["Q003", "Q004", "Q005", "Q006", "Q007"]:
        assert f"q {qid}" in r2
    for qid in ["Q001", "Q002"]:
        assert f"q {qid}" not in r2


def test_prune_overflow_restores_placeholder_when_category_empty():
    r = storage.insert_toggle(storage.EMPTY_README, storage.build_readme_toggle(_q(id="Q001")))
    r2 = storage.prune_overflow(r, limit=0)
    cs_start = r2.index("<!-- questions:CS:start -->")
    cs_end = r2.index("<!-- questions:CS:end -->")
    assert "이번 주 등록된 문제 없음" in r2[cs_start:cs_end]
    assert "q Q001" not in r2


def test_prune_overflow_other_categories_untouched():
    r = storage.EMPTY_README
    java_q = _q(id="Q100", slug="Java", category=storage.category_for_slug("Java"))
    r = storage.insert_toggle(r, storage.build_readme_toggle(java_q))
    for i in range(1, 8):  # CS 7개(초과)
        r = storage.insert_toggle(r, storage.build_readme_toggle(_q(id=f"Q{i:03d}")))
    r2 = storage.prune_overflow(r, limit=5)
    assert "q Q100" in r2  # Java는 1개뿐이라 안 잘림


def test_marker_info_and_has_toggle():
    r = _readme_with(_q(id="Q007", date="2026-07-04"))
    assert storage.marker_info(r, "Q007") == ("CS", "2026-07-04")
    assert storage.marker_info(r, "Q999") is None
    assert storage.has_toggle(r, "Q007") is True
    assert storage.has_toggle(r, "Q999") is False


def test_build_readme_window_keeps_top_n_per_category():
    qs = [_q(id=f"Q{i:03d}", date=f"2026-07-{i:02d}") for i in range(1, 8)]  # CS 7개
    r = storage.build_readme_window(qs, limit=5)
    for qid in ["Q003", "Q004", "Q005", "Q006", "Q007"]:
        assert f"q {qid}" in r
    for qid in ["Q001", "Q002"]:
        assert f"q {qid}" not in r


def test_build_readme_window_separates_by_category():
    qs = [_q(id="Q001"),
          _q(id="Q002", slug="Java", category=storage.category_for_slug("Java"))]
    r = storage.build_readme_window(qs)
    assert "q Q001" in r and "q Q002" in r
