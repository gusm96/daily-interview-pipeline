"""파일 기반 문제 저장소의 순수 렌더/파싱 로직. I/O(네트워크·환경변수) 없음."""
import re
from dataclasses import dataclass

README_TOP_N_PER_CATEGORY = 5

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
    if q.ai_auto:
        answer = f"{AI_AUTO_TAG}\n{answer}" if answer else AI_AUTO_TAG
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
    ai_auto = m.group("ai") == "true"
    answer = body_m.group("answer").strip()
    if ai_auto and answer.startswith(AI_AUTO_TAG):
        answer = answer[len(AI_AUTO_TAG):].strip()
    return Question(
        id=m.group("id"),
        slug=slug,
        category=category_for_slug(slug),
        title=title_m.group(1).strip(),
        date=m.group("date"),
        question=body_m.group("question").strip(),
        answer=answer,
        feedback=body_m.group("feedback").strip(),
        answered=m.group("answered") == "true",
        ai_auto=ai_auto,
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


def existing_titles_block(index_texts_by_slug):
    """{slug: index_text} → 카테고리별 전체 제목 목록 텍스트(중복 방지 컨텍스트용). I/O 없음.

    README 윈도우(상위 N개)와 달리 인덱스는 전체 이력을 담으므로, 오래돼서 윈도우 밖으로
    밀려난 질문까지 Gemini가 중복 판단에 참고할 수 있다."""
    lines = []
    for slug in SLUGS:
        category = category_for_slug(slug)
        rows = _index_rows(index_texts_by_slug.get(slug, ""))
        lines.append(f"## {category}")
        if rows:
            lines.extend(f"- {title}" for _qid, title, _date, _status in rows)
        else:
            lines.append("- (없음)")
    return "\n".join(lines)


_TITLE_NORM_RE = re.compile(r"[^0-9a-z가-힣]+")


def normalize_title(title):
    """소문자화 후 영숫자·한글 이외 문자를 제거한 비교 키. 완전 복제 검출 전용."""
    return _TITLE_NORM_RE.sub("", (title or "").lower())


def existing_titles(index_texts_by_slug):
    """{slug: index_text} → 전체 카테고리의 제목 평면 목록(중복 검사용)."""
    return [title for slug in SLUGS
            for _qid, title, _date, _status in _index_rows(index_texts_by_slug.get(slug, ""))]


def next_question_ids(index_texts, count):
    """여러 인덱스 텍스트에서 최대 Q### + 1부터 count개 ID 반환."""
    nums = [int(n) for t in index_texts for n in _QID_ANY_RE.findall(t or "")]
    start = (max(nums) + 1) if nums else 1
    return [f"Q{n:03d}" for n in range(start, start + count)]


_EMPTY_CATEGORY_NOTE = "(이번 주 등록된 문제 없음)"


def _cat_start(slug):
    return f"<!-- questions:{slug}:start -->"


def _cat_end(slug):
    return f"<!-- questions:{slug}:end -->"


EMPTY_README = (
    "<!-- config:default=5 -->\n"
    "# daily-interview-pipeline\n"
    "GCP Cloud Functions & Gemini API를 이용해 매일 아침 자동으로 빌드되는 "
    "백엔드 기술 면접 독학 저장소\n\n"
) + "\n".join(
    f"## {category_for_slug(slug)}\n\n"
    f"{_cat_start(slug)}\n{_EMPTY_CATEGORY_NOTE}\n{_cat_end(slug)}\n"
    f"📄 [{slug} 모든 문제 보기](./{slug}/{slug}.md)\n"
    for slug in SLUGS
)


def build_readme_toggle(q):
    """Question → README 롤링 윈도우용 토글(리스트 아이템). 첫 줄에 기계 판독 마커."""
    answer = q.answer if q.answer else ""
    if q.ai_auto:
        answer = f"{AI_AUTO_TAG}\n{answer}" if answer else AI_AUTO_TAG
    feedback = q.feedback if q.feedback else ""
    ans_block = "\n".join(f"  {ln}" for ln in answer.splitlines()) if answer else "  "
    fb_block = "\n".join(f"  {ln}" for ln in feedback.splitlines()) if feedback else "  "
    return (
        f"- <!-- q {q.id} {q.slug} {q.date} --><details>"
        f"<summary><b>[{q.id}]</b> {q.title} <i>({q.date})</i></summary>\n"
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


_TOGGLE_MARKER_RE = re.compile(r"<!-- q (Q\d{3,}) (\w+) ([\d-]+) -->")


def insert_toggle(readme, toggle):
    """토글 마커의 slug를 읽어 해당 카테고리 섹션 앵커 바로 아래에 삽입(최신 먼저)."""
    slug = _TOGGLE_MARKER_RE.search(toggle).group(2)
    anchor = _cat_start(slug) + "\n"
    idx = readme.index(anchor) + len(anchor)
    rest = readme[idx:]
    if rest.startswith(_EMPTY_CATEGORY_NOTE):
        rest = rest[len(_EMPTY_CATEGORY_NOTE):]
        if rest.startswith("\n"):
            rest = rest[1:]
    return readme[:idx] + toggle + "\n" + rest


def build_readme_window(questions, limit=README_TOP_N_PER_CATEGORY):
    """전체 Question 목록 → 카테고리별 상위 limit개만 반영한 새 README(EMPTY_README 기준 재구성)."""
    by_slug = {}
    for q in questions:
        by_slug.setdefault(q.slug, []).append(q)
    readme = EMPTY_README
    for qs in by_slug.values():
        qs = sorted(qs, key=lambda x: x.id)
        for q in qs[-limit:]:  # 오래된→최신 삽입 = 최신이 위
            readme = insert_toggle(readme, build_readme_toggle(q))
    return readme


AI_AUTO_TAG = "[⚠️ AI 자동 작성 답변 - 미응시]"


# 한 토글(리스트 아이템) 전체: 마커부터 </details>까지
def _toggle_span_re(qid):
    return re.compile(
        r"-\s*<!-- q " + re.escape(qid) + r" \w+ [\d-]+ -->.*?</details>",
        re.DOTALL,
    )


# 토글 내부 답변/피드백 구획 캡처
_TOGGLE_BODY_RE = lambda qid: re.compile(
    r"(-\s*<!-- q " + re.escape(qid) + r" \w+ [\d-]+ -->.*?### 🧑‍💻 나의 답변\n)"
    r"(.*?)(\n\s*### 🤖 AI 피드백\n)(.*?)(\n\s*📄 \[전체 보기\])",
    re.DOTALL,
)


def _indent2(text):
    return "\n".join(f"  {ln}" for ln in text.splitlines()) if text else "  "


def patch_toggle_body(readme, qid, answer, feedback, ai_auto):
    """qid 토글의 답변/피드백 구획을 치환. ai_auto=True면 답변 앞에 AI 태그."""
    body_answer = f"{AI_AUTO_TAG}\n{answer}" if ai_auto else answer
    new_ans = _indent2(body_answer)
    new_fb = _indent2(feedback)

    def _repl(m):
        return m.group(1) + new_ans + m.group(3) + new_fb + m.group(5)

    new_content, n = _TOGGLE_BODY_RE(qid).subn(_repl, readme)
    if n == 0:
        raise ValueError(f"토글을 찾지 못함: {qid}")
    return new_content


def _iter_toggles(readme):
    """(qid, slug, date, full_toggle_text) 목록."""
    out = []
    for m in re.finditer(
        r"-\s*<!-- q (Q\d{3,}) (\w+) ([\d-]+) -->.*?</details>", readme, re.DOTALL
    ):
        out.append((m.group(1), m.group(2), m.group(3), m.group(0)))
    return out


def prune_overflow(readme, limit=README_TOP_N_PER_CATEGORY):
    """카테고리별로 최신 limit개만 남기고 초과분 토글을 제거. 섹션이 비면 플레이스홀더 복구."""
    for slug in SLUGS:
        toggles = [t for qid, s, date, t in _iter_toggles(readme) if s == slug]
        for toggle in toggles[limit:]:
            readme = readme.replace(toggle + "\n", "").replace(toggle, "")
        start, end = _cat_start(slug), _cat_end(slug)
        try:
            s_idx = readme.index(start) + len(start)
            e_idx = readme.index(end, s_idx)
        except ValueError:
            continue
        if readme[s_idx:e_idx].strip() == "":
            readme = readme[:s_idx] + f"\n{_EMPTY_CATEGORY_NOTE}\n" + readme[e_idx:]
    return readme


def scan_window_unanswered(readme):
    """답변 공백 AND AI 태그 없음인 토글 → (qid, slug, date, title, question)."""
    out = []
    for qid, slug, date, toggle in _iter_toggles(readme):
        body = _TOGGLE_BODY_RE(qid).search(readme)
        if not body:
            continue
        answer_seg = body.group(2)
        if answer_seg.strip() != "" or AI_AUTO_TAG in answer_seg:
            continue
        title_m = re.search(r"</b>\s*(.*?)\s*<i>", toggle)
        q_m = re.search(r"\*\*Q\.\*\*\s*(.*?)\s*\n", toggle)
        out.append((qid, slug, date,
                    title_m.group(1) if title_m else "",
                    q_m.group(1).strip() if q_m else ""))
    return out


def marker_info(readme, qid):
    """qid 토글의 (slug, date) 또는 None."""
    m = re.search(r"<!-- q " + re.escape(qid) + r" (\w+) ([\d-]+) -->", readme)
    return (m.group(1), m.group(2)) if m else None


def has_toggle(readme, qid):
    return marker_info(readme, qid) is not None
