from unittest.mock import patch
import pytest
import main
import handlers
import config
import state

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
    with patch("gemini_client.call_gemini", return_value=fake):
        result = main.generate_questions("기존 readme")
    assert ("🖥️ CS (네트워크/OS)", "T1", "Q1") in result
    assert len(result) == 2


def test_generate_questions_passes_category_enum_schema(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    fake = '[{"category":"☕ Java","title":"T1","question":"Q1"}]'
    with patch("gemini_client.call_gemini", return_value=fake) as m:
        main.generate_questions("기존 readme")
    schema = m.call_args.kwargs["response_schema"]
    assert schema["items"]["properties"]["category"]["enum"] == main.CATEGORIES


def _fresh_readme_with_unanswered(qid="Q002"):
    import storage
    q = storage.Question(qid, "Java", storage.category_for_slug("Java"),
                         "OSI", "2026-07-05", "OSI란?")
    return storage.insert_toggle(storage.EMPTY_README, storage.build_readme_toggle(q))


def test_run_generate_routine_flow(monkeypatch):
    """루틴 A 기본 경로: 신규 질문만 생성하고 모범답안은 만들지 않는다(풀 모델)."""
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    posted = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "call_gemini",
                        lambda *a, **kw: pytest.fail("루틴 A가 모범답안을 생성했다"))
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: [("☕ Java", "제목1", "새 질문1"),
                                            ("🗄️ Database", "제목2", "새 질문2")])
    readme = _fresh_readme_with_unanswered("Q002")
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: (readme, "s") if path == "README.md" else ("", None))
    committed = {}
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files, msg=message))
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-07-06")

    main.run_generate_routine()

    files = committed["files"]
    # 신규 질문 2개 파일 + 각 인덱스 + README. 인덱스가 비어 있으므로 ID는 Q001부터다.
    assert "Java/Q001.md" in files and "Database/Q002.md" in files
    assert "README.md" in files
    # 자동 채움이 사라졌으므로 기존 미답변 Q002의 문제 파일은 건드리지 않는다
    assert "Java/Q002.md" not in files
    # Slack에 신규 질문 2건만(인덱스가 비어 미답변 안내는 없다), ID 포함
    assert len(posted) == 2 and any("Q001" in t for t in posted)


def test_run_generate_feeds_full_history_not_just_window(monkeypatch):
    """루틴 A는 README 윈도우(상위 5개) 밖의 과거 제목까지 중복방지 컨텍스트로 넘겨야 한다."""
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "call_gemini", lambda p, temperature: "AI답안")

    captured = {}

    def fake_generate(context, count=5):
        captured["context"] = context
        return [("☕ Java", "새 제목", "새 질문")]

    monkeypatch.setattr(handlers, "generate_questions", fake_generate)
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

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "github_commit_files", lambda files, message, **kw: None)
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-07-09")

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
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "call_gemini", lambda p, temperature: "AI답안")
    monkeypatch.setattr(handlers, "generate_questions",
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

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    committed = {}
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files))
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-07-11")

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
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "call_gemini", lambda p, temperature: "AI답안")

    def fake_generate(r, count=5):
        captured["count"] = count
        return [("☕ Java", "t", "q")]

    monkeypatch.setattr(handlers, "generate_questions", fake_generate)
    readme = storage.EMPTY_README

    def fake_get(path):
        if path == "README.md":
            return readme, "s"
        if path == "state.json":
            return '{"daily_count": 50}', "s"
        return "", None

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "github_commit_files", lambda files, message, **kw: None)
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-07-06")
    main.run_generate_routine()
    assert captured["count"] == 10     # 50 → 10 클램프


def test_handle_slack_event_grades_and_commits(monkeypatch):
    import storage
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    posted = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    monkeypatch.setattr(handlers, "slack_get_thread_parent",
                        lambda ch, ts: "*[Q002] 🖥️ CS (네트워크/OS) | OSI 7계층*\nOSI 7계층을 설명하라.")
    monkeypatch.setattr(handlers, "call_gemini",
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
    monkeypatch.setattr(handlers, "github_get_file", fake_get_file)
    monkeypatch.setattr(handlers, "github_commit_files",
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
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "slack_get_thread_parent",
                        lambda ch, ts: "*[Q002] 🖥️ CS (네트워크/OS) | OSI 7계층*\nOSI 7계층을 설명하라.")
    monkeypatch.setattr(handlers, "call_gemini",
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
    monkeypatch.setattr(handlers, "github_get_file", fake_get_file)
    monkeypatch.setattr(handlers, "github_commit_files",
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
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "slack_get_thread_parent",
                        lambda ch, ts: "*[Q002] 🖥️ CS (네트워크/OS) | OSI 7계층*\nOSI 7계층을 설명하라.")
    captured = {}

    def fake_call_gemini(prompt, temperature, response_schema=None, thinking_budget=0):
        captured.update(prompt=prompt, thinking_budget=thinking_budget)
        return "좋은 답변입니다"

    q = storage.Question("Q002", "CS", storage.category_for_slug("CS"),
                         "OSI 7계층", "2026-07-05", "OSI 7계층을 설명하라.")
    monkeypatch.setattr(handlers, "call_gemini", fake_call_gemini)
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: (storage.render_question_file(q), "s")
                        if path == "CS/Q002.md" else ("", None))
    monkeypatch.setattr(handlers, "github_commit_files", lambda files, message, **kw: None)

    payload = {"event": {"type": "message", "user": "UHUMAN", "text": "OSI는 7계층입니다",
                         "thread_ts": "111.1", "ts": "111.2", "channel": "C1"}}
    main.handle_slack_event(payload)
    assert "OSI 7계층을 설명하라." in captured["prompt"]
    assert "OSI는 7계층입니다" in captured["prompt"]
    assert captured["thinking_budget"] == main.FEEDBACK_THINKING_BUDGET


def test_handle_slack_event_ignores_bot(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    called = []
    monkeypatch.setattr(handlers, "call_gemini", lambda *a, **k: called.append(1))
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
    monkeypatch.setattr(handlers, "run_generate_routine", lambda: called.append("A"))
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
    monkeypatch.setattr(handlers, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(headers={"X-Slack-Retry-Num": "1"},
                  json_data={"type": "event_callback", "event": {}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == []  # 재시도는 즉시 200, 처리 안함


def test_entry_routes_event_callback(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(handlers, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(json_data={"type": "event_callback", "event": {"text": "x"}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["B"]


def test_handle_app_mention_help(monkeypatch):
    posted = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> help"})
    assert posted and "명령어" in posted[0][0]


def test_handle_app_mention_config_show(monkeypatch):
    posted = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: ('{"daily_count": 7}', "s"))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config"})
    assert "7" in posted[0]


def test_handle_app_mention_config_set_commits(monkeypatch):
    posted = []
    commits = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: ('{"daily_count": 5}', "s"))
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: commits.append((files, message)))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config --default=4"})
    assert commits                                    # 커밋 발생
    files, _ = commits[0]
    assert list(files) == ["state.json"]              # README를 커밋하지 않는다
    assert '"daily_count": 4' in files["state.json"]
    assert "4" in posted[-1]


def test_handle_app_mention_config_set_rejects_out_of_range(monkeypatch):
    posted = []
    called = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: called.append(1))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config --default=99"})
    assert called == []  # 커밋 안 함
    assert "1~10" in posted[0]


def test_handle_app_mention_question_posts_top_level(monkeypatch):
    import storage
    posted = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: (storage.EMPTY_README, "s") if path == "README.md" else ("", None))
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: [("☕ Java", "t", "q")] * count)
    monkeypatch.setattr(handlers, "github_commit_files", lambda files, message, **kw: None)
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-07-06")
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
    monkeypatch.setattr(handlers, "call_gemini", lambda *a, **k: gemini_calls.append(1))
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: (storage.EMPTY_README, "s") if path == "README.md" else ("", None))
    monkeypatch.setattr(handlers, "generate_questions", lambda r, count=5: [("☕ Java", "t", "q")])
    monkeypatch.setattr(handlers, "github_commit_files", lambda files, message, **kw: None)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> 질문 1"})
    # find_unanswered/fill 경로를 타지 않으므로 call_gemini는 호출되지 않음
    # (generate_questions를 모킹했으므로 내부 call_gemini도 없음)
    assert gemini_calls == []


def test_handle_app_mention_unknown(monkeypatch):
    posted = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> 안녕"})
    assert "help" in posted[0]


def test_entry_routes_app_mention(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(handlers, "handle_app_mention", lambda e: called.append("M"))
    monkeypatch.setattr(handlers, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(json_data={"type": "event_callback",
                             "event": {"type": "app_mention", "text": "<@UBOT> help"}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["M"]  # 멘션은 handle_app_mention으로


def test_entry_message_still_routes_to_slack_event(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(handlers, "handle_app_mention", lambda e: called.append("M"))
    monkeypatch.setattr(handlers, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(json_data={"type": "event_callback",
                             "event": {"type": "message", "text": "x"}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["B"]  # 일반 메시지는 기존 경로 유지


def test_entry_routine_a_failure_notifies_slack(monkeypatch):
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setattr(handlers, "run_generate_routine",
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
    monkeypatch.setattr(handlers, "slack_post_message",
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
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: called.append(1) or [])
    main.handle_app_mention({"channel": "C1", "user": "UHACKER", "text": "<@UBOT> 질문 3"})
    assert called == []        # 생성 시도조차 하지 않음
    assert "권한" in posted[0]


def test_handle_app_mention_config_set_blocked_for_unauthorized(monkeypatch):
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    posted = []
    commits = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: commits.append(message))
    main.handle_app_mention({"channel": "C1", "user": "UHACKER",
                             "text": "<@UBOT> config --default=4"})
    assert commits == []       # 커밋 안 함
    assert "권한" in posted[0]


def test_handle_app_mention_help_blocked_for_unauthorized(monkeypatch):
    # 전체 잠금: 읽기/도움말도 비등록 사용자에겐 거부
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    posted = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    main.handle_app_mention({"channel": "C1", "user": "UHACKER", "text": "<@UBOT> help"})
    assert "권한" in posted[0]   # 도움말 대신 권한 안내


def test_handle_app_mention_question_allowed_for_authorized(monkeypatch):
    import storage
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: (storage.EMPTY_README, "s") if path == "README.md" else ("", None))
    seen = []
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: seen.append(count) or [("☕ Java", "t", "q")])
    monkeypatch.setattr(handlers, "github_commit_files", lambda files, message, **kw: None)
    main.handle_app_mention({"channel": "C1", "user": "UADMIN", "text": "<@UBOT> 질문 1"})
    assert seen == [1]


def test_handle_app_mention_help_allowed_for_authorized(monkeypatch):
    # 등록 사용자는 help 정상 동작
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    posted = []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    main.handle_app_mention({"channel": "C1", "user": "UADMIN", "text": "<@UBOT> help"})
    assert "명령어" in posted[0]


def test_today_kst_iso_at_0700_kst_returns_same_kst_day(monkeypatch):
    # 2026-06-29 22:30 UTC == 2026-06-30 07:30 KST → 날짜는 06-30 이어야 한다(전날 아님)
    fixed = config.datetime(2026, 6, 30, 7, 30, tzinfo=config.KST)
    monkeypatch.setattr(config, "_now_kst", lambda: fixed)
    assert config.today_kst_iso() == "2026-06-30"



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

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "call_gemini", lambda *a, **k: "피드백")
    monkeypatch.setattr(handlers, "slack_get_thread_parent",
                        lambda c, t: "*[Q001] ☕ Java | 제목*\n질문본문")
    monkeypatch.setattr(handlers, "slack_post_message", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "github_commit_files", lambda *a, **k: "sha")

    main.handle_slack_event({"event": {
        "channel": "C1", "thread_ts": "1", "user": "U1", "text": "내 답변", "ts": "2"}})

    # 카테고리를 부모 헤더에서 파싱하므로 _find_slug_for_qid 스캔이 없어야 함.
    # 같은 경로를 두 번 이상 GET 하지 않는다.
    assert calls.count("Java/Q001.md") == 1
    assert calls.count("README.md") == 1
    assert len([c for c in calls if c.endswith("/Java.md")]) == 1


def test_config_set_preserves_unknown_fields(monkeypatch):
    # 확장 기능을 위해 paused_until 같은 필드를 config 명령이 지우면 안 된다
    commits = []
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: ('{"daily_count": 5, "paused_until": "2026-08-01"}', "s"))
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: commits.append(files))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config --default=8"})
    assert '"paused_until": "2026-08-01"' in commits[0]["state.json"]


def test_run_generate_routine_falls_back_when_state_missing(monkeypatch):
    # state.json이 없어도(404) 기본값 5로 동작한다
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    captured = {}
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)

    def fake_generate(r, count=5):
        captured["count"] = count
        return [("☕ Java", "t", "q")]

    monkeypatch.setattr(handlers, "generate_questions", fake_generate)
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: (storage.EMPTY_README, "s") if path == "README.md"
                        else (None, None))
    monkeypatch.setattr(handlers, "github_commit_files", lambda files, message, **kw: None)
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-07-28")
    main.run_generate_routine()
    assert captured["count"] == 5



def test_run_generate_prunes_with_top_n_from_state(monkeypatch):
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    r = storage.EMPTY_README
    for i in range(1, 6):  # CS 5개, 모두 답변 완료 상태로 둬서 fill 경로를 타지 않게 한다
        q = storage.Question(f"Q{i:03d}", "CS", storage.category_for_slug("CS"),
                             f"t{i}", "2026-07-05", f"질문{i}",
                             answer="답", feedback="피드백", answered=True)
        r = storage.insert_toggle(r, storage.build_readme_toggle(q))

    def fake_get(path):
        if path == "README.md":
            return r, "s"
        if path == "state.json":
            return '{"readme_top_n": 2}', "s"
        return "", None

    committed = {}
    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "generate_questions", lambda c, count=5: [])
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: committed.update(files))
    monkeypatch.setattr(handlers, "slack_post_message", lambda *a, **k: None)
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-07-06")
    main.run_generate_routine()
    readme = committed["README.md"]
    assert readme.count("<!-- q Q") == 2      # 카테고리당 2개만 남는다


def _setup_generate_with_state(monkeypatch, state_json, posted, commits):
    """루틴 A를 최소 구성으로 돌리기 위한 공통 목. state.json 내용만 바꿔가며 쓴다."""
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")

    def fake_get(path):
        if path == "README.md":
            return storage.EMPTY_README, "s"
        if path == "state.json":
            return state_json, "s"
        return "", None

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "generate_questions", lambda c, count=5: [("☕ Java", "t", "q")])
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: commits.append(message))
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-07-06")
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))


def test_run_generate_notifies_when_state_clamped(monkeypatch):
    posted, commits = [], []
    _setup_generate_with_state(monkeypatch, '{"readme_top_n": 0}', posted, commits)
    main.run_generate_routine()
    assert any("보정" in t and "readme_top_n" in t for t in posted)


def test_run_generate_notifies_when_state_unparseable(monkeypatch):
    posted, commits = [], []
    _setup_generate_with_state(monkeypatch, '{ "daily_count": ', posted, commits)
    main.run_generate_routine()
    assert any("보정" in t for t in posted)
    assert commits          # 기본값으로 정상 생성까지 완주한다


def test_run_generate_does_not_notify_for_valid_state(monkeypatch):
    posted, commits = [], []
    _setup_generate_with_state(monkeypatch, '{"daily_count": 5}', posted, commits)
    main.run_generate_routine()
    assert not any("보정" in t for t in posted)


def test_run_generate_survives_notify_failure(monkeypatch):
    posted, commits = [], []
    _setup_generate_with_state(monkeypatch, '{"readme_top_n": 0}', posted, commits)

    def boom(ch, text, thread_ts=None):
        raise RuntimeError("slack down")

    monkeypatch.setattr(handlers, "slack_post_message", boom)
    main.run_generate_routine()
    assert commits          # 알림이 실패해도 커밋까지 완주한다


# --- 루틴 A 정지 (조기 종료) ---

def _paused_env(monkeypatch, paused_until, readme="# README"):
    """정지 상태의 루틴 A를 돌리기 위한 공통 준비. 조회된 경로 목록을 돌려준다."""
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    fetched = []

    def fake_get(path):
        fetched.append(path)
        if path == "state.json":
            return ('{"daily_count": 5, "paused_until": "%s"}' % paused_until, "s")
        return (readme, "s")

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-08-05")
    return fetched


def test_run_generate_paused_does_not_fetch_readme(monkeypatch):
    """정지 중에는 285KB짜리 README를 아예 받지 않는다.
    읽기 순서가 되돌아가거나 조기 종료가 늦춰지면 여기서 잡힌다."""
    fetched = _paused_env(monkeypatch, "2026-08-10")
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: pytest.fail("정지 중 커밋 발생"))
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: pytest.fail("정지 중 Slack 전송"))
    monkeypatch.setattr(handlers, "call_gemini",
                        lambda *a, **kw: pytest.fail("정지 중 Gemini 호출"))
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda *a, **kw: pytest.fail("정지 중 질문 생성"))

    main.run_generate_routine()

    assert fetched == ["state.json"]          # README를 받지 않았다
    assert "README.md" not in fetched


def test_run_generate_paused_forever_is_also_silent(monkeypatch):
    fetched = _paused_env(monkeypatch, "forever")
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: pytest.fail("정지 중 커밋 발생"))
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: pytest.fail("정지 중 Slack 전송"))

    main.run_generate_routine()

    assert fetched == ["state.json"]


def test_run_generate_paused_ignores_unanswered_questions(monkeypatch):
    """README에 미답변이 있어도 결과가 같아야 한다 (R-3 폐기 — 모범답안 자동 작성 안 함)."""
    readme = _fresh_readme_with_unanswered("Q002")
    fetched = _paused_env(monkeypatch, "2026-08-10", readme=readme)
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: pytest.fail("정지 중 커밋 발생"))
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: pytest.fail("정지 중 Slack 전송"))
    monkeypatch.setattr(handlers, "call_gemini",
                        lambda *a, **kw: pytest.fail("정지 중 모범답안 생성"))

    main.run_generate_routine()

    assert fetched == ["state.json"]


def test_run_generate_paused_last_day_still_paused(monkeypatch):
    # paused_until은 마지막 정지일이므로 그날 아침도 정지다
    fetched = _paused_env(monkeypatch, "2026-08-05")
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: pytest.fail("정지 중 커밋 발생"))
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: pytest.fail("정지 중 Slack 전송"))

    main.run_generate_routine()

    assert fetched == ["state.json"]


def test_run_generate_expired_pause_clears_field_and_generates(monkeypatch):
    """정지가 끝난 첫 아침: 정상 생성하고 그 커밋에서 paused_until이 사라진다."""
    import json, storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "call_gemini", lambda p, temperature: "AI답안")
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: [("☕ Java", "제목1", "새 질문1")])
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: ('{"daily_count": 5, "paused_until": "2026-08-01"}', "s")
                        if path == "state.json" else (storage.EMPTY_README, "s"))
    committed = {}
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files))
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-08-05")

    main.run_generate_routine()

    assert "state.json" in committed["files"]
    assert "paused_until" not in json.loads(committed["files"]["state.json"])
    assert "Java/Q001.md" in committed["files"]        # 신규 질문이 생성됐다


def test_run_generate_corrupt_pause_value_is_cleaned(monkeypatch):
    """훼손된 값은 '정지 아님'으로 떨어지고 그 커밋에서 정리된다."""
    import json, storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "call_gemini", lambda p, temperature: "AI답안")
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: [("☕ Java", "제목1", "새 질문1")])
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: ('{"daily_count": 5, "paused_until": "invalid"}', "s")
                        if path == "state.json" else (storage.EMPTY_README, "s"))
    committed = {}
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files))
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-08-05")

    main.run_generate_routine()

    assert "paused_until" not in json.loads(committed["files"]["state.json"])


def test_run_generate_without_pause_field_does_not_commit_state_json(monkeypatch):
    """평상시 아침에 내용이 같은 state.json을 매일 트리에 올리지 않는지."""
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(handlers, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(handlers, "call_gemini", lambda p, temperature: "AI답안")
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: [("☕ Java", "제목1", "새 질문1")])
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: ('{"daily_count": 5}', "s")
                        if path == "state.json" else (storage.EMPTY_README, "s"))
    committed = {}
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files))
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-08-05")

    main.run_generate_routine()

    assert "state.json" not in committed["files"]


# --- stop/start 명령 처리 ---

def _mention_env(monkeypatch, state_json='{"daily_count": 5}'):
    """멘션 명령 테스트 공통 준비. (posted, commits, fetched) 반환."""
    posted, commits, fetched = [], [], []
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))

    def fake_get(path):
        fetched.append(path)
        return (state_json, "s")

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: commits.append((files, message)))
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-08-05")
    return posted, commits, fetched


def test_handle_stop_with_days_commits_computed_last_day(monkeypatch):
    import json
    posted, commits, _ = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 3"})
    files, message = commits[0]
    assert list(files) == ["state.json"]                      # README를 건드리지 않는다
    # 오늘 포함 3일 → 마지막 정지일은 오늘+2
    assert json.loads(files["state.json"])["paused_until"] == "2026-08-07"
    assert "2026-08-07" in posted[-1] and "2026-08-08" in posted[-1]   # 마지막일·재개일 둘 다


def test_handle_stop_with_date_stores_value_verbatim(monkeypatch):
    import json
    posted, commits, _ = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 2026-08-10"})
    files, _ = commits[0]
    assert json.loads(files["state.json"])["paused_until"] == "2026-08-10"
    assert "2026-08-11" in posted[-1]                          # 재개일


def test_handle_stop_without_arg_is_forever(monkeypatch):
    import json
    posted, commits, _ = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop"})
    files, _ = commits[0]
    assert json.loads(files["state.json"])["paused_until"] == "forever"
    assert "start" in posted[-1]                               # 재개 방법 안내


def test_handle_stop_out_of_range_days_does_not_touch_github(monkeypatch):
    posted, commits, fetched = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 99"})
    assert commits == []
    assert fetched == []                    # 잘못된 입력이 GitHub API를 건드리지 않는다
    assert "1~30" in posted[0]


def test_handle_stop_zero_days_rejected(monkeypatch):
    posted, commits, fetched = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 0"})
    assert commits == [] and fetched == []
    assert "1~30" in posted[0]


def test_handle_stop_fake_date_rejected(monkeypatch):
    posted, commits, fetched = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 2026-13-99"})
    assert commits == [] and fetched == []
    assert "YYYY-MM-DD" in posted[0]


def test_handle_stop_past_date_rejected(monkeypatch):
    posted, commits, fetched = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 2026-07-01"})
    assert commits == [] and fetched == []
    assert "2026-08-05" in posted[0]        # 오늘 날짜를 알려준다


def test_handle_stop_today_is_allowed(monkeypatch):
    import json
    _, commits, _ = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 2026-08-05"})
    files, _ = commits[0]
    assert json.loads(files["state.json"])["paused_until"] == "2026-08-05"


def test_handle_stop_typo_rejected_without_pausing(monkeypatch):
    posted, commits, fetched = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop tomorow"})
    assert commits == [] and fetched == []
    assert "stop 3" in posted[0]            # 올바른 형식 안내


def test_handle_stop_while_paused_replaces_not_extends(monkeypatch):
    import json
    _, commits, _ = _mention_env(
        monkeypatch, state_json='{"daily_count": 5, "paused_until": "2026-08-20"}')
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 5"})
    files, _ = commits[0]
    # 연장이 아니라 오늘 기준 재계산 → 오늘+4
    assert json.loads(files["state.json"])["paused_until"] == "2026-08-09"


def test_handle_start_removes_field(monkeypatch):
    import json
    posted, commits, _ = _mention_env(
        monkeypatch, state_json='{"daily_count": 5, "paused_until": "forever"}')
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> start"})
    files, _ = commits[0]
    assert "paused_until" not in json.loads(files["state.json"])
    assert "재개" in posted[-1]


def test_handle_start_when_not_paused_does_not_commit(monkeypatch):
    posted, commits, _ = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> start"})
    assert commits == []
    assert "이미" in posted[0]


def test_handle_config_show_appends_pause_status(monkeypatch):
    posted, _, _ = _mention_env(
        monkeypatch, state_json='{"daily_count": 5, "paused_until": "2026-08-07"}')
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config"})
    assert "D-3" in posted[0]               # 오늘 포함 8/5·8/6·8/7 = 3일
    assert "2026-08-08" in posted[0]        # 재개 예정일


def test_handle_config_show_forever(monkeypatch):
    posted, _, _ = _mention_env(
        monkeypatch, state_json='{"daily_count": 5, "paused_until": "forever"}')
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config"})
    assert "무기한" in posted[0]


def test_handle_config_show_no_pause_line_when_active(monkeypatch):
    posted, _, _ = _mention_env(monkeypatch)
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config"})
    assert "정지" not in posted[0]


def test_handle_question_works_while_paused(monkeypatch):
    """정지 중에도 수동 생성은 동작하고 paused_until을 건드리지 않는다 (R-5)."""
    import storage
    posted, commits, _ = _mention_env(
        monkeypatch, state_json='{"daily_count": 5, "paused_until": "forever"}')
    monkeypatch.setattr(handlers, "github_get_file",
                        lambda path: ('{"daily_count": 5, "paused_until": "forever"}', "s")
                        if path == "state.json" else (storage.EMPTY_README, "s"))
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: [("☕ Java", "제목1", "새 질문1")])
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> 질문 1"})
    files, _ = commits[0]
    assert "Java/Q001.md" in files
    assert "state.json" not in files        # 정지 상태를 건드리지 않는다


def test_handle_stop_blocked_for_unauthorized(monkeypatch):
    posted, commits, _ = _mention_env(monkeypatch)
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U_OWNER")
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> stop 3", "user": "U_OTHER"})
    assert commits == []
    assert "권한" in posted[0]


def test_main_reexports_pause_helpers():
    """기존 테스트들이 main.X로 접근하는 관례를 새 함수에도 유지한다."""
    for name in ["is_paused", "get_paused_until", "set_paused_until",
                 "shift_date_iso", "parse_iso_date", "days_left", "PAUSE_FOREVER"]:
        assert hasattr(main, name), f"main에 {name}이 재노출되지 않았다"


# --- 루틴 A: 미답변 안내 (자동 채움 제거) ---

def _index_text(rows):
    """인덱스 텍스트. rows: [(qid, title, date, status)]"""
    head = "# 테스트\n\n| ID | 제목 | 출제일 | 상태 |\n| --- | --- | --- | --- |\n"
    return head + "".join(f"| [{q}](./{q}.md) | {t} | {d} | {s} |\n" for q, t, d, s in rows)


def _routine_env(monkeypatch, cs_rows=(), state_json='{"daily_count": 5}'):
    """루틴 A 공통 준비. (posted, committed, fetched) 반환."""
    import storage
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    posted, committed, fetched = [], {}, []

    def fake_get(path):
        fetched.append(path)
        if path == "state.json":
            return (state_json, "s")
        if path == "README.md":
            return (storage.EMPTY_README, "s")
        if path == "CS/CS.md":
            return (_index_text(cs_rows), "s")
        return ("", None)

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: committed.update(files=files, msg=message))
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: [("☕ Java", "제목1", "새 질문1")])
    monkeypatch.setattr(handlers, "today_kst_iso", lambda: "2026-08-08")
    return posted, committed, fetched


def test_routine_a_never_generates_model_answers(monkeypatch):
    """자동 채움 제거 검증. call_gemini가 불리면 실패한다."""
    import storage
    monkeypatch.setattr(handlers, "call_gemini",
                        lambda *a, **kw: pytest.fail("루틴 A가 모범답안을 생성했다"))
    rows = [("Q001", "제목1", "2026-08-01", storage.STATUS_UNANSWERED)]
    posted, committed, _ = _routine_env(monkeypatch, cs_rows=rows)

    main.run_generate_routine()

    assert committed["files"]                       # 신규 질문은 정상 커밋


def test_routine_a_sends_pending_notice_before_questions(monkeypatch):
    import storage
    rows = [("Q003", "c", "2026-08-03", storage.STATUS_UNANSWERED),
            ("Q002", "b", "2026-08-02", storage.STATUS_UNANSWERED),
            ("Q001", "a", "2026-08-01", storage.STATUS_AI_AUTO)]
    posted, _, _ = _routine_env(monkeypatch, cs_rows=rows)

    main.run_generate_routine()

    assert "미답변이 2개" in posted[0]              # 안내가 첫 메시지
    assert "Q003" in posted[0] and "Q002" in posted[0]
    assert "Q001" not in posted[0]                  # 🤖는 세지 않는다
    assert "새 질문1" in posted[1]                  # 신규 질문이 그다음


def test_routine_a_pending_count_excludes_todays_new_questions(monkeypatch):
    """집계는 신규 생성 '전' 값이어야 한다. 뒤에서 세면 방금 보낸 질문이 섞인다."""
    import storage
    rows = [("Q001", "a", "2026-08-01", storage.STATUS_UNANSWERED)]
    posted, _, _ = _routine_env(monkeypatch, cs_rows=rows)
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda r, count=5: [("☕ Java", "t1", "q1"), ("☕ Java", "t2", "q2")])

    main.run_generate_routine()

    assert "미답변이 1개" in posted[0]              # 3개가 아니다


def test_routine_a_no_notice_when_nothing_pending(monkeypatch):
    posted, _, _ = _routine_env(monkeypatch, cs_rows=[])

    main.run_generate_routine()

    assert not any("미답변" in t for t in posted)
    assert "새 질문1" in posted[0]


def test_routine_a_notice_failure_does_not_block_questions(monkeypatch):
    """안내 전송이 실패해도 신규 질문 전송은 계속돼야 한다."""
    import storage
    rows = [("Q001", "a", "2026-08-01", storage.STATUS_UNANSWERED)]
    posted, _, _ = _routine_env(monkeypatch, cs_rows=rows)
    calls = {"n": 0}

    def flaky(ch, text, thread_ts=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("slack down")
        posted.append(text)

    monkeypatch.setattr(handlers, "slack_post_message", flaky)

    main.run_generate_routine()

    assert any("새 질문1" in t for t in posted)


# --- 루틴 A: 미답변 자동 정지 ---

def _pending(n, start=1):
    """미답변 n건짜리 인덱스 행. qid는 Q001부터."""
    import storage
    return [(f"Q{start + i:03d}", f"t{i}", "2026-08-01", storage.STATUS_UNANSWERED)
            for i in range(n)]


def test_auto_pause_when_threshold_reached(monkeypatch):
    import json
    posted, committed, fetched = _routine_env(
        monkeypatch, cs_rows=_pending(20),
        state_json='{"daily_count": 5, "auto_stop_threshold": 20}')
    monkeypatch.setattr(handlers, "generate_questions",
                        lambda *a, **kw: pytest.fail("자동 정지 중 질문 생성"))

    main.run_generate_routine()

    files = committed["files"]
    assert list(files) == ["state.json"]                    # state.json 한 파일만
    assert json.loads(files["state.json"])["paused_until"] == "forever"
    assert "20" in posted[0] and "자동 정지" in posted[0]
    assert "README.md" not in fetched                       # 285KB를 받지 않는다


def test_auto_pause_boundary_19_still_generates(monkeypatch):
    posted, committed, _ = _routine_env(
        monkeypatch, cs_rows=_pending(19),
        state_json='{"daily_count": 5, "auto_stop_threshold": 20}')

    main.run_generate_routine()

    assert "README.md" in committed["files"]                # 정상 생성 경로
    assert "미답변이 19개" in posted[0]
    assert "자동 정지" not in posted[0]


def test_auto_pause_uses_configured_threshold(monkeypatch):
    import json
    _, committed, _ = _routine_env(
        monkeypatch, cs_rows=_pending(6),
        state_json='{"daily_count": 5, "auto_stop_threshold": 5}')

    main.run_generate_routine()

    assert json.loads(committed["files"]["state.json"])["paused_until"] == "forever"


def test_auto_pause_notice_lists_recent_qids(monkeypatch):
    posted, _, _ = _routine_env(
        monkeypatch, cs_rows=_pending(20),
        state_json='{"daily_count": 5, "auto_stop_threshold": 20}')

    main.run_generate_routine()

    assert "Q020" in posted[0]           # 최근순 상위
    assert "auto" in posted[0] and "start" in posted[0]


def test_auto_pause_fires_only_once(monkeypatch):
    """정지된 다음 날 아침은 0단계 조기 종료가 먼저 걸려 인덱스도 읽지 않는다."""
    posted, committed, fetched = _routine_env(
        monkeypatch, cs_rows=_pending(20),
        state_json='{"daily_count": 5, "auto_stop_threshold": 20, "paused_until": "forever"}')

    main.run_generate_routine()

    assert fetched == ["state.json"]
    assert committed == {}
    assert posted == []


def test_auto_pause_overrides_expired_pause_field(monkeypatch):
    """만료된 값이 남아 있고 동시에 임계값을 넘으면 forever가 덮어쓴다."""
    import json
    _, committed, _ = _routine_env(
        monkeypatch, cs_rows=_pending(20),
        state_json='{"daily_count": 5, "auto_stop_threshold": 20, "paused_until": "2026-08-01"}')

    main.run_generate_routine()

    assert json.loads(committed["files"]["state.json"])["paused_until"] == "forever"


# --- @봇 auto 명령 ---

def _qfile(qid="Q001", slug="CS", answered=False, ai_auto=False):
    """문제 파일 마크다운. storage.render_question_file로 만든다."""
    import storage
    q = storage.Question(qid, slug, storage.category_for_slug(slug),
                         "TCP와 UDP 차이", "2026-08-01", "TCP와 UDP의 차이는?",
                         answer="기존답변" if (answered or ai_auto) else "",
                         feedback="기존피드백" if (answered or ai_auto) else "",
                         answered=answered, ai_auto=ai_auto)
    return storage.render_question_file(q)


def _auto_env(monkeypatch, qfile_text=None, readme=None):
    """@봇 auto 공통 준비. (posted, commits, fetched) 반환."""
    import storage
    monkeypatch.setenv("REPO_OWNER", "gusm96")
    monkeypatch.setenv("REPO_NAME", "daily-interview-pipeline")
    monkeypatch.delenv("REPO_BRANCH", raising=False)
    posted, commits, fetched = [], [], []

    def fake_get(path):
        fetched.append(path)
        if path.endswith("/CS.md"):
            return (_index_text([("Q001", "TCP와 UDP 차이", "2026-08-01",
                                  storage.STATUS_UNANSWERED)]), "s")
        if path == "CS/Q001.md":
            return (qfile_text if qfile_text is not None else _qfile(), "s")
        if path == "README.md":
            return (readme if readme is not None else storage.EMPTY_README, "s")
        return ("", None)

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: commits.append((files, message)))
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(handlers, "call_gemini", lambda p, temperature: "AI모범답안본문")
    return posted, commits, fetched


def test_auto_generates_and_commits(monkeypatch):
    import storage
    posted, commits, _ = _auto_env(monkeypatch)

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001"})

    files, message = commits[0]
    assert "CS/Q001.md" in files
    assert "CS/CS.md" in files
    assert storage.STATUS_AI_AUTO in files["CS/CS.md"]      # 인덱스 배지 갱신
    assert "AI모범답안본문" in files["CS/Q001.md"]
    assert "Q001" in message


def test_auto_replies_with_github_link_not_body(monkeypatch):
    """모범답안 본문을 Slack에 싣지 않는다 — 4000자 한도에 걸릴 경로를 없앤다.
    저장소가 정본이고 Slack은 그리로 가는 안내만 한다."""
    posted, _, _ = _auto_env(monkeypatch)

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001"})

    assert ("https://github.com/gusm96/daily-interview-pipeline/blob/main/CS/Q001.md"
            in posted[-1])
    assert "AI모범답안본문" not in posted[-1]


def test_auto_link_follows_repo_branch_env(monkeypatch):
    posted, _, _ = _auto_env(monkeypatch)
    monkeypatch.setenv("REPO_BRANCH", "develop")

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001"})

    assert "/blob/develop/CS/Q001.md" in posted[-1]


def test_auto_refuses_when_already_answered_by_user(monkeypatch):
    posted, commits, _ = _auto_env(monkeypatch, qfile_text=_qfile(answered=True))

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001"})

    assert commits == []                                    # 덮어쓰지 않는다
    assert "이미 직접 답변" in posted[0]


def test_auto_refuses_when_already_ai_answered(monkeypatch):
    posted, commits, _ = _auto_env(monkeypatch, qfile_text=_qfile(ai_auto=True))

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001"})

    assert commits == []
    assert "이미 AI 모범답안" in posted[0]


def test_auto_does_not_call_gemini_when_refused(monkeypatch):
    posted, commits, _ = _auto_env(monkeypatch, qfile_text=_qfile(answered=True))
    monkeypatch.setattr(handlers, "call_gemini",
                        lambda *a, **kw: pytest.fail("거부해야 할 요청에 Gemini 호출"))

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001"})

    assert commits == []


def test_auto_without_qid_does_not_touch_github(monkeypatch):
    posted, commits, fetched = _auto_env(monkeypatch)

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto"})

    assert commits == [] and fetched == []
    assert "Q001" in posted[0]                              # 형식 예시 안내


def test_auto_unknown_qid_reports_not_found(monkeypatch):
    posted, commits, _ = _auto_env(monkeypatch)

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q999"})

    assert commits == []
    assert "Q999" in posted[0] and "찾지 못했" in posted[0]


def test_auto_patches_readme_toggle_when_present(monkeypatch):
    import storage
    q = storage.Question("Q001", "CS", storage.category_for_slug("CS"),
                         "TCP와 UDP 차이", "2026-08-01", "TCP와 UDP의 차이는?")
    readme = storage.insert_toggle(storage.EMPTY_README, storage.build_readme_toggle(q))
    _, commits, _ = _auto_env(monkeypatch, readme=readme)

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001"})

    files, _ = commits[0]
    assert "AI모범답안본문" in files["README.md"]


def test_auto_skips_readme_when_toggle_absent(monkeypatch):
    """창 밖으로 밀려난 질문도 문제 파일·인덱스는 정상 갱신돼야 한다."""
    import storage
    _, commits, _ = _auto_env(monkeypatch, readme=storage.EMPTY_README)

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001"})

    files, _ = commits[0]
    assert "README.md" not in files
    assert "CS/Q001.md" in files


def test_auto_blocked_for_unauthorized(monkeypatch):
    posted, commits, _ = _auto_env(monkeypatch)
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "U_OWNER")

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> auto Q001", "user": "U_OTHER"})

    assert commits == []
    assert "권한" in posted[0]


# --- start 경고 + 재노출 ---

def _start_env(monkeypatch, unanswered_count, threshold=20):
    import storage
    posted, commits = [], []
    rows = [(f"Q{i + 1:03d}", f"t{i}", "2026-08-01", storage.STATUS_UNANSWERED)
            for i in range(unanswered_count)]

    def fake_get(path):
        if path == "state.json":
            return ('{"daily_count": 5, "paused_until": "forever", '
                    f'"auto_stop_threshold": {threshold}}}', "s")
        if path.endswith("/CS.md"):
            return (_index_text(rows), "s")
        return ("", None)

    monkeypatch.setattr(handlers, "github_get_file", fake_get)
    monkeypatch.setattr(handlers, "github_commit_files",
                        lambda files, message, **kw: commits.append((files, message)))
    monkeypatch.setattr(handlers, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    return posted, commits


def test_start_warns_when_still_over_threshold(monkeypatch):
    posted, commits = _start_env(monkeypatch, unanswered_count=23, threshold=20)

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> start"})

    assert commits                              # 재개는 시킨다
    assert "재개" in posted[-1]
    assert "23" in posted[-1] and "20" in posted[-1]
    assert "자동 정지" in posted[-1]


def test_start_no_warning_when_under_threshold(monkeypatch):
    posted, commits = _start_env(monkeypatch, unanswered_count=3, threshold=20)

    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> start"})

    assert commits
    assert "재개" in posted[-1]
    assert "자동 정지" not in posted[-1]


def test_main_reexports_pull_helpers():
    for name in ["unanswered_rows", "STATUS_UNANSWERED", "STATUS_AI_AUTO", "STATUS_ANSWERED"]:
        assert hasattr(main, name), f"main에 {name}이 재노출되지 않았다"
