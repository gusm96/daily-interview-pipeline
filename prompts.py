# prompts.py
"""daily-interview-pipeline에서 사용하는 Gemini API 프롬프트 템플릿 및 설정 모음."""

CATEGORIES = [
    "🖥️ CS (네트워크/OS)",
    "☕ Java",
    "🌱 Spring Boot",
    "🗄️ Database",
    "⭐ 우대조건 (MSA / CI·CD / 대용량 트래픽 / 테스트)",
]

# 질문 생성 프롬프트 (JSON 내 중괄호는 .format 호출을 위해 이중 중괄호 {{}}로 이스케이프 처리)
QUESTION_GENERATION_PROMPT = (
    "너는 백엔드 기술 면접관이다. 아래 기존 README의 질문들과 절대 중복되지 않는 "
    "한국어 백엔드 면접 질문 {count}개를 생성하라. 카테고리는 다음에서 골고루 분배: "
    "{categories}. Oracle Java/Spring Reference/AWS 가이드 기준으로 기술적으로 정확해야 한다.\n"
    "각 질문에는 토글 목록에 표시할 5~10단어의 짧은 한국어 요약 제목(title)을 함께 만들어라.\n"
    '출력은 순수 JSON 배열만: [{{"category":"<카테고리>","title":"<짧은 제목>","question":"<질문>"}}, ...]\n\n'
    "=== 기존 README ===\n{readme}"
)

# 모범답안 자동생성 프롬프트
MODEL_ANSWER_PROMPT = (
    "다음 백엔드 면접 질문의 모범답안을 한국어로 간결히 작성하라.\n질문: {question}"
)

# Slack 답변 채점 및 피드백 프롬프트
FEEDBACK_PROMPT = (
    "너는 백엔드 면접관이다. 아래 답변의 기술적 정확성을 평가하라. 방향이 옳으면 "
    "가볍게 칭찬하고 부족한 키워드를 짚고, 치명적 오개념이면 정중히 교정하고 모범 방향을 제시하라.\n"
    "답변: {answer}"
)
