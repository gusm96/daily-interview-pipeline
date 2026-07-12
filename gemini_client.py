import os
import json
import re
import logging
import requests
from retry import _request_with_retry
import storage
from prompts import CATEGORIES, QUESTION_GENERATION_PROMPT

logger = logging.getLogger("daily_interview_bot")

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", re.DOTALL)

def strip_markdown_fence(text):
    """Gemini 응답을 감싼 최외곽 ```/```markdown 펜스만 제거. 내부 코드블록은 보존.

    문자열 앞뒤 공백을 자르고 정규식을 활용하여 펜스를 파싱하여 제거함.
    """
    if text is None:
        return ""
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    return text.strip()

GEMINI_TIMEOUT = 30  # 초 (§8.5). 루틴 A는 Scheduler 호출이라 3초 제약 없음. 2.5-flash 지연 여유.
FEEDBACK_THINKING_BUDGET = 1024  # 채점 품질 개선용. 비용 영향 미미(건당 출력 토큰 소량 증가).

class GeminiError(Exception):
    pass

def call_gemini(prompt, temperature, response_schema=None, thinking_budget=0):
    """Gemini generateContent REST 호출 → 텍스트 추출 → 펜스 제거.
    response_schema 지정 시 구조화 JSON 출력을 강제(responseMimeType+responseSchema).
    thinking_budget으로 thinkingConfig.thinkingBudget 조절(기본 0, 지연·비용 절감)."""
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    generation_config = {
        "temperature": temperature,
        "thinkingConfig": {"thinkingBudget": thinking_budget},
    }
    if response_schema is not None:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = response_schema
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    resp = _request_with_retry(
        lambda: requests.post(url, headers=headers, json=payload, timeout=GEMINI_TIMEOUT)
    )
    if not resp.ok:
        logger.error("Gemini API 실패: status=%s", resp.status_code)
        resp.raise_for_status()
    data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise GeminiError(f"Gemini 후보 없음 (promptFeedback={data.get('promptFeedback', {})})")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    if not parts or "text" not in parts[0]:
        raise GeminiError(f"Gemini 응답에 text 없음 (finishReason={candidates[0].get('finishReason')})")
    text = parts[0]["text"]
    logger.info("Gemini 호출 성공 (model=%s)", model)
    return strip_markdown_fence(text)

_QUESTION_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "category": {"type": "STRING", "enum": CATEGORIES},
            "title": {"type": "STRING"},
            "question": {"type": "STRING"},
        },
        "required": ["category", "title", "question"],
    },
}

def generate_questions(existing, count=5):
    """기존 질문 전체 제목 목록(existing)을 컨텍스트로 중복 없는 면접 질문 count개 생성.
    [(category, title, question), ...] 반환. temperature=0.1 (정확도).
    category는 responseSchema enum으로 CATEGORIES 값만 나오도록 강제한다."""
    prompt = QUESTION_GENERATION_PROMPT.format(
        count=count,
        categories=", ".join(CATEGORIES),
        existing=existing
    )
    raw = call_gemini(prompt, temperature=0.1, response_schema=_QUESTION_SCHEMA)
    items = json.loads(raw)
    return [(it["category"], it["title"], it["question"]) for it in items]

def drop_duplicate_titles(candidates, existing_titles):
    """정규화 제목이 기존 제목 또는 같은 배치의 앞선 후보와 일치하는 후보를 제거.
    개념 중복은 복습 효과로 수용하고, 글자 단위 복제(Q046~Q055류)만 코드에서 확정 차단한다."""
    seen = {storage.normalize_title(t) for t in existing_titles}
    kept = []
    for category, title, question in candidates:
        key = storage.normalize_title(title)
        if key in seen:
            logger.warning("기존 제목과 중복인 후보 제외: %s", title)
            continue
        seen.add(key)
        kept.append((category, title, question))
    return kept
