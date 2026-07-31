"""봇 런타임 설정(state.json)의 텍스트↔dict 변환.

GitHub I/O는 호출부(handlers)가 담당하고 이 모듈은 순수 함수만 둔다.
설정 파일이 깨져도 봇이 죽으면 안 되므로 모든 실패 경로는 경고 로그 + 기본값이다.
"""
import json
import logging

logger = logging.getLogger("daily_interview_bot")

DEFAULTS = {"daily_count": 5, "max_fill_per_run": 10, "readme_top_n": 5}

# 허용 범위의 유일한 출처. 검증(사용자 입력 거부)과 클램프(파일 값 보정)가 이 표 하나를 본다.
# max_fill_per_run 상한 10: 30초(GEMINI_TIMEOUT) × 10 = 300초 = 배포된 함수의 service_timeout.
# readme_top_n 하한 1: 0이면 prune_overflow가 README를 영구히 비운다.
LIMITS = {
    "daily_count": (1, 10),
    "max_fill_per_run": (1, 10),
    "readme_top_n": (1, 15),
}


def in_range(key, value):
    """value가 key의 허용 범위 안인지. LIMITS에 없는 키는 제약이 없으므로 True."""
    if key not in LIMITS:
        return True
    low, high = LIMITS[key]
    return low <= value <= high


def range_text(key):
    """안내 문구에 넣을 '1~10' 형태의 문자열."""
    low, high = LIMITS[key]
    return f"{low}~{high}"


def _note(warnings, message):
    """보정 사실 기록. warnings가 None이면 아무것도 하지 않는다(기존 호출부 호환)."""
    if warnings is not None:
        warnings.append(message)


def parse_state(text, warnings=None):
    """state.json 텍스트 → 설정 dict. 예외를 던지지 않는다.

    - 없는 키는 기본값으로 채운다.
    - 모르는 키는 보존한다(구버전 배포가 신버전 필드를 지우지 않도록).
    - 알려진 키의 타입이 기본값과 다르면 그 키만 기본값으로 되돌린다.
    - 타입이 맞으면 LIMITS 범위로 클램프한다.

    warnings에 리스트를 넘기면 값을 보정한 사실이 사람이 읽을 문자열로 쌓인다.
    파일 자체는 고치지 않는다 — 읽기 경로가 쓰기를 하지 않는다.
    """
    if not text:
        return dict(DEFAULTS)
    try:
        loaded = json.loads(text)
    except ValueError:
        logger.warning("state.json 파싱 실패, 기본값 사용")
        _note(warnings, "state.json을 읽지 못해 전체 기본값을 사용했습니다")
        return dict(DEFAULTS)
    if not isinstance(loaded, dict):
        logger.warning("state.json 최상위가 객체가 아님(%s), 기본값 사용",
                       type(loaded).__name__)
        _note(warnings, "state.json 최상위가 객체가 아니라 전체 기본값을 사용했습니다")
        return dict(DEFAULTS)

    state = {**DEFAULTS, **loaded}
    for key, default in DEFAULTS.items():
        # type() is 비교: isinstance(True, int)가 참이라 bool이 int로 새는 것을 막는다.
        if type(state[key]) is not type(default):
            logger.warning("state.json 키 타입 불일치(%s=%r), 기본값 %r 사용",
                           key, state[key], default)
            _note(warnings, f"{key}={state[key]!r} → {default} (타입 오류, 기본값 사용)")
            state[key] = default
            continue  # 기본값은 항상 범위 안이므로 클램프를 다시 볼 필요가 없다
        if not in_range(key, state[key]):
            low, high = LIMITS[key]
            clamped = max(low, min(high, state[key]))
            logger.warning("state.json 키 범위 초과(%s=%r), %r로 보정",
                           key, state[key], clamped)
            _note(warnings, f"{key}={state[key]} → {clamped} (허용 {range_text(key)})")
            state[key] = clamped
    return state


def render_state(state):
    """설정 dict → 커밋할 state.json 텍스트.

    사람이 저장소에서 읽고 직접 고칠 수 있어야 하므로 들여쓰기를 유지하고,
    카테고리 원문이 한글이라 ensure_ascii=False를 쓴다. sort_keys는 키 순서 차이로
    생기는 헛된 diff를 막는다.
    """
    return json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
