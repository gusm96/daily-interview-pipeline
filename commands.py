import os
from config import _MENTION_TOKEN_RE, _CONFIG_SET_RE, _FIRST_INT_RE

def parse_mention_command(text, bot_user_id=None):
    """app_mention 텍스트를 파싱해 (command, arg) 반환. 범위 검증은 호출부 담당.
    command: 'help' | 'config_set' | 'config_show' | 'question' | 'unknown'."""
    body = _MENTION_TOKEN_RE.sub("", text or "").strip().lower()
    if "help" in body:
        return ("help", None)
    if "config" in body:
        m = _CONFIG_SET_RE.search(body)
        return ("config_set", int(m.group(1))) if m else ("config_show", None)
    if "질문" in body or "question" in body:
        m = _FIRST_INT_RE.search(body)
        return ("question", int(m.group(0)) if m else None)
    return ("unknown", None)

def build_help_text():
    """봇 사용법 안내 텍스트."""
    return (
        "🤖 *Daily Interview Bot 명령어*\n\n"
        "• `@봇 질문` — 질문 추가 생성 (기본 N개)\n"
        "• `@봇 질문 3` — 질문 3개 추가 생성 (1~10)\n"
        "• `@봇 config` — 현재 기본 생성 개수 확인\n"
        "• `@봇 config --default=5` — 기본 생성 개수 설정 (매일 자동 생성 수에도 적용)\n"
        "• `@봇 help` — 이 도움말\n\n"
        "💬 *답변 방법*: 봇이 보낸 질문 메시지에 *스레드 답글*로 답하면 AI가 채점·피드백합니다.\n"
        "ℹ️ 답변하지 않은 질문은 다음 날 오전 7시에 AI 모범답안이 자동 작성됩니다."
    )


def is_authorized_user(event):
    """SLACK_ALLOWED_USER_IDS(쉼표 구분)가 설정돼 있으면 해당 사용자만 쓰기/생성 명령 허용.
    미설정이면 제한 없음(기존 단일 사용자 동작 유지, R-2)."""
    allowed = os.environ.get("SLACK_ALLOWED_USER_IDS", "").strip()
    if not allowed:
        return True
    ids = {x.strip() for x in allowed.split(",") if x.strip()}
    return event.get("user", "") in ids
