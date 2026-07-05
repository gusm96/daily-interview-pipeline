"""파일 기반 문제 저장소의 순수 렌더/파싱 로직. I/O(네트워크·환경변수) 없음."""
import re
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


_META_RE = re.compile(
    r"<!--\s*meta id=(?P<id>Q\d{3,}) slug=(?P<slug>\w+) date=(?P<date>[\d-]+) "
    r"answered=(?P<answered>true|false) ai_auto=(?P<ai>true|false)\s*-->"
)
# 파일 본문 파싱: 질문 / 나의 답변 / AI 피드백 구획
_QFILE_BODY_RE = re.compile(
    r"\*\*Q\.\*\*\s*(?P<question>.*?)\s*"
    r"## 🧑‍💻 나의 답변\s*(?P<answer>.*?)\s*"
    r"## 🤖 AI 피드백\s*(?P<feedback>.*?)\s*$",
    re.DOTALL,
)


def render_question_file(q):
    """Question → 개별 문제 파일 마크다운. parse_question_file과 왕복 가능."""
    meta = (f"<!-- meta id={q.id} slug={q.slug} date={q.date} "
            f"answered={str(q.answered).lower()} ai_auto={str(q.ai_auto).lower()} -->")
    answer = q.answer if q.answer else ""
    feedback = q.feedback if q.feedback else ""
    return (
        f"{meta}\n"
        f"# [{q.id}] {q.title}\n\n"
        f"> {q.category} · {q.date}\n\n"
        f"**Q.** {q.question}\n\n"
        f"## 🧑‍💻 나의 답변\n\n{answer}\n\n"
        f"## 🤖 AI 피드백\n\n{feedback}\n"
    )


def parse_question_file(text):
    """개별 문제 파일 마크다운 → Question. 포맷 불일치 시 ValueError."""
    m = _META_RE.search(text)
    if not m:
        raise ValueError("문제 파일 메타를 찾지 못함")
    title_m = re.search(r"#\s*\[Q\d{3,}\]\s*(.+)", text)
    body_m = _QFILE_BODY_RE.search(text)
    if not title_m or not body_m:
        raise ValueError("문제 파일 본문 파싱 실패")
    slug = m.group("slug")
    return Question(
        id=m.group("id"),
        slug=slug,
        category=category_for_slug(slug),
        title=title_m.group(1).strip(),
        date=m.group("date"),
        question=body_m.group("question").strip(),
        answer=body_m.group("answer").strip(),
        feedback=body_m.group("feedback").strip(),
        answered=m.group("answered") == "true",
        ai_auto=m.group("ai") == "true",
    )


_INDEX_ROW_RE = re.compile(
    r"^\|\s*\[(Q\d{3,})\]\(\./\1\.md\)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|$",
    re.MULTILINE,
)
_QID_ANY_RE = re.compile(r"\bQ(\d{3,})\b")


def _index_rows(index_text):
    """인덱스 텍스트 → [(qid, title, date, status)] (등장 순서)."""
    return [(m.group(1), m.group(2), m.group(3), m.group(4))
            for m in _INDEX_ROW_RE.finditer(index_text or "")]


def _render_index(slug, category, rows):
    rows = sorted(rows, key=lambda r: r[0], reverse=True)  # ID 내림차순
    header = f"# {category} — 전체 문제 (총 {len(rows)}개)\n\n"
    table = "| ID | 제목 | 출제일 | 상태 |\n| --- | --- | --- | --- |\n"
    body = "".join(
        f"| [{qid}](./{qid}.md) | {title} | {date} | {status} |\n"
        for qid, title, date, status in rows
    )
    return header + table + body


def upsert_index_row(index_text, slug, category, qid, title, date, status):
    """qid 행을 추가하거나 갱신한 인덱스 텍스트 반환(총 개수·정렬 유지)."""
    rows = [r for r in _index_rows(index_text) if r[0] != qid]
    rows.append((qid, title, date, status))
    return _render_index(slug, category, rows)


def next_question_ids(index_texts, count):
    """여러 인덱스 텍스트에서 최대 Q### + 1부터 count개 ID 반환."""
    nums = [int(n) for t in index_texts for n in _QID_ANY_RE.findall(t or "")]
    start = (max(nums) + 1) if nums else 1
    return [f"Q{n:03d}" for n in range(start, start + count)]


_Q_START = "<!-- questions:start -->"
_Q_END = "<!-- questions:end -->"

EMPTY_README = (
    "<!-- config:default=5 -->\n"
    "# daily-interview-pipeline\n"
    "GCP Cloud Functions & Gemini API를 이용해 매일 아침 자동으로 빌드되는 "
    "백엔드 기술 면접 독학 저장소\n\n"
    "📚 카테고리별 전체 문제: "
    "[CS](./CS/CS.md) · [Java](./Java/Java.md) · [SpringBoot](./SpringBoot/SpringBoot.md) · "
    "[Database](./Database/Database.md) · [Etc](./Etc/Etc.md)\n\n"
    "## 최근 7일 문제\n\n"
    f"{_Q_START}\n{_Q_END}\n"
)


def build_readme_toggle(q):
    """Question → README 롤링 윈도우용 토글(리스트 아이템). 첫 줄에 기계 판독 마커."""
    answer = q.answer if q.answer else ""
    feedback = q.feedback if q.feedback else ""
    ans_block = "\n".join(f"  {ln}" for ln in answer.splitlines()) if answer else "  "
    fb_block = "\n".join(f"  {ln}" for ln in feedback.splitlines()) if feedback else "  "
    return (
        f"- <!-- q {q.id} {q.slug} {q.date} --><details>"
        f"<summary><b>[{q.id}]</b> {q.category} | {q.title} <i>({q.date})</i></summary>\n"
        f"  \n"
        f"  **Q.** {q.question}\n"
        f"  \n"
        f"  ### 🧑‍💻 나의 답변\n{ans_block}\n"
        f"  \n"
        f"  ### 🤖 AI 피드백\n{fb_block}\n"
        f"  \n"
        f"  📄 [전체 보기](./{q.slug}/{q.id}.md)\n"
        f"  </details>"
    )


def insert_toggle(readme, toggle):
    """questions:start 바로 아래에 토글을 삽입(최신 먼저)."""
    anchor = _Q_START + "\n"
    idx = readme.index(anchor) + len(anchor)
    return readme[:idx] + toggle + "\n" + readme[idx:]
