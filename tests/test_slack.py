import hashlib
import hmac
import time
from unittest.mock import patch, MagicMock
import main



class FakeRequest:
    def __init__(self, body, headers):
        self._body = body
        self.headers = headers

    def get_data(self):
        return self._body


def _signed_request(secret, body, ts=None):
    ts = ts or str(int(time.time()))
    basestring = f"v0:{ts}:{body.decode()}".encode()
    sig = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return FakeRequest(body, {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})


def test_verify_valid_signature(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    req = _signed_request("shhh", b'{"ok":true}')
    assert main.verify_slack_signature(req) is True


def test_verify_bad_signature(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    req = _signed_request("wrong", b'{"ok":true}')
    assert main.verify_slack_signature(req) is False


def test_verify_stale_timestamp(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    old = str(int(time.time()) - 60 * 10)  # 10분 전
    req = _signed_request("shhh", b'{"ok":true}', ts=old)
    assert main.verify_slack_signature(req) is False


def test_is_bot_or_self_detects_bot(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    assert main.is_bot_or_self({"bot_id": "B1", "user": "U2"}) is True
    assert main.is_bot_or_self({"user": "UBOT"}) is True
    assert main.is_bot_or_self({"subtype": "message_changed", "user": "U2"}) is True
    assert main.is_bot_or_self({"user": "UHUMAN", "text": "hi"}) is False


def test_extract_user_answer_returns_text(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    event = {"user": "UHUMAN", "text": "내 답변", "thread_ts": "123.45", "ts": "123.46"}
    assert main.extract_user_answer(event) == "내 답변"


def test_extract_user_answer_ignores_top_level(monkeypatch):
    # thread_ts 없는 최상위 메시지는 무시
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    event = {"user": "UHUMAN", "text": "최상위", "ts": "123.46"}
    assert main.extract_user_answer(event) is None


def test_extract_user_answer_ignores_bot(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    event = {"user": "UBOT", "text": "봇답", "thread_ts": "1", "ts": "2"}
    assert main.extract_user_answer(event) is None


def test_slack_post_message_calls_sdk(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb")
    fake_client = MagicMock()
    with patch("main.WebClient", return_value=fake_client):
        main.slack_post_message("C1", "안녕", thread_ts="123.4")
    fake_client.chat_postMessage.assert_called_once_with(
        channel="C1", text="안녕", thread_ts="123.4"
    )


def test_slack_get_thread_parent_returns_text(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb")
    fake_client = MagicMock()
    fake_client.conversations_replies.return_value = {
        "messages": [{"text": "부모 [Q016] 질문", "ts": "123.4"}]
    }
    with patch("main.WebClient", return_value=fake_client):
        text = main.slack_get_thread_parent("C1", "123.4")
    assert text == "부모 [Q016] 질문"


