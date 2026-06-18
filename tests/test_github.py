import base64
from unittest.mock import patch, MagicMock
import main


def _get_resp(content_str, sha):
    r = MagicMock()
    r.ok = True
    r.status_code = 200
    r.json.return_value = {
        "content": base64.b64encode(content_str.encode()).decode(),
        "sha": sha,
    }
    return r


def _put_resp(status):
    r = MagicMock()
    r.status_code = status
    r.ok = status in (200, 201)
    return r


def test_github_get_readme_decodes(monkeypatch):
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    with patch("main.requests.get", return_value=_get_resp("# Hello", "sha1")):
        content, sha = main.github_get_readme()
    assert content == "# Hello"
    assert sha == "sha1"


def test_commit_with_retry_success_returns_result(monkeypatch):
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    mutate = lambda c: (c + "\nX", ["Q009"])
    with patch("main.requests.get", return_value=_get_resp("base", "sha1")), \
         patch("main.requests.put", return_value=_put_resp(200)) as put:
        new_content, result = main.github_commit_with_retry(mutate, "msg")
    assert result == ["Q009"]
    assert new_content == "base\nX"
    assert put.call_count == 1


def test_commit_with_retry_409_then_success(monkeypatch):
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    # 첫 PUT은 409, 둘째는 200. mutate는 매번 최신 content 기준 재적용.
    calls = {"n": 0}

    def mutate(c):
        return c + "\nappended", ["Qxxx"]

    with patch("main.requests.get", side_effect=[_get_resp("v1", "sha1"), _get_resp("v2", "sha2")]), \
         patch("main.requests.put", side_effect=[_put_resp(409), _put_resp(200)]), \
         patch("main.time.sleep"):
        new_content, result = main.github_commit_with_retry(mutate, "msg", max_retries=3)
    # 둘째 시도는 최신 v2 기준 재적용
    assert new_content == "v2\nappended"
    assert result == ["Qxxx"]


def test_commit_with_retry_exhausts_raises(monkeypatch):
    import pytest
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    with patch("main.requests.get", return_value=_get_resp("v", "s")), \
         patch("main.requests.put", return_value=_put_resp(409)), \
         patch("main.time.sleep"):
        with pytest.raises(main.GitHubError):
            main.github_commit_with_retry(lambda c: (c, None), "msg", max_retries=2)
