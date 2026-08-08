import os
from config import _MENTION_TOKEN_RE, _CONFIG_SET_RE, _FIRST_INT_RE, _ISO_DATE_RE, _QID_RE
from state import range_text

def parse_mention_command(text, bot_user_id=None):
    """app_mention 텍스트를 파싱해 (command, arg) 반환. 범위 검증은 호출부 담당.
    command: 'help' | 'stop' | 'stop_invalid' | 'start' | 'auto' | 'auto_invalid'
             | 'config_set' | 'config_show' | 'question' | 'unknown'."""
    body = _MENTION_TOKEN_RE.sub("", text or "").strip().lower()
    if "help" in body:
        return ("help", None)
    if "stop" in body or "정지" in body:
        return ("stop", None) if not _stop_arg_text(body) else _parse_stop_arg(body)
    if "start" in body or "재개" in body:
        return ("start", None)
    if "auto" in body or "자동답변" in body:
        m = _QID_RE.search(body)
        # 본문이 소문자로 내려오므로 대문자로 되돌린다. 이걸 빠뜨리면
        # 인덱스·파일 경로 조회가 전부 빗나가고 "문제를 찾지 못했습니다"만 나온다.
        return ("auto", m.group(0).upper()) if m else ("auto_invalid", None)
    if "config" in body:
        m = _CONFIG_SET_RE.search(body)
        return ("config_set", int(m.group(1))) if m else ("config_show", None)
    if "질문" in body or "question" in body:
        m = _FIRST_INT_RE.search(body)
        return ("question", int(m.group(0)) if m else None)
    return ("unknown", None)


def _stop_arg_text(body):
    """stop 명령어를 제거한 나머지 문자열.

    인자 유무를 '숫자를 찾았는가'가 아니라 '잔여 문자열이 비었는가'로 판단하기 위한 것이다.
    숫자 유무로 판단하면 `stop tomorow` 오타가 무기한 정지로 흘러간다.
    """
    return body.replace("stop", " ").replace("정지", " ").strip()


def _parse_stop_arg(body):
    """stop 인자를 (command, arg)로 변환. 날짜는 str, 일수는 int."""
    rest = _stop_arg_text(body)
    # 날짜를 먼저 본다. _FIRST_INT_RE(-?\d+)를 먼저 돌리면 "2026-08-10"에서
    # 2026을 잡아 2026일간 정지가 된다.
    m = _ISO_DATE_RE.search(rest)
    if m:
        return ("stop", m.group(0))
    m = _FIRST_INT_RE.search(rest)
    if m:
        return ("stop", int(m.group(0)))
    # 인자는 있는데 날짜도 숫자도 아니다 → 무기한으로 떨어뜨리지 않고 오류로 되돌린다
    return ("stop_invalid", None)

def build_help_text():
    """봇 사용법 안내 텍스트."""
    return (
        "🤖 *Daily Interview Bot 명령어*\n\n"
        "• `@봇 질문` — 질문 추가 생성 (기본 N개)\n"
        f"• `@봇 질문 3` — 질문 3개 추가 생성 ({range_text('daily_count')})\n"
        "• `@봇 config` — 현재 기본 생성 개수 확인\n"
        "• `@봇 config --default=5` — 기본 생성 개수 설정 (매일 자동 생성 수에도 적용)\n"
        "• `@봇 auto Q001` — 해당 문제의 AI 모범답안 생성 (이미 답변이 있으면 거부)\n"
        f"• `@봇 stop 3` — 3일간 질문 생성 정지 ({range_text('pause_days')})\n"
        "• `@봇 stop 2026-08-10` — 지정일까지 정지\n"
        "• `@봇 stop` / `@봇 start` — 무기한 정지 / 재개\n"
        "• `@봇 help` — 이 도움말\n\n"
        "💬 *답변 방법*: 봇이 보낸 질문 메시지에 *스레드 답글*로 답하면 AI가 채점·피드백합니다.\n"
        "ℹ️ 답변하지 않은 질문은 그대로 보존됩니다. AI 모범답안이 필요하면 `@봇 auto Q001`.\n"
        "⏸️ 정지 중에는 새 질문이 생성되지 않고, 기존 질문은 그대로 보존됩니다."
    )


def is_authorized_user(event):
    """SLACK_ALLOWED_USER_IDS(쉼표 구분)가 설정돼 있으면 해당 사용자만 쓰기/생성 명령 허용.
    미설정이면 제한 없음(기존 단일 사용자 동작 유지, R-2)."""
    allowed = os.environ.get("SLACK_ALLOWED_USER_IDS", "").strip()
    if not allowed:
        return True
    ids = {x.strip() for x in allowed.split(",") if x.strip()}
    return event.get("user", "") in ids
