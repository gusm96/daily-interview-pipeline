import hashlib
import hmac
import time
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
