"""파일 기반 문제 저장소의 순수 렌더/파싱 로직. I/O(네트워크·환경변수) 없음."""
from dataclasses import dataclass

README_WINDOW_DAYS = 7

# 카테고리 원문(prompts.CATEGORIES) → 경로용 ASCII 슬러그. 유일 소스.
CATEGORY_SLUGS = {
    "🖥️ CS (네트워크/OS)": "CS",
    "☕ Java": "Java",
    "🌱 Spring Boot": "SpringBoot",
    "🗄️ Database": "Database",
    "🧩 기타 (Python·FastAPI·Next.js / MSA·CI·CD·대용량·테스트)": "Etc",
}
_SLUG_TO_CATEGORY = {v: k for k, v in CATEGORY_SLUGS.items()}
SLUGS = list(CATEGORY_SLUGS.values())


def slug_for(category):
    """카테고리 원문 → 슬러그. 미등록이면 KeyError."""
    return CATEGORY_SLUGS[category]


def category_for_slug(slug):
    """슬러그 → 카테고리 원문. 미등록이면 KeyError."""
    return _SLUG_TO_CATEGORY[slug]


@dataclass
class Question:
    id: str
    slug: str
    category: str
    title: str
    date: str
    question: str
    answer: str = ""
    feedback: str = ""
    answered: bool = False
    ai_auto: bool = False


def status_label(q):
    """인덱스/렌더용 상태 라벨. answered가 ai_auto보다 우선."""
    if q.answered:
        return "✅ 답변완료"
    if q.ai_auto:
        return "🤖 자동답안"
    return "⬜ 미답변"
