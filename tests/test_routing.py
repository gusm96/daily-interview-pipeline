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


def test_run_generate_routine_flow(monkeypatch, sample_readme):
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")

    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    # Gemini 모범답안 + 질문 생성 모킹
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "AI답안")
    monkeypatch.setattr(main, "generate_questions",
                        lambda r, count=5: [("☕ Java", "제목1", "새 질문1"), ("🗄️ Database", "제목2", "새 질문2")])

    commits = []

    def fake_commit(mutate_fn, message, max_retries=3):
        new_content, result = mutate_fn(sample_readme)
        commits.append(message)
        return new_content, result

    monkeypatch.setattr(main, "github_commit_with_retry", fake_commit)
    monkeypatch.setattr(main, "github_get_readme", lambda: (sample_readme, "sha"))

    main.run_generate_routine()

    # 미답변(Q002) 채움 커밋 + 질문 append 커밋 = 2회
    assert len(commits) == 2
    # Slack에 질문 2개 개별 전송, ID 포함
    assert len(posted) == 2
    assert any("Q003" in t for t in posted)


def test_run_generate_routine_uses_config_default(monkeypatch, sample_readme):
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    readme = "<!-- config:default=3 -->\n" + sample_readme
    captured = {}
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "AI답안")

    def fake_generate(r, count=5):
        captured["count"] = count
        return [("☕ Java", "t", "q")]

    monkeypatch.setattr(main, "generate_questions", fake_generate)
    monkeypatch.setattr(main, "github_get_readme", lambda: (readme, "sha"))
    monkeypatch.setattr(main, "github_commit_with_retry",
                        lambda fn, msg, max_retries=3: fn(readme))
    main.run_generate_routine()
    assert captured["count"] == 3


def test_handle_slack_event_grades_and_commits(monkeypatch, sample_readme):
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")

    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    monkeypatch.setattr(main, "slack_get_thread_parent",
                        lambda ch, ts: "오늘의 질문 [Q002] OSI 7계층")
    monkeypatch.setattr(
        main, "call_gemini",
        lambda p, temperature, response_schema=None, thinking_budget=0: "좋은 답변입니다",
    )

    committed = {}

    def fake_commit(mutate_fn, message, max_retries=3):
        new_content, result = mutate_fn(sample_readme)
        committed["msg"] = message
        committed["content"] = new_content
        return new_content, result

    monkeypatch.setattr(main, "github_commit_with_retry", fake_commit)

    payload = {"event": {
        "type": "message", "user": "UHUMAN", "text": "OSI는 7계층입니다",
        "thread_ts": "111.1", "ts": "111.2", "channel": "C1",
    }}
    main.handle_slack_event(payload)

    # 피드백이 해당 스레드로 전송
    assert posted and posted[0][1] == "111.1"
    # README Q002 갱신 커밋
    assert "OSI는 7계층입니다" in committed["content"]
    assert "좋은 답변입니다" in committed["content"]


def test_handle_slack_event_passes_question_and_thinking_budget(monkeypatch, sample_readme):
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(
        main, "slack_get_thread_parent",
        lambda ch, ts: "*[Q002] 🖥️ CS (네트워크/OS) | OSI 7계층*\nOSI 7계층을 설명하라.",
    )
    captured = {}

    def fake_call_gemini(prompt, temperature, response_schema=None, thinking_budget=0):
        captured["prompt"] = prompt
        captured["thinking_budget"] = thinking_budget
        return "좋은 답변입니다"

    monkeypatch.setattr(main, "call_gemini", fake_call_gemini)
    monkeypatch.setattr(
        main, "github_commit_with_retry",
        lambda mutate_fn, message, max_retries=3: mutate_fn(sample_readme),
    )

    payload = {"event": {
        "type": "message", "user": "UHUMAN", "text": "OSI는 7계층입니다",
        "thread_ts": "111.1", "ts": "111.2", "channel": "C1",
    }}
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
    monkeypatch.setattr(main, "github_get_readme",
                        lambda: ("<!-- config:default=7 -->\n# r", "sha"))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config"})
    assert "7" in posted[0]


def test_handle_app_mention_config_set_commits(monkeypatch):
    posted = []
    commits = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "github_commit_with_retry",
                        lambda fn, msg, max_retries=3: commits.append(msg) or fn("# r\n"))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config --default=4"})
    assert commits  # 커밋 발생
    assert "4" in posted[-1]


def test_handle_app_mention_config_set_rejects_out_of_range(monkeypatch):
    posted = []
    called = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    monkeypatch.setattr(main, "github_commit_with_retry",
                        lambda fn, msg, max_retries=3: called.append(1))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> config --default=99"})
    assert called == []  # 커밋 안 함
    assert "1~10" in posted[0]


def test_handle_app_mention_question_posts_top_level(monkeypatch):
    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    monkeypatch.setattr(main, "github_get_readme", lambda: ("# r\n", "sha"))
    monkeypatch.setattr(main, "generate_questions",
                        lambda r, count=5: [("☕ Java", "t", "q")] * count)
    monkeypatch.setattr(main, "github_commit_with_retry",
                        lambda fn, msg, max_retries=3: ("new", ["Q010", "Q011"]))
    main.handle_app_mention({"channel": "C1", "text": "<@UBOT> 질문 2", "thread_ts": "T1"})
    # 질문 메시지는 thread_ts=None(최상위)으로 전송
    q_msgs = [p for p in posted if p[0].startswith("*[Q")]
    assert q_msgs and all(p[1] is None for p in q_msgs)
    # 확인 메시지는 멘션 스레드(T1)로
    assert any(p[1] == "T1" and "추가" in p[0] for p in posted)


def test_handle_app_mention_question_does_not_call_grading(monkeypatch):
    # 명령 경로는 절대 모범답안/채점을 호출하지 않는다
    gemini_calls = []
    monkeypatch.setattr(main, "call_gemini", lambda *a, **k: gemini_calls.append(1))
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "github_get_readme", lambda: ("# r\n", "sha"))
    monkeypatch.setattr(main, "generate_questions", lambda r, count=5: [("☕ Java", "t", "q")])
    monkeypatch.setattr(main, "github_commit_with_retry",
                        lambda fn, msg, max_retries=3: ("new", ["Q010"]))
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


def test_run_generate_routine_clamps_out_of_range_default(monkeypatch, sample_readme):
    # 마커가 범위를 벗어나면(50) 생성 개수는 10으로 클램프
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    readme = "<!-- config:default=50 -->\n" + sample_readme
    captured = {}
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "AI답안")

    def fake_generate(r, count=5):
        captured["count"] = count
        return [("☕ Java", "t", "q")]

    monkeypatch.setattr(main, "generate_questions", fake_generate)
    monkeypatch.setattr(main, "github_get_readme", lambda: (readme, "sha"))
    monkeypatch.setattr(main, "github_commit_with_retry",
                        lambda fn, msg, max_retries=3: fn(readme))
    main.run_generate_routine()
    assert captured["count"] == 10


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
    monkeypatch.setattr(main, "github_commit_with_retry",
                        lambda fn, msg, max_retries=3: commits.append(msg))
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
    monkeypatch.setenv("SLACK_ALLOWED_USER_IDS", "UADMIN")
    monkeypatch.setattr(main, "slack_post_message", lambda ch, text, thread_ts=None: None)
    monkeypatch.setattr(main, "github_get_readme", lambda: ("# r\n", "sha"))
    seen = []
    monkeypatch.setattr(main, "generate_questions",
                        lambda r, count=5: seen.append(count) or [("☕ Java", "t", "q")])
    monkeypatch.setattr(main, "github_commit_with_retry",
                        lambda fn, msg, max_retries=3: ("new", ["Q010"]))
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


def test_append_questions_default_date_uses_kst(monkeypatch):
    fixed = main.datetime(2026, 6, 30, 7, 30, tzinfo=main.KST)
    monkeypatch.setattr(main, "_now_kst", lambda: fixed)
    new_content, ids = main.append_questions([("CS", "제목", "질문 본문")], "## CS\n\n")
    assert "(2026-06-30)" in new_content


def test_run_generate_caps_unanswered_fill_calls(monkeypatch):
    # 미답변이 많아도 한 번에 최대 _MAX_FILL_PER_RUN개만 모범답안 생성
    many = [(f"Q{i:03d}", f"질문{i}") for i in range(1, 30)]
    monkeypatch.setattr(main, "validate_env", lambda: [])
    monkeypatch.setattr(main, "github_get_readme", lambda: ("README", "sha"))
    monkeypatch.setattr(main, "find_unanswered_questions", lambda c: many)
    monkeypatch.setattr(main, "get_config_default", lambda c: 1)
    monkeypatch.setattr(main, "generate_questions", lambda c, n: [("CS", "t", "q")])
    monkeypatch.setattr(main, "github_commit_with_retry", lambda fn, msg: ("README", ["Q100"]))
    monkeypatch.setattr(main, "slack_post_message", lambda *a, **k: None)
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    calls = {"n": 0}
    monkeypatch.setattr(main, "call_gemini",
                        lambda prompt, temperature: (calls.__setitem__("n", calls["n"] + 1), "답")[1])

    main.run_generate_routine()
    assert calls["n"] == main._MAX_FILL_PER_RUN  # generate_questions는 대체됨 → fill 호출만 집계


def test_model_answer_prompt_has_question_placeholder():
    from prompts import MODEL_ANSWER_PROMPT
    rendered = MODEL_ANSWER_PROMPT.format(question="테스트 질문")
    assert "테스트 질문" in rendered
