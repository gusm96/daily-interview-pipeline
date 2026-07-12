import logging
import os
import time  # noqa: F401  (테스트가 main.time.sleep 패치 seam으로 사용)
import requests  # noqa: F401  (테스트가 main.requests.post 패치 seam으로 사용)
import functions_framework

# 엔트리포인트가 직접 호출하는 협력자
from slack_client import verify_slack_signature, slack_post_message

# 하위 모듈 재노출 — 기존 테스트의 main.X 접근 및 라우팅 호출을 무손상 유지한다.
# ("미사용" import처럼 보여도 재노출 목적이므로 유지한다.)
import handlers  # noqa: F401
from handlers import (  # noqa: F401
    run_generate_routine, handle_slack_event, handle_app_mention,
    _generate_and_stage, _index_text, _find_slug_for_qid, _MAX_FILL_PER_RUN,
)
from retry import _request_with_retry  # noqa: F401
from github_client import github_get_file, github_commit_files  # noqa: F401
from gemini_client import (  # noqa: F401
    call_gemini, strip_markdown_fence, generate_questions, drop_duplicate_titles,
    GeminiError, FEEDBACK_THINKING_BUDGET,
)
from config import (  # noqa: F401
    validate_env, today_kst_iso, get_config_default, set_config_default,
)
from commands import (  # noqa: F401
    is_authorized_user, parse_mention_command, build_help_text,
)
from slack_client import parse_parent_header  # noqa: F401
from prompts import CATEGORIES  # noqa: F401

logger = logging.getLogger("daily_interview_bot")


@functions_framework.http
def daily_interview_bot(request):
    """통합 엔트리포인트. (body, status) 반환 (functions-framework 호환)."""

    # 루틴 A: Scheduler
    if request.args.get("action") == "generate":
        try:
            handlers.run_generate_routine()
            return ("OK: questions generated", 200)
        except Exception:
            logger.exception("루틴 A 실패")
            try:
                channel = os.environ.get("SLACK_CHANNEL_ID", "")
                if channel:
                    slack_post_message(
                        channel,
                        "⚠️ 오전 질문 자동 생성(루틴 A)이 실패했습니다. 함수 로그를 확인해주세요.",
                    )
            except Exception:
                logger.exception("루틴 A 실패 알림 전송 실패")
            return ("error", 500)

    # 슬랙 경로: 서명검증 우선 (C-1)
    if not verify_slack_signature(request):
        logger.warning("Slack 서명검증 실패")
        return ("invalid signature", 401)

    # 재시도 즉시 차단 (Minor-3, 중복 채점 방지)
    if request.headers.get("X-Slack-Retry-Num"):
        return ("OK: retry ignored", 200)

    payload = request.get_json(silent=True) or {}
    if payload.get("type") == "url_verification":
        return (payload.get("challenge", ""), 200)

    if payload.get("type") == "event_callback":
        event = payload.get("event", {})
        try:
            if event.get("type") == "app_mention":
                handlers.handle_app_mention(event)
            else:
                handlers.handle_slack_event(payload)
        except Exception:
            logger.exception("이벤트 처리 실패")
        return ("OK", 200)

    return ("ignored", 200)
