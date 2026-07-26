import base64
import os
import time
import logging
import requests
from retry import _request_with_retry

logger = logging.getLogger("daily_interview_bot")

GITHUB_API = "https://api.github.com"

class GitHubError(Exception):
    pass

def _github_headers():
    return {
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "daily-interview-pipeline-bot",
    }

def _contents_url(path):
    owner = os.environ.get("REPO_OWNER", "")
    name = os.environ.get("REPO_NAME", "")
    return f"{GITHUB_API}/repos/{owner}/{name}/contents/{path}"

def github_get_file(path):
    """path 파일의 (디코딩 content, sha) 반환. 404면 (None, None)."""
    resp = _request_with_retry(
        lambda: requests.get(_contents_url(path), headers=_github_headers(), timeout=10)
    )
    if resp.status_code == 404:
        return None, None
    if not resp.ok:
        raise GitHubError(f"파일 조회 실패({path}): {resp.status_code}")
    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]

def _git_url(suffix):
    owner = os.environ.get("REPO_OWNER", "")
    name = os.environ.get("REPO_NAME", "")
    return f"{GITHUB_API}/repos/{owner}/{name}/git/{suffix}"

def github_commit_files(files, message, branch=None, max_retries=3):
    """files({path: content})를 1커밋으로 원자적 반영. 새 커밋 sha 반환.
    ref 업데이트가 non-fast-forward(422/409)면 처음부터 재시도(C-2 대체)."""
    branch = branch or os.environ.get("REPO_BRANCH", "main")
    headers = _github_headers()
    ref_suffix = f"refs/heads/{branch}"
    for attempt in range(max_retries):
        ref = _request_with_retry(
            lambda: requests.get(_git_url(f"ref/heads/{branch}"), headers=headers, timeout=10))
        if not ref.ok:
            raise GitHubError(f"ref 조회 실패: {ref.status_code}")
        base_sha = ref.json()["object"]["sha"]
        commit = _request_with_retry(
            lambda: requests.get(_git_url(f"commits/{base_sha}"), headers=headers, timeout=10))
        if not commit.ok:
            raise GitHubError(f"base commit 조회 실패: {commit.status_code}")
        base_tree = commit.json()["tree"]["sha"]

        tree_entries = [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in files.items()
        ]
        tree = _request_with_retry(lambda: requests.post(
            _git_url("trees"), headers=headers,
            json={"base_tree": base_tree, "tree": tree_entries}, timeout=10))
        if not tree.ok:
            raise GitHubError(f"tree 생성 실패: {tree.status_code}")
        new_commit = _request_with_retry(lambda: requests.post(
            _git_url("commits"), headers=headers,
            json={"message": message, "tree": tree.json()["sha"], "parents": [base_sha]},
            timeout=10))
        if not new_commit.ok:
            raise GitHubError(f"commit 생성 실패: {new_commit.status_code}")
        new_sha = new_commit.json()["sha"]

        upd = _request_with_retry(lambda: requests.patch(
            _git_url(ref_suffix), headers=headers,
            json={"sha": new_sha, "force": False}, timeout=10))
        if upd.ok:
            logger.info("Git 커밋 성공: %s (%d파일)", message, len(files))
            return new_sha
        if upd.status_code in (409, 422):
            logger.warning("ref 충돌, 재시도 %d/%d", attempt + 1, max_retries)
            time.sleep(2 ** attempt)
            continue
        raise GitHubError(f"ref 업데이트 실패: {upd.status_code}")
    raise GitHubError("ref 충돌 재시도 소진")
