import json
import logging
import os
import sys
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
    _generate_and_stage, _index_text, _find_slug_for_qid,
)
from retry import _request_with_retry  # noqa: F401
from github_client import github_get_file, github_commit_files  # noqa: F401
from gemini_client import (  # noqa: F401
    call_gemini, strip_markdown_fence, generate_questions, drop_duplicate_titles,
    GeminiError, FEEDBACK_THINKING_BUDGET,
)
from config import (  # noqa: F401
    validate_env, today_kst_iso, shift_date_iso, parse_iso_date, days_left,
)
from state import (  # noqa: F401
    parse_state, render_state, in_range, range_text,
    is_paused, get_paused_until, set_paused_until, PAUSE_FOREVER,
    DEFAULTS as STATE_DEFAULTS, LIMITS as STATE_LIMITS,
)
from commands import (  # noqa: F401
    is_authorized_user, parse_mention_command, build_help_text,
)
from slack_client import parse_parent_header  # noqa: F401
from prompts import CATEGORIES  # noqa: F401

logger = logging.getLogger("daily_interview_bot")


class _CloudLoggingFormatter(logging.Formatter):
    """로그 1건을 Cloud Logging이 파싱하는 JSON 한 줄로 직렬화한다.
    severity 필드가 있어야 콘솔에서 레벨별 필터가 동작한다."""

    def format(self, record):
        return json.dumps(
            {
                "severity": record.levelname,
                "message": super().format(record),  # exc_info가 있으면 트레이스백까지 포함
                "logger": record.name,
            },
            ensure_ascii=False,
        )


def _configure_logging():
    """루트 로거를 stdout + JSON으로 고정한다.

    설정이 없으면 두 가지가 동시에 깨진다.
    1) 루트 로거 기본 레벨이 WARNING이라 logger.info(...)가 전부 버려진다.
    2) 기본 핸들러는 stderr로 나가는데 Cloud Run은 stderr를 severity=ERROR로 분류한다.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_CloudLoggingFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level, logging.INFO))


_configure_logging()


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
    retry_num = request.headers.get("X-Slack-Retry-Num")
    if retry_num:
        # 재시도가 잦아지면 3초 초과가 상시화됐다는 신호다(콜드스타트/외부 API 지연).
        logger.info("Slack 재시도 무시: retry=%s reason=%s",
                    retry_num, request.headers.get("X-Slack-Retry-Reason", "-"))
        return ("OK: retry ignored", 200)

    payload = request.get_json(silent=True) or {}
    if payload.get("type") == "url_verification":
        return (payload.get("challenge", ""), 200)

    # 한 번의 멘션이 app_mention·message 등 여러 이벤트로 배달되므로,
    # "중복처럼 보이는 요청"을 구분하려면 event_id와 이벤트 타입이 필요하다.
    event_obj = payload.get("event") or {}
    logger.info("Slack 이벤트 수신: type=%s event=%s event_id=%s ts=%s",
                payload.get("type"), event_obj.get("type"),
                payload.get("event_id"), event_obj.get("event_ts"))

    if payload.get("type") == "event_callback":
        event = event_obj
        try:
            if event.get("type") == "app_mention":
                handlers.handle_app_mention(event)
            else:
                handlers.handle_slack_event(payload)
        except Exception:
            logger.exception("이벤트 처리 실패")
        return ("OK", 200)

    return ("ignored", 200)
