import base64
from unittest.mock import patch, MagicMock
import main


def _content_resp(text, sha, ok=True, status=200):
    r = MagicMock()
    r.ok = ok
    r.status_code = status
    r.json.return_value = {"content": base64.b64encode(text.encode()).decode(), "sha": sha}
    return r


def _env(monkeypatch):
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")


def test_github_get_file_decodes(monkeypatch):
    _env(monkeypatch)
    with patch("main.requests.get", return_value=_content_resp("hi", "s1")):
        content, sha = main.github_get_file("CS/CS.md")
    assert content == "hi" and sha == "s1"


def test_github_get_file_404_returns_none(monkeypatch):
    _env(monkeypatch)
    r = MagicMock(); r.ok = False; r.status_code = 404
    with patch("main.requests.get", return_value=r):
        content, sha = main.github_get_file("CS/Q999.md")
    assert content is None and sha is None
