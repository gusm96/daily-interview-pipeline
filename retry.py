import logging
import time
import requests

logger = logging.getLogger("daily_interview_bot")

# 트랜션트 네트워크 장애(SSL 핸드셰이크 끊김/연결 실패/타임아웃)는 재시도로 흡수.
# api.github.com·Gemini 호출 중 일시적 SSLEOFError로 루틴 A가 통째로 죽는 사례 대응.
_NETWORK_RETRY_EXCEPTIONS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_MAX_ATTEMPTS = 5      # 6/29 GitHub SSL 블립(~30s)을 견디도록 3→5
_RETRY_BACKOFF_CAP = 8       # 초. 지수 백오프 상한(폭주 방지)

def _request_with_retry(fn, max_attempts=_RETRY_MAX_ATTEMPTS, retry_statuses=_RETRYABLE_STATUS):
    """fn()을 호출. 트랜션트 네트워크 예외 또는 재시도 가능 HTTP 상태(429/5xx)면
    상한 있는 지수 백오프로 재시도. 소진 시 마지막 응답 반환 또는 마지막 네트워크 예외 전파."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            resp = fn()
        except _NETWORK_RETRY_EXCEPTIONS as e:
            last_exc = e
            logger.warning("네트워크 오류 재시도 %d/%d: %s", attempt + 1, max_attempts, e)
            if attempt < max_attempts - 1:
                time.sleep(min(2 ** attempt, _RETRY_BACKOFF_CAP))
            continue
        if (
            retry_statuses
            and getattr(resp, "status_code", None) in retry_statuses
            and attempt < max_attempts - 1
        ):
            logger.warning("HTTP %s 재시도 %d/%d", resp.status_code, attempt + 1, max_attempts)
            time.sleep(min(2 ** attempt, _RETRY_BACKOFF_CAP))
            continue
        return resp
    raise last_exc
