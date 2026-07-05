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


def _json_resp(payload, ok=True, status=200):
    r = MagicMock(); r.ok = ok; r.status_code = status
    r.json.return_value = payload
    return r


def test_commit_files_happy_path(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("REPO_BRANCH", "main")
    get_seq = [
        _json_resp({"object": {"sha": "commit1"}}),      # get ref
        _json_resp({"tree": {"sha": "tree1"}}),          # get commit
    ]
    post_seq = [
        _json_resp({"sha": "tree2"}, status=201),        # create tree
        _json_resp({"sha": "commit2"}, status=201),      # create commit
    ]
    patch_resp = _json_resp({"object": {"sha": "commit2"}})  # update ref
    with patch("main.requests.get", side_effect=get_seq), \
         patch("main.requests.post", side_effect=post_seq) as post, \
         patch("main.requests.patch", return_value=patch_resp):
        sha = main.github_commit_files({"README.md": "hello", "CS/Q1.md": "q"}, "msg")
    assert sha == "commit2"
    # 트리 생성 호출에 두 파일이 blob content로 포함
    tree_payload = post.call_args_list[0].kwargs["json"]
    paths = {e["path"] for e in tree_payload["tree"]}
    assert paths == {"README.md", "CS/Q1.md"}
    assert all(e["mode"] == "100644" and "content" in e for e in tree_payload["tree"])


def test_commit_files_retries_on_ref_conflict(monkeypatch):
    _env(monkeypatch)
    # 첫 ref 업데이트 422(non-fast-forward) → 처음부터 재시도 후 성공
    get_seq = [
        _json_resp({"object": {"sha": "c1"}}), _json_resp({"tree": {"sha": "t1"}}),
        _json_resp({"object": {"sha": "c1b"}}), _json_resp({"tree": {"sha": "t1b"}}),
    ]
    post_seq = [
        _json_resp({"sha": "t2"}, status=201), _json_resp({"sha": "c2"}, status=201),
        _json_resp({"sha": "t2b"}, status=201), _json_resp({"sha": "c2b"}, status=201),
    ]
    patch_seq = [_json_resp({}, ok=False, status=422), _json_resp({"object": {"sha": "c2b"}})]
    with patch("main.requests.get", side_effect=get_seq), \
         patch("main.requests.post", side_effect=post_seq), \
         patch("main.requests.patch", side_effect=patch_seq), \
         patch("main.time.sleep"):
        sha = main.github_commit_files({"README.md": "x"}, "msg", max_retries=3)
    assert sha == "c2b"
