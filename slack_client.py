import os
import re
import logging
from slack_sdk import WebClient
import storage

logger = logging.getLogger("daily_interview_bot")

_QID_RE = re.compile(r"\[Q(\d{3,})\]")

def parse_question_id(text):
    """대괄호 앵커 [Q###]에서만 ID를 추출. 없으면 None."""
    if not text:
        return None
    m = _QID_RE.search(text)
    return f"Q{m.group(1)}" if m else None

def is_bot_or_self(event):
    """봇/시스템/자기 메시지 판정 (M-6)."""
    if event.get("bot_id") or event.get("subtype"):
        return True
    bot_user = os.environ.get("SLACK_BOT_USER_ID", "")
    return bool(bot_user) and event.get("user") == bot_user

def extract_user_answer(event):
    """스레드 답변인 순수 사용자 텍스트 반환. 무시 대상이면 None.
    thread_ts 없는 최상위 메시지도 무시(이 함수가 thread_ts 판정 담당).
    봇 멘션 메시지는 명령 경로(handle_app_mention)가 처리하므로 채점 제외(R-1)."""
    if is_bot_or_self(event):
        return None
    if not event.get("thread_ts"):
        return None
    text = (event.get("text") or "").strip()
    bot_user = os.environ.get("SLACK_BOT_USER_ID", "")
    if bot_user and f"<@{bot_user}>" in text:
        return None
    return text or None

def _slack_client():
    return WebClient(token=os.environ.get("SLACK_BOT_TOKEN", ""))

def slack_post_message(channel, text, thread_ts=None):
    """슬랙 메시지/스레드 댓글 전송."""
    client = _slack_client()
    if thread_ts:
        client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
    else:
        client.chat_postMessage(channel=channel, text=text)
    logger.info("Slack 전송 성공: channel=%s thread=%s", channel, thread_ts)

def slack_get_thread_parent(channel, thread_ts):
    """스레드의 부모(루트) 메시지 텍스트 반환."""
    client = _slack_client()
    resp = client.conversations_replies(channel=channel, ts=thread_ts, limit=1)
    msgs = resp.get("messages", [])
    return msgs[0]["text"] if msgs else ""

# 부모 카드: *[Qxxx] 카테고리 | 제목*  (카테고리는 CATEGORIES 원문 중 하나)
_PARENT_HEADER_RE = re.compile(r"\[Q\d{3,}\]\s*(.+?)\s*\|\s*(.+?)\*")

def parse_parent_header(parent_text):
    """부모 카드에서 (qid, category, title) 추출. 카테고리 미확인 시 (qid, None, None)."""
    qid = parse_question_id(parent_text)
    if not qid:
        return None, None, None
    m = _PARENT_HEADER_RE.search(parent_text or "")
    if not m:
        return qid, None, None
    category = m.group(1).strip()
    if category not in storage.CATEGORY_SLUGS:
        return qid, None, None
    return qid, category, m.group(2).strip()
