import re

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def strip_markdown_fence(text):
    """Gemini 응답을 감싼 최외곽 ```/```markdown 펜스만 제거. 내부 코드블록은 보존."""
    if text is None:
        return ""
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    return text.strip()


_QID_RE = re.compile(r"\[Q(\d{3,})\]")


def parse_question_id(text):
    """대괄호 앵커 [Q###]에서만 ID를 추출. 없으면 None."""
    if not text:
        return None
    m = _QID_RE.search(text)
    return f"Q{m.group(1)}" if m else None

