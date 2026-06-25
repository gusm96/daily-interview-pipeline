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


def test_github_get_readme_retries_on_ssl_error(monkeypatch):
    """github_get_readme는 SSL 끊김을 만나도 재시도해 성공할 수 있어야 한다."""
    import base64
    from unittest.mock import MagicMock, patch

    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(main.time, "sleep", lambda *_: None)

    good = MagicMock()
    good.ok = True
    good.status_code = 200
    good.json.return_value = {
        "content": base64.b64encode(b"# Hello").decode(),
        "sha": "sha1",
    }

    with patch(
        "main.requests.get",
        side_effect=[requests.exceptions.SSLError("eof"), good],
    ):
        content, sha = main.github_get_readme()

    assert content == "# Hello"
    assert sha == "sha1"
