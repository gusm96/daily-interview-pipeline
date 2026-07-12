from unittest.mock import patch
import pytest
import main

REQUIRED = [
    "GITHUB_TOKEN", "REPO_OWNER", "REPO_NAME", "GEMINI_API_KEY",
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID", "SLACK_BOT_USER_ID",
]


def test_validate_env_passes_when_all_set(monkeypatch):
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    assert main.validate_env() == []


def test_validate_env_reports_missing(monkeypatch):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    missing = main.validate_env()
    assert "SLACK_BOT_USER_ID" in missing
    assert "GITHUB_TOKEN" not in missing


def test_generate_questions_returns_category_tuples(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    fake = ('[{"category":"🖥️ CS (네트워크/OS)","title":"T1","question":"Q1"},'
            '{"category":"☕ Java","title":"T2","question":"Q2"}]')
    with patch("main.call_gemini", return_value=fake):
        result = main.generate_questions("기존 readme")
    assert ("🖥️ CS (네트워크/OS)", "T1", "Q1") in result
    assert len(result) == 2


def test_generate_questions_passes_category_enum_schema(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    fake = '[{"category":"☕ Java","title":"T1","question":"Q1"}]'
    with patch("main.call_gemini", return_value=fake) as m:
        main.generate_questions("기존 readme")
    schema = m.call_args.kwargs["response_schema"]
    assert schema["items"]["properties"]["category"]["enum"] == main.CATEGORIES


def _fresh_readme_with_unanswered(qid="Q002"):
    import storage
    q = storage.Question(qid, "Java", storage.category_for_slug("Java"),
                         "OSI", "2026-07-05", "OSI란?")
    return storage.insert_toggle(storage.EMPTY_README, storage.build_readme_toggle(q))


def test_run_generate_routine_flow(monkeypatch):
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "call_gemini",
                        lambda p, temperature: "AI답안")            # 모범답안
    monkeypatch.setattr(main, "generate_questions",
                        lambda r, count=5: [("☕ Java", "제목1", "새 질문1"),
                                            ("🗄️ Database", "제목2", "새 질문2")])
    readme = _fresh_readme_with_unanswered("Q002")
    monkeypatch.setattr(main, "github_get_file",
                        lambda path: (readme, "s") if path == "README.md" else ("", None))
    committed = {}
    monkeypatch.setattr(main, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files, msg=message))
    monkeypatch.setattr(main, "today_kst_iso", lambda: "2026-07-06")

    main.run_generate_routine()

    files = committed["files"]
    # 신규 질문 2개 파일 + 각 인덱스 + README
    assert "Java/Q003.md" in files and "Database/Q004.md" in files
    assert "README.md" in files
    # 미답변 Q002 자동답안 반영(README 토글 + 문제 파일)
    assert "AI답안" in files["README.md"]
    assert "Java/Q002.md" in files
    # Slack에 신규 질문 2건, ID 포함
    assert len(posted) == 2 and any("Q003" in t for t in posted)


def test_run_generate_feeds_full_history_not_just_window(monkeypatch):
    """루틴 A는 README 윈도우(상위 5개) 밖의 과거 제목까지 중복방지 컨텍스트로 넘겨야 한다."""
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "AI답안")

    captured = {}

    def fake_generate(context, count=5):
        captured["context"] = context
        return [("☕ Java", "새 제목", "새 질문")]

    monkeypatch.setattr(main, "generate_questions", fake_generate)
    # 인덱스에는 과거 제목이 있지만 README에는 없음(윈도우 밖)
    java_index = storage.upsert_index_row(
        "", "Java", storage.category_for_slug("Java"),
        "Q037", "윈도우밖 과거제목", "2026-07-03", "🤖 자동답안")

    def fake_get(path):
        if path == "README.md":
            return storage.EMPTY_README, "s"
        if path == "Java/Java.md":
            return java_index, "s"
        return "", None

    monkeypatch.setattr(main, "github_get_file", fake_get)
    monkeypatch.setattr(main, "github_commit_files", lambda files, message, **kw: None)
    monkeypatch.setattr(main, "today_kst_iso", lambda: "2026-07-09")

    main.run_generate_routine()

    assert "윈도우밖 과거제목" in captured["context"]


def test_drop_duplicate_titles_rejects_normalized_match_with_existing():
    candidates = [("☕ Java", "JAVA GC 동작 원리와 튜닝!", "q1"),
                  ("🗄️ Database", "인덱스 자료구조", "q2")]
    kept = main.drop_duplicate_titles(candidates, ["Java GC 동작원리와 튜닝"])
    assert kept == [("🗄️ Database", "인덱스 자료구조", "q2")]


def test_drop_duplicate_titles_rejects_within_batch_duplicate():
    candidates = [("☕ Java", "Stream API 지연 평가", "q1"),
                  ("☕ Java", "stream api 지연평가", "q2"),
                  ("🗄️ Database", "인덱스 자료구조", "q3")]
    kept = main.drop_duplicate_titles(candidates, [])
    assert kept == [("☕ Java", "Stream API 지연 평가", "q1"),
                    ("🗄️ Database", "인덱스 자료구조", "q3")]


def test_run_generate_skips_candidates_duplicating_index_titles(monkeypatch):
    """생성 후보가 인덱스의 기존 제목과 (정규화 기준) 같으면 출제·전송에서 제외한다."""
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "AI답안")
    monkeypatch.setattr(main, "generate_questions",
                        lambda r, count=5: [("☕ Java", "옛날 java 제목", "재탕 질문"),
                                            ("🗄️ Database", "새 제목", "새 질문")])
    java_index = storage.upsert_index_row(
        "", "Java", storage.category_for_slug("Java"),
        "Q037", "옛날 Java 제목", "2026-07-03", "🤖 자동답안")

    def fake_get(path):
        if path == "README.md":
            return storage.EMPTY_README, "s"
        if path == "Java/Java.md":
            return java_index, "s"
        return "", None

    monkeypatch.setattr(main, "github_get_file", fake_get)
    committed = {}
    monkeypatch.setattr(main, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files))
    monkeypatch.setattr(main, "today_kst_iso", lambda: "2026-07-11")

    main.run_generate_routine()

    files = committed["files"]
    # 중복 후보는 제외되고 새 제목만 다음 ID(Q038)로 출제됨
    assert "Database/Q038.md" in files
    assert not any(p.startswith("Java/Q0") and p != "Java/Java.md" for p in files)
    assert len(posted) == 1 and "새 제목" in posted[0]


def test_run_generate_uses_and_clamps_config_default(monkeypatch):
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    captured = {}
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "AI답안")

    def fake_generate(r, count=5):
        captured["count"] = count
        return [("☕ Java", "t", "q")]

    monkeypatch.setattr(main, "generate_questions", fake_generate)
    readme = "<!-- config:default=50 -->\n" + storage.EMPTY_README
    monkeypatch.setattr(main, "github_get_file",
                        lambda path: (readme, "s") if path == "README.md" else ("", None))
    monkeypatch.setattr(main, "github_commit_files", lambda files, message, **kw: None)
    monkeypatch.setattr(main, "today_kst_iso", lambda: "2026-07-06")
    main.run_generate_routine()
    assert captured["count"] == 10     # 50 → 10 클램프


def test_handle_slack_event_grades_and_commits(monkeypatch):
    import storage
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    monkeypatch.setattr(main, "slack_get_thread_parent",
                        lambda ch, ts: "*[Q002] 🖥️ CS (네트워크/OS) | OSI 7계층*\nOSI 7계층을 설명하라.")
    monkeypatch.setattr(main, "call_gemini",
                        lambda p, temperature, response_schema=None, thinking_budget=0: "좋은 답변입니다")
    q = storage.Question("Q002", "CS", storage.category_for_slug("CS"),
                         "OSI 7계층", "2026-07-05", "OSI 7계층을 설명하라.")
    qfile = storage.render_question_file(q)
    readme = storage.insert_toggle(storage.EMPTY_README, storage.build_readme_toggle(q))

    def fake_get_file(path):
        if path == "README.md":
            return readme, "s"
        if path == "CS/Q002.md":
            return qfile, "s"
        if path == "CS/CS.md":
            return "", None
        return None, None

    committed = {}
    monkeypatch.setattr(main, "github_get_file", fake_get_file)
    monkeypatch.setattr(main, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files, msg=message))

    payload = {"event": {"type": "message", "user": "UHUMAN", "text": "OSI는 7계층입니다",
                         "thread_ts": "111.1", "ts": "111.2", "channel": "C1"}}
    main.handle_slack_event(payload)

    assert posted and posted[0][1] == "111.1"                  # 피드백은 스레드로
    files = committed["files"]
    assert "OSI는 7계층입니다" in files["CS/Q002.md"]           # 문제 파일 답변 반영
    assert "✅ 답변완료" in files["CS/CS.md"]                    # 인덱스 상태 갱신
    assert "OSI는 7계층입니다" in files["README.md"]            # README 토글 패치(창 안)


def test_handle_slack_event_persists_when_readme_toggle_malformed(monkeypatch):
    # 마커는 있으나 토글 본문이 손상돼 patch_toggle_body가 ValueError면,
    # README는 건너뛰되 문제 파일/인덱스 커밋은 유지되어야 한다(채점 유실 방지).
    import storage
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "slack_get_thread_parent",
                        lambda ch, ts: "*[Q002] 🖥️ CS (네트워크/OS) | OSI 7계층*\nOSI 7계층을 설명하라.")
    monkeypatch.setattr(main, "call_gemini",
                        lambda p, temperature, response_schema=None, thinking_budget=0: "피드백")
    q = storage.Question("Q002", "CS", storage.category_for_slug("CS"),
                         "OSI 7계층", "2026-07-05", "OSI 7계층을 설명하라.")
    qfile = storage.render_question_file(q)
    # 마커만 있고 '나의 답변' 구획이 없는 손상된 토글
    malformed = "- <!-- q Q002 CS 2026-07-05 --><details><summary>깨짐</summary>\n  본문\n  </details>"
    readme = storage.insert_toggle(storage.EMPTY_README, malformed)

    def fake_get_file(path):
        return {"README.md": (readme, "s"), "CS/Q002.md": (qfile, "s"),
                "CS/CS.md": ("", None)}.get(path, (None, None))

    committed = {}
    monkeypatch.setattr(main, "github_get_file", fake_get_file)
    monkeypatch.setattr(main, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files))

    payload = {"event": {"type": "message", "user": "UHUMAN", "text": "내 답변",
                         "thread_ts": "111.1", "ts": "111.2", "channel": "C1"}}
    main.handle_slack_event(payload)  # 크래시 없이 완료

    files = committed["files"]
    assert "내 답변" in files["CS/Q002.md"]     # 문제 파일은 커밋됨
    assert "CS/CS.md" in files                   # 인덱스도 커밋됨
    assert "README.md" not in files              # 손상 토글은 건너뜀


def test_handle_slack_event_passes_question_and_thinking_budget(monkeypatch):
    import storage
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "slack_get_thread_parent",
                        lambda ch, ts: "*[Q002] 🖥️ CS (네트워크/OS) | OSI 7계층*\nOSI 7계층을 설명하라.")
    captured = {}

    def fake_call_gemini(prompt, temperature, response_schema=None, thinking_budget=0):
        captured.update(prompt=prompt, thinking_budget=thinking_budget)
        return "좋은 답변입니다"

    q = storage.Question("Q002", "CS", storage.category_for_slug("CS"),
                         "OSI 7계층", "2026-07-05", "OSI 7계층을 설명하라.")
    monkeypatch.setattr(main, "call_gemini", fake_call_gemini)
    monkeypatch.setattr(main, "github_get_file",
                        lambda path: (storage.render_question_file(q), "s")
                        if path == "CS/Q002.md" else ("", None))
    monkeypatch.setattr(main, "github_commit_files", lambda files, message, **kw: None)

    payload = {"event": {"type": "message", "user": "UHUMAN", "text": "OSI는 7계층입니다",
                         "thread_ts": "111.1", "ts": "111.2", "channel": "C1"}}
    main.handle_slack_event(payload)
    assert "OSI 7계층을 설명하라." in captured["prompt"]
    assert "OSI는 7계층입니다" in captured["prompt"]
    assert captured["thinking_budget"] == main.FEEDBACK_THINKING_BUDGET


def test_handle_slack_event_ignores_bot(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    called = []
    monkeypatch.setattr(main, "call_gemini", lambda *a, **k: called.append(1))
    payload = {"event": {"type": "message", "user": "UBOT",
                         "text": "x", "thread_ts": "1", "ts": "2", "channel": "C1"}}
    main.handle_slack_event(payload)
    assert called == []  # 봇 메시지는 채점하지 않음


class FakeReq:
    def __init__(self, args=None, body=b"{}", headers=None, json_data=None):
        self.args = args or {}
        self._body = body
        self.headers = headers or {}
        self._json = json_data

    def get_data(self):
        return self._body

    def get_json(self, silent=False):
        return self._json


def test_entry_routes_generate(monkeypatch):
    called = []
    monkeypatch.setattr(main, "run_generate_routine", lambda: called.append("A"))
    req = FakeReq(args={"action": "generate"})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["A"]


def test_entry_url_verification_after_signature(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    req = FakeReq(json_data={"type": "url_verification", "challenge": "abc"})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert body == "abc"


def test_entry_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: False)
    req = FakeReq(json_data={"type": "event_callback"})
    body, status = main.daily_interview_bot(req)
    assert status == 401


def test_entry_retry_num_short_circuits(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(main, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(headers={"X-Slack-Retry-Num": "1"},
                  json_data={"type": "event_callback", "event": {}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == []  # 재시도는 즉시 200, 처리 안함


def test_entry_routes_event_callback(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(main, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(json_data={"type": "event_callback", "event": {"text": "x"}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["B"]


def test_handle_app_mention_help(monkeypatch):
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> help"})
    assert posted and "명령어" in posted[0][0]


def test_handle_app_mention_config_show(monkeypatch):
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "github_get_file",
                        lambda path: ("<!-- config:default=7 -->\n# r", "s"))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config"})
    assert "7" in posted[0]


def test_handle_app_mention_config_set_commits(monkeypatch):
    posted = []
    commits = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "github_get_file", lambda path: ("# r\n", "s"))
    monkeypatch.setattr(main, "github_commit_files",
                        lambda files, message, **kw: commits.append(message))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config --default=4"})
    assert commits  # 커밋 발생
    assert "4" in posted[-1]


def test_handle_app_mention_config_set_rejects_out_of_range(monkeypatch):
    posted = []
    called = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "github_commit_files",
                        lambda files, message, **kw: called.append(1))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config --default=99"})
    assert called == []  # 커밋 안 함
    assert "1~10" in posted[0]


def test_handle_app_mention_question_posts_top_level(monkeypatch):
    import storage
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    monkeypatch.setattr(main, "github_get_file",
                        lambda path: (storage.EMPTY_README, "s") if path == "README.md" else ("", None))
    monkeypatch.setattr(main, "generate_questions",
                        lambda r, count=5: [("☕ Java", "t", "q")] * count)
    monkeypatch.setattr(main, "github_commit_files", lambda files, message, **kw: None)
    monkeypatch.setattr(main, "today_kst_iso", lambda: "2026-07-06")
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> 질문 2", "thread_ts": "T1"})
    # 질문 메시지는 thread_ts=None(최상위)으로 전송
    q_msgs = [p for p in posted if p[0].startswith("*[Q")]
    assert q_msgs and all(p[1] is None for p in q_msgs)
    # 확인 메시지는 멘션 스레드(T1)로
    assert any(p[1] == "T1" and "추가" in p[0] for p in posted)


def test_handle_app_mention_question_does_not_call_grading(monkeypatch):
    # 명령 경로는 절대 모범답안/채점을 호출하지 않는다
    import storage
    gemini_calls = []
    monkeypatch.setattr(main, "call_gemini", lambda *a, **k: gemini_calls.append(1))
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "github_get_file",
                        lambda path: (storage.EMPTY_README, "s") if path == "README.md" else ("", None))
    monkeypatch.setattr(main, "generate_questions", lambda r, count=5: [("☕ Java", "t", "q")])
    monkeypatch.setattr(main, "github_commit_files", lambda files, message, **kw: None)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> 질문 1"})
    # find_unanswered/fill 경로를 타지 않으므로 call_gemini는 호출되지 않음
    # (generate_questions를 모킹했으므로 내부 call_gemini도 없음)
    assert gemini_calls == []


def test_handle_app_mention_unknown(monkeypatch):
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> 안녕"})
    assert "help" in posted[0]


def test_entry_routes_app_mention(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(main, "handle_app_mention", lambda e: called.append("M"))
    monkeypatch.setattr(main, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(json_data={"type": "event_callback",
                             "event": {"type": "app_mention", "text": "<@UBOT> help"}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["M"]  # 멘션은 handle_app_mention으로


def test_entry_message_still_routes_to_slack_event(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(main, "handle_app_mention", lambda e: called.append("M"))
    monkeypatch.setattr(main, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(json_data={"type": "event_callback",
                             "event": {"type": "message", "text": "x"}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["B"]  # 일반 메시지는 기존 경로 유지


def test_entry_routine_a_failure_notifies_slack(monkeypatch):
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setattr(main, "run_generate_routine",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((ch, text)))
    req = FakeReq(args={"action": "generate"})
    body, status = main.daily_interview_bot(req)
    assert status == 500
    assert posted and posted[0][0] == "C1" and "실패" in posted[0][1]


def test_handle_app_mention_ignores_bot(monkeypatch):
    # 봇/자기 메시지로 들어온 멘션 이벤트는 아무것도 처리하지 않음
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    main.handle_app_mention({"channel": "C1", "user": "UBOT", "text": "<@UBOT> help"})
    assert posted == []


def test_is_authorized_user_no_restriction_when_unset(monkeypatch):
    monkeypatch.delenv("SLACK_ALLOWED_USER_IDS", raising=False)
    assert main.is_authorized_user({"user": "UANY"}) is True


def test_is_authorized_user_enforces_whitelist(monkeypatch):
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN, UOWNER")
    assert main.is_authorized_user({"user": "UADMIN"}) is True
    assert main.is_authorized_user({"user": "UHACKER"}) is False


def test_handle_app_mention_question_blocked_for_unauthorized(monkeypatch):
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    posted = []
    called = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "generate_questions",
                        lambda r, count=5: called.append(1) or [])
    main.handle_app_mention({"channel": "C1", "user": "UHACKER", "text": "<@UBOT> 질문 3"})
    assert called == []        # 생성 시도조차 하지 않음
    assert "권한" in posted[0]


def test_handle_app_mention_config_set_blocked_for_unauthorized(monkeypatch):
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    posted = []
    commits = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "github_commit_files",
                        lambda files, message, **kw: commits.append(message))
    main.handle_app_mention({"channel": "C1", "user": "UHACKER",
                             "text": "<@UBOT> config --default=4"})
    assert commits == []       # 커밋 안 함
    assert "권한" in posted[0]


def test_handle_app_mention_help_blocked_for_unauthorized(monkeypatch):
    # 전체 잠금: 읽기/도움말도 비등록 사용자에겐 거부
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    main.handle_app_mention({"channel": "C1", "user": "UHACKER", "text": "<@UBOT> help"})
    assert "권한" in posted[0]   # 도움말 대신 권한 안내


def test_handle_app_mention_question_allowed_for_authorized(monkeypatch):
    import storage
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "github_get_file",
                        lambda path: (storage.EMPTY_README, "s") if path == "README.md" else ("", None))
    seen = []
    monkeypatch.setattr(main, "generate_questions",
                        lambda r, count=5: seen.append(count) or [("☕ Java", "t", "q")])
    monkeypatch.setattr(main, "github_commit_files", lambda files, message, **kw: None)
    main.handle_app_mention({"channel": "C1", "user": "UADMIN", "text": "<@UBOT> 질문 1"})
    assert seen == [1]


def test_handle_app_mention_help_allowed_for_authorized(monkeypatch):
    # 등록 사용자는 help 정상 동작
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    main.handle_app_mention({"channel": "C1", "user": "UADMIN", "text": "<@UBOT> help"})
    assert "명령어" in posted[0]


def test_today_kst_iso_at_0700_kst_returns_same_kst_day(monkeypatch):
    # 2026-06-29 22:30 UTC == 2026-06-30 07:30 KST → 날짜는 06-30 이어야 한다(전날 아님)
    fixed = main.datetime(2026, 6, 30, 7, 30, tzinfo=main.KST)
    monkeypatch.setattr(main, "_now_kst", lambda: fixed)
    assert main.today_kst_iso() == "2026-06-30"


def test_run_generate_caps_unanswered_fill_calls(monkeypatch):
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    # README에 미답변 20개
    r = storage.EMPTY_README
    for i in range(1, 21):
        q = storage.Question(f"Q{i:03d}", "CS", storage.category_for_slug("CS"),
                             f"t{i}", "2026-07-05", f"질문{i}")
        r = storage.insert_toggle(r, storage.build_readme_toggle(q))
    monkeypatch.setattr(main, "github_get_file",
                        lambda path: (r, "s") if path == "README.md" else ("", None))
    monkeypatch.setattr(main, "generate_questions", lambda c, count=5: [("☕ Java", "t", "q")])
    monkeypatch.setattr(main, "github_commit_files", lambda files, message, **kw: None)
    monkeypatch.setattr(main, "slack_post_message", lambda *a, **k: None)
    monkeypatch.setattr(main, "today_kst_iso", lambda: "2026-07-06")
    calls = {"n": 0}
    monkeypatch.setattr(main, "call_gemini",
                        lambda prompt, temperature: (calls.__setitem__("n", calls["n"] + 1), "답")[1])
    main.run_generate_routine()
    assert calls["n"] == main._MAX_FILL_PER_RUN


def test_model_answer_prompt_has_question_placeholder():
    from prompts import MODEL_ANSWER_PROMPT
    rendered = MODEL_ANSWER_PROMPT.format(question="테스트 질문")
    assert "테스트 질문" in rendered


def test_handle_slack_event_reads_each_file_once(monkeypatch):
    import main, storage
    calls = []
    qfile = storage.render_question_file(storage.Question(
        "Q001", "Java", "☕ Java", "제목", "2026-07-11", "질문본문"))
    idx = storage.upsert_index_row("", "Java", "☕ Java", "Q001", "제목", "2026-07-11", "⬜ 미답변")

    def fake_get(path):
        calls.append(path)
        if path.endswith("Q001.md"):
            return qfile, "sha"
        if path.endswith("Java.md"):
            return idx, "sha"
        if path == "README.md":
            return storage.EMPTY_README, "sha"
        return None, None

    monkeypatch.setattr(main, "github_get_file", fake_get)
    monkeypatch.setattr(main, "call_gemini", lambda *a, **k: "피드백")
    monkeypatch.setattr(main, "slack_get_thread_parent",
                        lambda c, t: "*[Q001] ☕ Java | 제목*\n질문본문")
    monkeypatch.setattr(main, "slack_post_message", lambda *a, **k: None)
    monkeypatch.setattr(main, "github_commit_files", lambda *a, **k: "sha")

    main.handle_slack_event({"event": {
        "channel": "C1", "thread_ts": "1", "user": "U1", "text": "내 답변", "ts": "2"}})

    # 카테고리를 부모 헤더에서 파싱하므로 _find_slug_for_qid 스캔이 없어야 함.
    # 같은 경로를 두 번 이상 GET 하지 않는다.
    assert calls.count("Java/Q001.md") == 1
    assert calls.count("README.md") == 1
    assert len([c for c in calls if c.endswith("/Java.md")]) == 1
