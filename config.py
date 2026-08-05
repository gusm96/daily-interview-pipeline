import os
import re
from datetime import date, datetime, timedelta, timezone

# Cloud Functions 런타임은 UTC라 date.today()를 쓰면 07:00 KST 실행이 전날로 찍히는
# off-by-one을 유발한다(2026-06-30 관측). KST는 DST가 없어 고정 오프셋 +9로 다룬다.
KST = timezone(timedelta(hours=9))


def _now_kst():
    """현재 시각(KST). 테스트에서 monkeypatch로 시점을 고정하는 seam."""
    return datetime.now(KST)


def today_kst_iso():
    """KST(Asia/Seoul) 기준 오늘 날짜 ISO 문자열(YYYY-MM-DD)."""
    return _now_kst().date().isoformat()


def parse_iso_date(value):
    """ISO 날짜 문자열 → date. 파싱 실패하거나 문자열이 아니면 None. 예외를 던지지 않는다.

    정규식 형식 검사가 아니라 실제 파싱으로 검증한다 — '2026-13-99'는
    \\d{4}-\\d{2}-\\d{2} 패턴은 만족하지만 존재하지 않는 날짜다.
    명령 인자 검증(stop YYYY-MM-DD)과 state.json 값 검증이 같은 함수를 써서
    규칙이 두 곳에서 갈라지지 않게 한다.
    """
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def shift_date_iso(iso, days):
    """ISO 날짜에 일수를 더한 ISO 날짜. date + timedelta라 월말·윤년이 자동 처리된다."""
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


def days_left(paused_until, today):
    """남은 정지 일수(오늘 포함). 'forever'면 None. `@봇 config`의 D-N 표시용."""
    if paused_until == "forever":
        return None
    return (date.fromisoformat(paused_until) - date.fromisoformat(today)).days + 1


_MENTION_TOKEN_RE = re.compile(r"<@[\w]+>")
_CONFIG_SET_RE = re.compile(r"--default=(-?\d+)")
_FIRST_INT_RE = re.compile(r"-?\d+")
# stop 인자의 날짜 형식. 실재하는 날짜인지는 parse_iso_date가 따로 검증한다.
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

REQUIRED_ENV = [
    "GITHUB_TOKEN", "REPO_OWNER", "REPO_NAME", "GEMINI_API_KEY",
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID", "SLACK_BOT_USER_ID",
]

def validate_env():
    """누락된 필수 환경변수 목록 반환(빈 리스트면 OK)."""
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]
