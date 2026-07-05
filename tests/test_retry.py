import pytest
import requests
import main


def test_request_with_retry_retries_on_ssl_error_then_succeeds(monkeypatch):
    """트랜션트 SSL 오류는 재시도 후 성공하면 결과를 반환한다."""
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise requests.exceptions.SSLError("UNEXPECTED_EOF_WHILE_READING")
        return "ok"

    result = main._request_with_retry(fn, max_attempts=3)

    assert result == "ok"
    assert attempts["n"] == 3


def test_request_with_retry_exhausts_raises_last_exception(monkeypatch):
    """재시도를 모두 소진하면 마지막 네트워크 예외를 그대로 올린다."""
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)

    def fn():
        raise requests.exceptions.ConnectionError("down")

    with pytest.raises(requests.exceptions.ConnectionError):
        main._request_with_retry(fn, max_attempts=2)


def test_request_with_retry_does_not_retry_non_network_errors():
    """네트워크 계열이 아닌 예외는 재시도 없이 즉시 전파한다."""
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("not a network error")

    with pytest.raises(ValueError):
        main._request_with_retry(fn, max_attempts=3)

    assert calls["n"] == 1


def test_request_with_retry_default_rides_out_extended_ssl_blip(monkeypatch):
    """기본 재시도 횟수가 늘어 더 긴 SSL 블립(연속 4회)을 견딘다(6/29 GitHub 블립 대응)."""
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 5:
            raise requests.exceptions.SSLError("eof")
        return type("R", (), {"status_code": 200, "ok": True})()

    resp = main._request_with_retry(fn)  # 기본 max_attempts 사용
    assert resp.status_code == 200
    assert attempts["n"] == 5


def test_request_with_retry_retries_on_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)
    seq = [type("R", (), {"status_code": 503, "ok": False})(),
           type("R", (), {"status_code": 200, "ok": True})()]
    calls = {"n": 0}

    def fn():
        r = seq[calls["n"]]; calls["n"] += 1; return r

    resp = main._request_with_retry(fn, max_attempts=3)
    assert resp.status_code == 200 and calls["n"] == 2
