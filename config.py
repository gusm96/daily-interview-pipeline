import os
import re

_CONFIG_DEFAULT_RE = re.compile(r"<!--\s*config:default=(\d+)\s*-->")
_MENTION_TOKEN_RE = re.compile(r"<@[\w]+>")
_CONFIG_SET_RE = re.compile(r"--default=(-?\d+)")
_FIRST_INT_RE = re.compile(r"-?\d+")

def get_config_default(readme):
    """README의 config 마커에서 기본 생성 개수를 파싱. 없으면 5."""
    m = _CONFIG_DEFAULT_RE.search(readme or "")
    return int(m.group(1)) if m else 5

def set_config_default(readme, n):
    """config:default 마커를 갱신(없으면 README 상단에 삽입). (new_content, n) 반환."""
    marker = f"<!-- config:default={n} -->"
    if _CONFIG_DEFAULT_RE.search(readme or ""):
        return _CONFIG_DEFAULT_RE.sub(marker, readme), n
    content = readme if readme.endswith("\n") else readme + "\n"
    return marker + "\n" + content, n

REQUIRED_ENV = [
    "GITHUB_TOKEN", "REPO_OWNER", "REPO_NAME", "GEMINI_API_KEY",
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID", "SLACK_BOT_USER_ID",
]

def validate_env():
    """누락된 필수 환경변수 목록 반환(빈 리스트면 OK)."""
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]
