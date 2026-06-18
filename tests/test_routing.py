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
    fake = '[{"category":"🖥️ CS (네트워크/OS)","question":"Q1"},{"category":"☕ Java","question":"Q2"}]'
    with patch("main.call_gemini", return_value=fake):
        result = main.generate_questions("기존 readme")
    assert ("🖥️ CS (네트워크/OS)", "Q1") in result
    assert len(result) == 2


def test_run_generate_routine_flow(monkeypatch, sample_readme):
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")

    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    # Gemini 모범답안 + 질문 생성 모킹
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "AI답안")
    monkeypatch.setattr(main, "generate_questions",
                        lambda r: [("☕ Java", "새 질문1"), ("🗄️ Database", "새 질문2")])

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


def test_handle_slack_event_grades_and_commits(monkeypatch, sample_readme):
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")

    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    monkeypatch.setattr(main, "slack_get_thread_parent",
                        lambda ch, ts: "오늘의 질문 [Q002] OSI 7계층")
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "좋은 답변입니다")

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



