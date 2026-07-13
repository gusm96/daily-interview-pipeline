import os
import re
from datetime import datetime, timedelta, timezone

# Cloud Functions 런타임은 UTC라 date.today()를 쓰면 07:00 KST 실행이 전날로 찍히는
# off-by-one을 유발한다(2026-06-30 관측). KST는 DST가 없어 고정 오프셋 +9로 다룬다.
KST = timezone(timedelta(hours=9))


def _now_kst():
    """현재 시각(KST). 테스트에서 monkeypatch로 시점을 고정하는 seam."""
    return datetime.now(KST)


def today_kst_iso():
    """KST(Asia/Seoul) 기준 오늘 날짜 ISO 문자열(YYYY-MM-DD)."""
    return _now_kst().date().isoformat()


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
