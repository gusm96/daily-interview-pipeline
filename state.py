"""봇 런타임 설정(state.json)의 텍스트↔dict 변환.

GitHub I/O는 호출부(handlers)가 담당하고 이 모듈은 순수 함수만 둔다.
설정 파일이 깨져도 봇이 죽으면 안 되므로 모든 실패 경로는 경고 로그 + 기본값이다.
"""
import json
import logging

logger = logging.getLogger("daily_interview_bot")

DEFAULTS = {"daily_count": 5}


def parse_state(text):
    """state.json 텍스트 → 설정 dict. 예외를 던지지 않는다.

    - 없는 키는 기본값으로 채운다.
    - 모르는 키는 보존한다(구버전 배포가 신버전 필드를 지우지 않도록).
    - 알려진 키의 타입이 기본값과 다르면 그 키만 기본값으로 되돌린다.
    """
    if not text:
        return dict(DEFAULTS)
    try:
        loaded = json.loads(text)
    except ValueError:
        logger.warning("state.json 파싱 실패, 기본값 사용")
        return dict(DEFAULTS)
    if not isinstance(loaded, dict):
        logger.warning("state.json 최상위가 객체가 아님(%s), 기본값 사용",
                       type(loaded).__name__)
        return dict(DEFAULTS)

    state = {**DEFAULTS, **loaded}
    for key, default in DEFAULTS.items():
        # type() is 비교: isinstance(True, int)가 참이라 bool이 int로 새는 것을 막는다.
        if type(state[key]) is not type(default):
            logger.warning("state.json 키 타입 불일치(%s=%r), 기본값 %r 사용",
                           key, state[key], default)
            state[key] = default
    return state


def render_state(state):
    """설정 dict → 커밋할 state.json 텍스트.

    사람이 저장소에서 읽고 직접 고칠 수 있어야 하므로 들여쓰기를 유지하고,
    카테고리 원문이 한글이라 ensure_ascii=False를 쓴다. sort_keys는 키 순서 차이로
    생기는 헛된 diff를 막는다.
    """
    return json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
