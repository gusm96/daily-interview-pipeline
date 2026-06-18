# Daily Interview Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** GCP Cloud Functions에서 구동되는 Slack 양방향 면접 챌린지 봇(`main.py`)을 TDD로 구현한다. 매일 질문 배달(루틴 A)과 Slack 스레드 답변 실시간 채점(루틴 B)을 단일 엔트리포인트에서 분기 처리한다.

**Architecture:** 단일 `main.py`에 책임별 순수 함수 + I/O 래퍼를 모듈화한다. GitHub 커밋은 `github_commit_with_retry(mutate_fn, message)` — `(content)->(new_content, result)` 단항 콜백 + 409 재시도로 동시성/멱등성을 보장한다. 질문은 전역 누적 ID(`[Q001]`)로 Slack↔README를 매핑한다.

**Tech Stack:** Python 3.11+, functions-framework, requests(Gemini REST + GitHub REST), slack-sdk, pytest(+unittest.mock).

**Spec:** `docs/superpowers/specs/2026-06-18-daily-interview-bot-design.md`

---

## File Structure

| 파일 | 책임 |
|---|---|
| `main.py` | 엔트리포인트 + 모든 봇 함수(라우팅/서명검증/루틴 A·B/외부 API 래퍼/마크다운 처리). prompt.md 요구상 단일 파일. |
| `requirements.txt` | 런타임 의존성(functions-framework, requests, slack-sdk) |
| `requirements-dev.txt` | `pytest` |
| `README.md` | 카테고리 고정 섹션 골격(스펙 §7) |
| `tests/test_markdown.py` | 순수 마크다운 함수 테스트(fence/id/append/update/find/build) |
| `tests/test_slack.py` | 서명검증·이벤트 필터 테스트 |
| `tests/test_github.py` | get/commit-with-retry(409 재시도) 테스트 |
| `tests/test_gemini.py` | call_gemini 응답 파싱 테스트 |
| `tests/test_routing.py` | 엔트리포인트 분기 테스트 |
| `tests/conftest.py` | 공용 픽스처(샘플 README 등) |

> **규약**: `main.py`의 모든 함수는 import 가능해야 한다(테스트가 `import main` 후 호출). 모듈 최상단에서 환경변수를 읽지 말 것(함수 내부에서 `os.environ.get` 호출) — import 시 실패 방지.

---

## Task 0: 프로젝트 스캐폴딩

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `tests/__init__.py`, `tests/conftest.py`
- Modify: `README.md`

- [ ] **Step 1: requirements 파일 작성**

`requirements.txt`:
```
functions-framework==3.*
requests==2.*
slack-sdk==3.*
```

`requirements-dev.txt`:
```
-r requirements.txt
pytest==8.*
```

- [ ] **Step 2: dev 의존성 설치**

Run: `pip install -r requirements-dev.txt`
Expected: pytest, functions-framework, requests, slack-sdk 설치 성공.

- [ ] **Step 3: README 골격 작성 (스펙 §7)**

`README.md` 전체를 다음으로 교체:
```markdown
# daily-interview-pipeline
GCP Cloud Functions & Gemini API를 이용해 매일 아침 자동으로 빌드되는 백엔드 기술 면접 독학 저장소

## 🖥️ CS (네트워크/OS)

## ☕ Java

## 🌱 Spring Boot

## 🗄️ Database

## ⭐ 우대조건 (MSA / CI·CD / 대용량 트래픽 / 테스트)
```

- [ ] **Step 4: 테스트 픽스처 작성**

`tests/__init__.py`: (빈 파일)

`tests/conftest.py`:
```python
import pytest

SAMPLE_README = """# daily-interview-pipeline
설명 줄

## 🖥️ CS (네트워크/OS)

- **[Q001] Q. TCP와 UDP의 차이는?** _(2026-06-17)_
  <details>
  <summary>💡 나의 답변 및 AI 피드백 보기/접기</summary>

  ### 🧑‍💻 나의 답변
  TCP는 연결지향이고 UDP는 비연결입니다.

  ### 🤖 AI 피드백
  좋은 답변입니다.
  </details>

- **[Q002] Q. OSI 7계층을 설명하라.** _(2026-06-17)_
  <details>
  <summary>💡 나의 답변 및 AI 피드백 보기/접기</summary>

  ### 🧑‍💻 나의 답변

  ### 🤖 AI 피드백

  </details>

## ☕ Java

## 🌱 Spring Boot

## 🗄️ Database

## ⭐ 우대조건 (MSA / CI·CD / 대용량 트래픽 / 테스트)
"""


@pytest.fixture
def sample_readme():
    return SAMPLE_README
```
(Q001=답변완료, Q002=미답변 본문 공백 — 이후 태스크에서 사용.)

- [ ] **Step 5: Commit**

```bash
git add requirements.txt requirements-dev.txt README.md tests/
git commit -m "chore: scaffold project, deps, README skeleton, test fixtures"
```

---

## Task 1: `strip_markdown_fence` (펜스 전처리, 스펙 §8.1/§10)

**Files:**
- Create: `main.py`
- Test: `tests/test_markdown.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_markdown.py`:
```python
import main


def test_strip_outer_markdown_fence():
    text = "```markdown\n# Hello\n내용\n```"
    assert main.strip_markdown_fence(text) == "# Hello\n내용"


def test_strip_plain_fence():
    assert main.strip_markdown_fence("```\nhi\n```") == "hi"


def test_strip_preserves_inner_code_block():
    text = "```markdown\n답변\n```java\nint x;\n```\n```"
    # 최외곽 펜스만 제거, 내부 java 코드블록은 보존
    assert main.strip_markdown_fence(text) == "답변\n```java\nint x;\n```"


def test_strip_no_fence_returns_trimmed():
    assert main.strip_markdown_fence("  그냥 텍스트  ") == "그냥 텍스트"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'main'` 또는 `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py` (신규):
```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_markdown.py
git commit -m "feat: add strip_markdown_fence preprocessing"
```

---

## Task 2: `parse_question_id` (ID 추출, 스펙 §5/M-2)

**Files:**
- Modify: `main.py`
- Test: `tests/test_markdown.py`

- [ ] **Step 1: 실패 테스트 추가**

`tests/test_markdown.py`에 추가:
```python
def test_parse_question_id_bracket_anchor():
    assert main.parse_question_id("오늘의 질문 [Q016] 입니다") == "Q016"


def test_parse_question_id_ignores_bare_text():
    # 대괄호 없는 Q001은 무시 (오탐 방지)
    assert main.parse_question_id("Q001 이 뭔가요?") is None


def test_parse_question_id_none_when_absent():
    assert main.parse_question_id("ID 없는 메시지") is None
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_markdown.py::test_parse_question_id_bracket_anchor -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute 'parse_question_id'`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
_QID_RE = re.compile(r"\[Q(\d{3,})\]")


def parse_question_id(text):
    """대괄호 앵커 [Q###]에서만 ID를 추출. 없으면 None."""
    if not text:
        return None
    m = _QID_RE.search(text)
    return f"Q{m.group(1)}" if m else None
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_markdown.py
git commit -m "feat: add parse_question_id with bracket anchor"
```

---

## Task 3: `next_question_ids` (전역 누적 ID, 스펙 §5)

**Files:**
- Modify: `main.py`
- Test: `tests/test_markdown.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_next_question_ids_continues_from_max(sample_readme):
    # sample_readme의 최대 ID는 Q002 → 다음은 Q003~Q005
    assert main.next_question_ids(sample_readme, 3) == ["Q003", "Q004", "Q005"]


def test_next_question_ids_from_empty():
    assert main.next_question_ids("# 제목\n## CS\n", 2) == ["Q001", "Q002"]


def test_next_question_ids_only_scans_headers():
    # 본문/코드블록의 [Q999]는 무시, 헤더 라인만 스캔
    readme = "## CS\n- **[Q005] Q. 질문** _(d)_\n  본문에 [Q999] 언급\n"
    assert main.next_question_ids(readme, 1) == ["Q006"]
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_markdown.py::test_next_question_ids_from_empty -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
# 질문 헤더 라인만 스캔 (M-2): "- **[Q###] ..." 형태만 매칭
_HEADER_ID_RE = re.compile(r"^\s*-\s*\*\*\[Q(\d{3,})\]", re.MULTILINE)


def next_question_ids(readme, count):
    """README의 질문 헤더 라인만 스캔해 최대 ID+1부터 count개 ID 반환."""
    nums = [int(n) for n in _HEADER_ID_RE.findall(readme or "")]
    start = (max(nums) + 1) if nums else 1
    return [f"Q{n:03d}" for n in range(start, start + count)]
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_markdown.py
git commit -m "feat: add next_question_ids global incremental allocation"
```

---

## Task 4: `build_question_block` (질문 블록 생성, 스펙 §7)

**Files:**
- Modify: `main.py`
- Test: `tests/test_markdown.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_build_question_block_format():
    block = main.build_question_block("Q016", "TCP 3-way handshake를 설명하라.", "2026-06-18")
    assert "- **[Q016] Q. TCP 3-way handshake를 설명하라.** _(2026-06-18)_" in block
    assert "<details>" in block
    assert "### 🧑‍💻 나의 답변" in block
    assert "### 🤖 AI 피드백" in block
    assert block.strip().endswith("</details>")
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_markdown.py::test_build_question_block_format -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
def build_question_block(qid, question, date_str):
    """스펙 §7 접이식 질문 블록 마크다운 생성 (답변/피드백 칸 공백)."""
    return (
        f"- **[{qid}] Q. {question}** _({date_str})_\n"
        f"  <details>\n"
        f"  <summary>💡 나의 답변 및 AI 피드백 보기/접기</summary>\n\n"
        f"  ### 🧑‍💻 나의 답변\n\n"
        f"  ### 🤖 AI 피드백\n\n"
        f"  </details>"
    )
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_markdown.py
git commit -m "feat: add build_question_block markdown builder"
```

---

## Task 5: `find_unanswered_questions` (미답변 탐지, 스펙 §7/C-3)

**Files:**
- Modify: `main.py`
- Test: `tests/test_markdown.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
AI_TAG = "[⚠️ AI 자동 작성 답변 - 미응시]"


def test_find_unanswered_returns_empty_answer_only(sample_readme):
    # Q001=답변있음(제외), Q002=공백(포함)
    result = main.find_unanswered_questions(sample_readme)
    ids = [qid for qid, _ in result]
    assert "Q002" in ids
    assert "Q001" not in ids


def test_find_unanswered_includes_question_text(sample_readme):
    result = dict(main.find_unanswered_questions(sample_readme))
    assert result["Q002"] == "OSI 7계층을 설명하라."


def test_find_unanswered_excludes_ai_tagged():
    readme = (
        "## CS\n- **[Q003] Q. 질문3** _(d)_\n  <details>\n"
        "  <summary>s</summary>\n\n  ### 🧑‍💻 나의 답변\n"
        f"  {AI_TAG}\n  AI가 쓴 답.\n\n  ### 🤖 AI 피드백\n\n  </details>\n"
    )
    # AI 태그가 있으면 미답변으로 보지 않음 (재처리 제외)
    assert main.find_unanswered_questions(readme) == []
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_markdown.py::test_find_unanswered_returns_empty_answer_only -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
AI_AUTO_TAG = "[⚠️ AI 자동 작성 답변 - 미응시]"

# 한 질문 블록 전체를 캡처: 헤더 + details 내부
_BLOCK_RE = re.compile(
    r"-\s*\*\*\[(Q\d{3,})\]\s*Q\.\s*(.*?)\*\*.*?"
    r"### 🧑‍💻 나의 답변\s*(.*?)\s*### 🤖 AI 피드백",
    re.DOTALL,
)


def find_unanswered_questions(readme):
    """(qid, 질문텍스트) 목록 반환. 조건: 답변 본문 공백 AND AI 자동 태그 없음 (C-3)."""
    out = []
    for qid, question, answer_body in _BLOCK_RE.findall(readme or ""):
        body = answer_body.strip()
        if body == "" and AI_AUTO_TAG not in answer_body:
            out.append((qid, question.strip()))
    return out
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_markdown.py
git commit -m "feat: add find_unanswered_questions with AI-tag guard"
```

---

## Task 6: `update_answer_block` (답변/피드백 갱신, 스펙 §4/§7/Mi-1/C-3)

**Files:**
- Modify: `main.py`
- Test: `tests/test_markdown.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_update_answer_block_fills_and_returns_tuple(sample_readme):
    new_content, result = main.update_answer_block(
        sample_readme, "Q002", "내 답변입니다", "AI 피드백입니다"
    )
    assert result is None
    assert "내 답변입니다" in new_content
    assert "AI 피드백입니다" in new_content
    # 다른 질문(Q001)은 그대로
    assert "TCP는 연결지향이고" in new_content


def test_update_answer_block_removes_ai_tag():
    readme = (
        "## CS\n- **[Q003] Q. 질문3** _(d)_\n  <details>\n"
        "  <summary>s</summary>\n\n  ### 🧑‍💻 나의 답변\n"
        f"  {main.AI_AUTO_TAG}\n  AI가 쓴 답.\n\n  ### 🤖 AI 피드백\n\n  </details>\n"
    )
    new_content, _ = main.update_answer_block(readme, "Q003", "진짜 답변", "피드백")
    assert main.AI_AUTO_TAG not in new_content
    assert "진짜 답변" in new_content


def test_update_answer_block_missing_id_raises(sample_readme):
    import pytest
    with pytest.raises(ValueError):
        main.update_answer_block(sample_readme, "Q999", "x", "y")
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_markdown.py::test_update_answer_block_fills_and_returns_tuple -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
def update_answer_block(readme, qid, answer, feedback):
    """qid 블록의 '나의 답변'/'AI 피드백' 칸을 치환. (new_content, None) 반환.
    AI 자동 태그가 있으면 제거(C-3). 대상 미존재/치환 실패 시 ValueError(Mi-1)."""
    # 해당 qid 블록의 details 내부 구간만 치환
    block_pat = re.compile(
        r"(-\s*\*\*\[" + re.escape(qid) + r"\]\s*Q\..*?"
        r"### 🧑‍💻 나의 답변\n)(.*?)(\n\s*### 🤖 AI 피드백\n)(.*?)(\n\s*</details>)",
        re.DOTALL,
    )
    new_answer = f"  {answer}\n"
    new_feedback = f"  {feedback}\n"

    def _repl(m):
        return m.group(1) + new_answer + m.group(3) + new_feedback + m.group(5)

    new_content, n = block_pat.subn(_repl, readme)
    if n == 0:
        raise ValueError(f"질문 블록을 찾지 못함: {qid}")
    return new_content, None
```

> 참고: AI 태그는 '나의 답변' 칸 전체를 새 answer로 치환하므로 자연히 제거된다.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_markdown.py
git commit -m "feat: add update_answer_block (idempotent, AI-tag removal, raises on miss)"
```

---

## Task 7: `append_questions` (mutate, ID 재할당, 스펙 §6.A-4/§8.4/C-1)

**Files:**
- Modify: `main.py`
- Test: `tests/test_markdown.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_append_questions_assigns_ids_and_returns_them(sample_readme):
    questions = [
        ("🖥️ CS (네트워크/OS)", "질문A"),
        ("☕ Java", "질문B"),
    ]
    new_content, assigned = main.append_questions(questions, sample_readme, date_str="2026-06-18")
    # sample 최대 Q002 → 다음 Q003, Q004
    assert assigned == ["Q003", "Q004"]
    assert "[Q003] Q. 질문A" in new_content
    assert "[Q004] Q. 질문B" in new_content


def test_append_questions_creates_missing_section():
    readme = "# 제목\n## ☕ Java\n"
    questions = [("🖥️ CS (네트워크/OS)", "새 CS 질문")]
    new_content, assigned = main.append_questions(questions, readme, date_str="2026-06-18")
    assert assigned == ["Q001"]
    assert "## 🖥️ CS (네트워크/OS)" in new_content
    assert "[Q001] Q. 새 CS 질문" in new_content


def test_append_questions_reapply_recomputes_ids():
    # 재적용(409) 시뮬레이션: 이미 Q003이 추가된 최신 content에 다시 append
    readme = "## ☕ Java\n- **[Q003] Q. 기존** _(d)_\n  <details>\n  <summary>s</summary>\n\n  ### 🧑‍💻 나의 답변\n\n  ### 🤖 AI 피드백\n\n  </details>\n"
    questions = [("☕ Java", "질문B")]
    _, assigned = main.append_questions(questions, readme, date_str="2026-06-18")
    assert assigned == ["Q004"]  # 최신 기준 재계산
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_markdown.py::test_append_questions_creates_missing_section -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
def append_questions(questions, readme, date_str=None):
    """mutate_fn(partial(append_questions, questions, date_str=...)). 최신 readme 기준으로
    ID를 재할당해 카테고리 섹션 아래 append. (new_content, 할당된 ID 목록) 반환 (C-1).
    카테고리 섹션이 없으면 생성 (Mi-4/S-5)."""
    if date_str is None:
        from datetime import date
        date_str = date.today().isoformat()

    content = readme if readme.endswith("\n") else readme + "\n"
    ids = next_question_ids(content, len(questions))

    for qid, (category, question) in zip(ids, questions):
        block = build_question_block(qid, question, date_str)
        header = f"## {category}"
        if header in content:
            # 해당 카테고리 섹션 헤더 라인 바로 뒤에 삽입
            idx = content.index(header) + len(header)
            # 헤더 줄 끝(다음 개행)까지 이동
            nl = content.index("\n", idx)
            content = content[: nl + 1] + "\n" + block + "\n" + content[nl + 1 :]
        else:
            # 섹션 신규 생성 후 append
            content = content.rstrip("\n") + f"\n\n{header}\n\n{block}\n"
        # ID 재스캔이 다음 루프 정확성 보장 (이미 ids로 고정 할당했으므로 그대로 사용)
    return content, ids
```

> 주의: `ids`는 호출 시작 시 `content` 기준으로 한 번 계산한다. 같은 호출 내 여러 질문은 연속 ID(Q003,Q004…)를 사용하며, 삽입으로 인한 헤더 라인 스캔은 `next_question_ids`가 헤더만 보므로 영향 없다.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_markdown.py
git commit -m "feat: add append_questions mutate (ID realloc, section bootstrap, returns ids)"
```

---

## Task 8: `fill_unanswered_questions` (mutate, 스펙 §6.A-2/§8.4)

**Files:**
- Modify: `main.py`
- Test: `tests/test_markdown.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_fill_unanswered_injects_tagged_answer(sample_readme):
    answer_map = {"Q002": "AI 모범답안 본문"}
    new_content, filled = main.fill_unanswered_questions(answer_map, sample_readme)
    assert filled == ["Q002"]
    assert main.AI_AUTO_TAG in new_content
    assert "AI 모범답안 본문" in new_content


def test_fill_unanswered_idempotent_skips_answered(sample_readme):
    # Q001은 이미 답변 있음 → 맵에 있어도 건너뜀(멱등)
    answer_map = {"Q001": "덮어쓰면 안됨", "Q002": "정상"}
    new_content, filled = main.fill_unanswered_questions(answer_map, sample_readme)
    assert filled == ["Q002"]
    assert "덮어쓰면 안됨" not in new_content
    assert "TCP는 연결지향이고" in new_content
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_markdown.py::test_fill_unanswered_injects_tagged_answer -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
def fill_unanswered_questions(answer_map, readme):
    """mutate_fn(partial(fill_unanswered_questions, answer_map)). 최신 readme에서 미답변
    (본문 공백 AND AI 태그 없음)인 qid에만, answer_map의 답변을 AI 태그와 함께 주입.
    (new_content, 채운 qid 목록) 반환. 이미 채워진 블록은 건너뜀(멱등, Minor-1)."""
    content = readme
    filled = []
    unanswered_ids = {qid for qid, _ in find_unanswered_questions(content)}
    for qid in unanswered_ids:
        if qid not in answer_map:
            continue
        tagged = f"{AI_AUTO_TAG}\n  {answer_map[qid]}"
        content, _ = update_answer_block(content, qid, tagged, "(AI 자동 작성 - 검토 필요)")
        filled.append(qid)
    return content, sorted(filled)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_markdown.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_markdown.py
git commit -m "feat: add fill_unanswered_questions mutate (idempotent, AI-tagged)"
```

---

## Task 9: `verify_slack_signature` (서명검증, 스펙 §3/§8.3)

**Files:**
- Modify: `main.py`
- Test: `tests/test_slack.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_slack.py`:
```python
import hashlib
import hmac
import time
import main


class FakeRequest:
    def __init__(self, body, headers):
        self._body = body
        self.headers = headers

    def get_data(self):
        return self._body


def _signed_request(secret, body, ts=None):
    ts = ts or str(int(time.time()))
    basestring = f"v0:{ts}:{body.decode()}".encode()
    sig = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return FakeRequest(body, {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig})


def test_verify_valid_signature(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    req = _signed_request("shhh", b'{"ok":true}')
    assert main.verify_slack_signature(req) is True


def test_verify_bad_signature(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    req = _signed_request("wrong", b'{"ok":true}')
    assert main.verify_slack_signature(req) is False


def test_verify_stale_timestamp(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "shhh")
    old = str(int(time.time()) - 60 * 10)  # 10분 전
    req = _signed_request("shhh", b'{"ok":true}', ts=old)
    assert main.verify_slack_signature(req) is False
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_slack.py -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가 (상단 import에 `import hashlib, hmac, os, time` 보강):
```python
import hashlib
import hmac
import os
import time


def verify_slack_signature(request):
    """Slack 서명검증: HMAC-SHA256(v0:{ts}:{body}), 5분 윈도우, 상수시간 비교."""
    secret = os.environ.get("SLACK_SIGNING_SECRET", "")
    ts = request.headers.get("X-Slack-Request-Timestamp", "")
    sig = request.headers.get("X-Slack-Signature", "")
    if not secret or not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > 60 * 5:
            return False
    except ValueError:
        return False
    body = request.get_data()
    if isinstance(body, str):
        body = body.encode()
    basestring = f"v0:{ts}:".encode() + body
    expected = "v0=" + hmac.new(secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_slack.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_slack.py
git commit -m "feat: add verify_slack_signature (HMAC, 5min window, constant-time)"
```

---

## Task 10: `is_bot_or_self` + `extract_user_answer` (이벤트 필터, 스펙 §6.B/M-6)

**Files:**
- Modify: `main.py`
- Test: `tests/test_slack.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_is_bot_or_self_detects_bot(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    assert main.is_bot_or_self({"bot_id": "B1", "user": "U2"}) is True
    assert main.is_bot_or_self({"user": "UBOT"}) is True
    assert main.is_bot_or_self({"subtype": "message_changed", "user": "U2"}) is True
    assert main.is_bot_or_self({"user": "UHUMAN", "text": "hi"}) is False


def test_extract_user_answer_returns_text(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    event = {"user": "UHUMAN", "text": "내 답변", "thread_ts": "123.45", "ts": "123.46"}
    assert main.extract_user_answer(event) == "내 답변"


def test_extract_user_answer_ignores_top_level(monkeypatch):
    # thread_ts 없는 최상위 메시지는 무시
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    event = {"user": "UHUMAN", "text": "최상위", "ts": "123.46"}
    assert main.extract_user_answer(event) is None


def test_extract_user_answer_ignores_bot(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    event = {"user": "UBOT", "text": "봇답", "thread_ts": "1", "ts": "2"}
    assert main.extract_user_answer(event) is None
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_slack.py::test_is_bot_or_self_detects_bot -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
def is_bot_or_self(event):
    """봇/시스템/자기 메시지 판정 (M-6)."""
    if event.get("bot_id") or event.get("subtype"):
        return True
    bot_user = os.environ.get("SLACK_BOT_USER_ID", "")
    return bool(bot_user) and event.get("user") == bot_user


def extract_user_answer(event):
    """스레드 답변인 순수 사용자 텍스트 반환. 무시 대상이면 None.
    thread_ts 없는 최상위 메시지도 무시(이 함수가 thread_ts 판정 담당)."""
    if is_bot_or_self(event):
        return None
    if not event.get("thread_ts"):
        return None
    text = (event.get("text") or "").strip()
    return text or None
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_slack.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_slack.py
git commit -m "feat: add is_bot_or_self and extract_user_answer event filters"
```

---

## Task 11: `call_gemini` (Gemini REST, 스펙 §8.1)

**Files:**
- Modify: `main.py`
- Test: `tests/test_gemini.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_gemini.py`:
```python
from unittest.mock import patch, MagicMock
import main


def _fake_resp(text, status=200):
    r = MagicMock()
    r.status_code = status
    r.ok = status == 200
    r.json.return_value = {
        "candidates": [{"content": {"parts": [{"text": text}]}}]
    }
    return r


def test_call_gemini_parses_and_strips(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    with patch("main.requests.post", return_value=_fake_resp("```markdown\n답변\n```")) as p:
        out = main.call_gemini("프롬프트", temperature=0.1)
    assert out == "답변"
    # generationConfig.temperature 전달 검증
    sent = p.call_args.kwargs["json"]
    assert sent["generationConfig"]["temperature"] == 0.1


def test_call_gemini_uses_model_env(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    with patch("main.requests.post", return_value=_fake_resp("x")) as p:
        main.call_gemini("p", temperature=0.4)
    assert "gemini-2.5-flash" in p.call_args.args[0]
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_gemini.py -v`
Expected: FAIL — `AttributeError` 또는 `import requests` 누락.

- [ ] **Step 3: 최소 구현**

`main.py` 상단 import에 `import requests`, `import logging` 추가 후:
```python
import logging
import requests

logger = logging.getLogger("daily_interview_bot")

GEMINI_TIMEOUT = 8  # 초 (§8.5)


def call_gemini(prompt, temperature):
    """Gemini generateContent REST 호출 → 텍스트 추출 → 펜스 제거."""
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=GEMINI_TIMEOUT)
    if not resp.ok:
        logger.error("Gemini API 실패: status=%s", resp.status_code)
        resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    logger.info("Gemini 호출 성공 (model=%s)", model)
    return strip_markdown_fence(text)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_gemini.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_gemini.py
git commit -m "feat: add call_gemini REST client with fence stripping"
```

---

## Task 12: GitHub 래퍼 + `github_commit_with_retry` (스펙 §8.2/§8.4/C-2)

**Files:**
- Modify: `main.py`
- Test: `tests/test_github.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_github.py`:
```python
import base64
from unittest.mock import patch, MagicMock
import main


def _get_resp(content_str, sha):
    r = MagicMock()
    r.ok = True
    r.status_code = 200
    r.json.return_value = {
        "content": base64.b64encode(content_str.encode()).decode(),
        "sha": sha,
    }
    return r


def _put_resp(status):
    r = MagicMock()
    r.status_code = status
    r.ok = status in (200, 201)
    return r


def test_github_get_readme_decodes(monkeypatch):
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    with patch("main.requests.get", return_value=_get_resp("# Hello", "sha1")):
        content, sha = main.github_get_readme()
    assert content == "# Hello"
    assert sha == "sha1"


def test_commit_with_retry_success_returns_result(monkeypatch):
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    mutate = lambda c: (c + "\nX", ["Q009"])
    with patch("main.requests.get", return_value=_get_resp("base", "sha1")), \
         patch("main.requests.put", return_value=_put_resp(200)) as put:
        new_content, result = main.github_commit_with_retry(mutate, "msg")
    assert result == ["Q009"]
    assert new_content == "base\nX"
    assert put.call_count == 1


def test_commit_with_retry_409_then_success(monkeypatch):
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    # 첫 PUT은 409, 둘째는 200. mutate는 매번 최신 content 기준 재적용.
    calls = {"n": 0}

    def mutate(c):
        return c + "\nappended", ["Qxxx"]

    with patch("main.requests.get", side_effect=[_get_resp("v1", "sha1"), _get_resp("v2", "sha2")]), \
         patch("main.requests.put", side_effect=[_put_resp(409), _put_resp(200)]), \
         patch("main.time.sleep"):
        new_content, result = main.github_commit_with_retry(mutate, "msg", max_retries=3)
    # 둘째 시도는 최신 v2 기준 재적용
    assert new_content == "v2\nappended"
    assert result == ["Qxxx"]


def test_commit_with_retry_exhausts_raises(monkeypatch):
    import pytest
    monkeypatch.setenv("REPO_OWNER", "o")
    monkeypatch.setenv("REPO_NAME", "n")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    with patch("main.requests.get", return_value=_get_resp("v", "s")), \
         patch("main.requests.put", return_value=_put_resp(409)), \
         patch("main.time.sleep"):
        with pytest.raises(main.GitHubError):
            main.github_commit_with_retry(lambda c: (c, None), "msg", max_retries=2)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_github.py -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py` 상단 import에 `import base64` 추가 후:
```python
import base64

GITHUB_API = "https://api.github.com"


class GitHubError(Exception):
    pass


def _github_headers():
    return {
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
    }


def _readme_url():
    owner = os.environ.get("REPO_OWNER", "")
    name = os.environ.get("REPO_NAME", "")
    return f"{GITHUB_API}/repos/{owner}/{name}/contents/README.md"


def github_get_readme():
    """README content(디코딩) + sha 반환."""
    resp = requests.get(_readme_url(), headers=_github_headers(), timeout=10)
    if not resp.ok:
        raise GitHubError(f"README 조회 실패: {resp.status_code}")
    data = resp.json()
    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, data["sha"]


def github_commit_with_retry(mutate_fn, message, max_retries=3):
    """README 조회 → (new, result)=mutate_fn(content) → PUT. 409 시 재조회·재적용·재시도.
    성공 시 (new_content, result) 반환 (C-1/C-2)."""
    for attempt in range(max_retries):
        content, sha = github_get_readme()
        new_content, result = mutate_fn(content)
        payload = {
            "message": message,
            "content": base64.b64encode(new_content.encode("utf-8")).decode(),
            "sha": sha,
        }
        resp = requests.put(_readme_url(), headers=_github_headers(), json=payload, timeout=10)
        if resp.ok:
            logger.info("GitHub 커밋 성공: %s", message)
            return new_content, result
        if resp.status_code == 409:
            logger.warning("GitHub 409 충돌, 재시도 %d/%d", attempt + 1, max_retries)
            time.sleep(2 ** attempt)
            continue
        raise GitHubError(f"커밋 실패: {resp.status_code}")
    raise GitHubError("409 재시도 소진")
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_github.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_github.py
git commit -m "feat: add github_get_readme and github_commit_with_retry (409 optimistic lock)"
```

---

## Task 13: Slack 송신/조회 래퍼 (스펙 §8.3)

**Files:**
- Modify: `main.py`
- Test: `tests/test_slack.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
from unittest.mock import patch, MagicMock


def test_slack_post_message_calls_sdk(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb")
    fake_client = MagicMock()
    with patch("main.WebClient", return_value=fake_client):
        main.slack_post_message("C1", "안녕", thread_ts="123.4")
    fake_client.chat_postMessage.assert_called_once_with(
        channel="C1", text="안녕", thread_ts="123.4"
    )


def test_slack_get_thread_parent_returns_text(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb")
    fake_client = MagicMock()
    fake_client.conversations_replies.return_value = {
        "messages": [{"text": "부모 [Q016] 질문", "ts": "123.4"}]
    }
    with patch("main.WebClient", return_value=fake_client):
        text = main.slack_get_thread_parent("C1", "123.4")
    assert text == "부모 [Q016] 질문"
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_slack.py::test_slack_post_message_calls_sdk -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py` 상단 import에 `from slack_sdk import WebClient` 추가 후:
```python
from slack_sdk import WebClient


def _slack_client():
    return WebClient(token=os.environ.get("SLACK_BOT_TOKEN", ""))


def slack_post_message(channel, text, thread_ts=None):
    """슬랙 메시지/스레드 댓글 전송."""
    client = _slack_client()
    if thread_ts:
        client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
    else:
        client.chat_postMessage(channel=channel, text=text)
    logger.info("Slack 전송 성공: channel=%s thread=%s", channel, thread_ts)


def slack_get_thread_parent(channel, thread_ts):
    """스레드의 부모(루트) 메시지 텍스트 반환."""
    client = _slack_client()
    resp = client.conversations_replies(channel=channel, ts=thread_ts, limit=1)
    msgs = resp.get("messages", [])
    return msgs[0]["text"] if msgs else ""
```

> 테스트의 `thread_ts="123.4"` 케이스에 맞추려면 `slack_post_message`가 thread_ts 있을 때 `chat_postMessage(channel=, text=, thread_ts=)`로 호출해야 한다. 위 구현은 분기하므로 테스트의 `assert_called_once_with(..., thread_ts="123.4")`와 일치.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_slack.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_slack.py
git commit -m "feat: add slack_post_message and slack_get_thread_parent wrappers"
```

---

## Task 14: `validate_env` + `generate_questions` (스펙 §9/§6.A-3/Mi-2)

**Files:**
- Modify: `main.py`
- Test: `tests/test_routing.py`

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_routing.py`:
```python
from unittest.mock import patch
import pytest
import main

REQUIRED = [
    "GITHUB_TOKEN", "REPO_OWNER", "REPO_NAME", "GEMINI_API_KEY",
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID", "SLACK_BOT_USER_ID",
]


def test_validate_env_passes_when_all_set(monkeypatch):
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")
    assert main.validate_env() == []


def test_validate_env_reports_missing(monkeypatch):
    for k in REQUIRED:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    missing = main.validate_env()
    assert "SLACK_BOT_USER_ID" in missing
    assert "GITHUB_TOKEN" not in missing


def test_generate_questions_returns_category_tuples(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    fake = '[{"category":"🖥️ CS (네트워크/OS)","question":"Q1"},{"category":"☕ Java","question":"Q2"}]'
    with patch("main.call_gemini", return_value=fake):
        result = main.generate_questions("기존 readme")
    assert ("🖥️ CS (네트워크/OS)", "Q1") in result
    assert len(result) == 2
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_routing.py::test_validate_env_passes_when_all_set -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py` 상단 import에 `import json` 추가 후:
```python
import json

REQUIRED_ENV = [
    "GITHUB_TOKEN", "REPO_OWNER", "REPO_NAME", "GEMINI_API_KEY",
    "SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET", "SLACK_CHANNEL_ID", "SLACK_BOT_USER_ID",
]

CATEGORIES = [
    "🖥️ CS (네트워크/OS)", "☕ Java", "🌱 Spring Boot",
    "🗄️ Database", "⭐ 우대조건 (MSA / CI·CD / 대용량 트래픽 / 테스트)",
]


def validate_env():
    """누락된 필수 환경변수 목록 반환(빈 리스트면 OK)."""
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]


def generate_questions(readme):
    """기존 README를 컨텍스트로 중복 없는 면접 질문 5개 생성.
    [(category, question), ...] 반환. temperature=0.1 (정확도)."""
    prompt = (
        "너는 백엔드 기술 면접관이다. 아래 기존 README의 질문들과 절대 중복되지 않는 "
        "한국어 백엔드 면접 질문 5개를 생성하라. 카테고리는 다음에서 골고루 분배: "
        f"{CATEGORIES}. Oracle Java/Spring Reference/AWS 가이드 기준으로 기술적으로 정확해야 한다.\n"
        '출력은 순수 JSON 배열만: [{"category":"<카테고리>","question":"<질문>"}, ...]\n\n'
        f"=== 기존 README ===\n{readme}"
    )
    raw = call_gemini(prompt, temperature=0.1)
    items = json.loads(raw)
    return [(it["category"], it["question"]) for it in items]
```

> 유사도 후처리(Mi-2)는 §8.1의 Jaccard 0.6 기준으로 구현 시 보강 가능하나, 1차 구현은 프롬프트 기반 중복 회피 + JSON 파싱으로 한다. (재생성 루프는 후속 개선 항목.)

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_routing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_routing.py
git commit -m "feat: add validate_env and generate_questions"
```

---

## Task 15: `run_generate_routine` (루틴 A 오케스트레이션, 스펙 §6.A)

**Files:**
- Modify: `main.py`
- Test: `tests/test_routing.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
from functools import partial


def test_run_generate_routine_flow(monkeypatch, sample_readme):
    for k in REQUIRED:
        monkeypatch.setenv(k, "x")

    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append(text))
    # Gemini 모범답안 + 질문 생성 모킹
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "AI답안")
    monkeypatch.setattr(main, "generate_questions",
                        lambda r: [("☕ Java", "새 질문1"), ("🗄️ Database", "새 질문2")])

    commits = []

    def fake_commit(mutate_fn, message, max_retries=3):
        new_content, result = mutate_fn(sample_readme)
        commits.append(message)
        return new_content, result

    monkeypatch.setattr(main, "github_commit_with_retry", fake_commit)
    monkeypatch.setattr(main, "github_get_readme", lambda: (sample_readme, "sha"))

    main.run_generate_routine()

    # 미답변(Q002) 채움 커밋 + 질문 append 커밋 = 2회
    assert len(commits) == 2
    # Slack에 질문 2개 개별 전송, ID 포함
    assert len(posted) == 2
    assert any("Q003" in t for t in posted)
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_routing.py::test_run_generate_routine_flow -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가 (`from functools import partial`, `from datetime import date` 보강):
```python
from datetime import date
from functools import partial


def run_generate_routine():
    """루틴 A: 미답변 자동 채움 → 신규 질문 생성/커밋 → Slack 전송."""
    missing = validate_env()
    if missing:
        logger.error("필수 환경변수 누락: %s", missing)
        raise RuntimeError(f"환경변수 누락: {missing}")

    # 1) 미답변 식별 + 모범답안 선생성
    content, _ = github_get_readme()
    unanswered = find_unanswered_questions(content)
    if unanswered:
        answer_map = {}
        for qid, question in unanswered:
            answer_map[qid] = call_gemini(
                f"다음 백엔드 면접 질문의 모범답안을 한국어로 간결히 작성하라.\n질문: {question}",
                temperature=0.1,
            )
        github_commit_with_retry(
            partial(fill_unanswered_questions, answer_map), "fill unanswered questions"
        )

    # 2) 신규 질문 생성 (ID 미확정)
    content, _ = github_get_readme()
    questions = generate_questions(content)

    # 3) append 커밋 + 확정 ID 회수
    today = date.today().isoformat()
    _, assigned_ids = github_commit_with_retry(
        partial(append_questions, questions, date_str=today), "add daily questions"
    )

    # 4) Slack 전송 (확정 ID 사용)
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    for qid, (category, question) in zip(assigned_ids, questions):
        try:
            slack_post_message(channel, f"*[{qid}] {category}*\n{question}")
        except Exception:
            logger.exception("Slack 질문 전송 실패: %s", qid)
```

> 테스트의 `fake_commit`은 `mutate_fn(sample_readme)`를 직접 호출하므로 `partial`로 단항화한 mutate가 올바른 인자 순서로 동작하는지 함께 검증된다.

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_routing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_routing.py
git commit -m "feat: add run_generate_routine (routine A orchestration)"
```

---

## Task 16: `handle_slack_event` (루틴 B 오케스트레이션, 스펙 §6.B)

**Files:**
- Modify: `main.py`
- Test: `tests/test_routing.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
def test_handle_slack_event_grades_and_commits(monkeypatch, sample_readme):
    monkeypatch.setenv("SLACK_CHANNEL_ID", "C1")
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")

    posted = []
    monkeypatch.setattr(main, "slack_post_message",
                        lambda ch, text, thread_ts=None: posted.append((text, thread_ts)))
    monkeypatch.setattr(main, "slack_get_thread_parent",
                        lambda ch, ts: "오늘의 질문 [Q002] OSI 7계층")
    monkeypatch.setattr(main, "call_gemini", lambda p, temperature: "좋은 답변입니다")

    committed = {}

    def fake_commit(mutate_fn, message, max_retries=3):
        new_content, result = mutate_fn(sample_readme)
        committed["msg"] = message
        committed["content"] = new_content
        return new_content, result

    monkeypatch.setattr(main, "github_commit_with_retry", fake_commit)

    payload = {"event": {
        "type": "message", "user": "UHUMAN", "text": "OSI는 7계층입니다",
        "thread_ts": "111.1", "ts": "111.2", "channel": "C1",
    }}
    main.handle_slack_event(payload)

    # 피드백이 해당 스레드로 전송
    assert posted and posted[0][1] == "111.1"
    # README Q002 갱신 커밋
    assert "OSI는 7계층입니다" in committed["content"]
    assert "좋은 답변입니다" in committed["content"]


def test_handle_slack_event_ignores_bot(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_USER_ID", "UBOT")
    called = []
    monkeypatch.setattr(main, "call_gemini", lambda *a, **k: called.append(1))
    payload = {"event": {"type": "message", "user": "UBOT",
                         "text": "x", "thread_ts": "1", "ts": "2", "channel": "C1"}}
    main.handle_slack_event(payload)
    assert called == []  # 봇 메시지는 채점하지 않음
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_routing.py::test_handle_slack_event_ignores_bot -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
def handle_slack_event(payload):
    """루틴 B: 답변 추출 → ID 매핑 → Gemini 채점 → Slack 피드백 + README 갱신."""
    event = payload.get("event", {})
    answer = extract_user_answer(event)
    if not answer:
        logger.info("무시할 이벤트(봇/시스템/최상위/빈 답변)")
        return

    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")
    parent_text = slack_get_thread_parent(channel, thread_ts)
    qid = parse_question_id(parent_text)
    if not qid:
        logger.info("부모 메시지에서 질문 ID를 찾지 못함")
        return

    feedback = call_gemini(
        "너는 백엔드 면접관이다. 아래 답변의 기술적 정확성을 평가하라. 방향이 옳으면 "
        "가볍게 칭찬하고 부족한 키워드를 짚고, 치명적 오개념이면 정중히 교정하고 모범 방향을 제시하라.\n"
        f"답변: {answer}",
        temperature=0.4,
    )

    # 1) Slack 피드백 (해당 스레드)
    try:
        slack_post_message(channel, f"🤖 *AI 피드백*\n{feedback}", thread_ts=thread_ts)
    except Exception:
        logger.exception("Slack 피드백 전송 실패")

    # 2) README 갱신 (멱등 mutate + 409 재시도)
    try:
        github_commit_with_retry(
            lambda c: update_answer_block(c, qid, answer, feedback),
            f"update {qid} answer",
        )
    except Exception:
        logger.exception("README 갱신 실패(불일치 가능): %s", qid)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_routing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_routing.py
git commit -m "feat: add handle_slack_event (routine B orchestration)"
```

---

## Task 17: `daily_interview_bot` 엔트리포인트 (라우팅, 스펙 §3)

**Files:**
- Modify: `main.py`
- Test: `tests/test_routing.py`

- [ ] **Step 1: 실패 테스트 추가**

```python
class FakeReq:
    def __init__(self, args=None, body=b"{}", headers=None, json_data=None):
        self.args = args or {}
        self._body = body
        self.headers = headers or {}
        self._json = json_data

    def get_data(self):
        return self._body

    def get_json(self, silent=False):
        return self._json


def test_entry_routes_generate(monkeypatch):
    called = []
    monkeypatch.setattr(main, "run_generate_routine", lambda: called.append("A"))
    req = FakeReq(args={"action": "generate"})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["A"]


def test_entry_url_verification_after_signature(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    req = FakeReq(json_data={"type": "url_verification", "challenge": "abc"})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert body == "abc"


def test_entry_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: False)
    req = FakeReq(json_data={"type": "event_callback"})
    body, status = main.daily_interview_bot(req)
    assert status == 401


def test_entry_retry_num_short_circuits(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(main, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(headers={"X-Slack-Retry-Num": "1"},
                  json_data={"type": "event_callback", "event": {}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == []  # 재시도는 즉시 200, 처리 안함


def test_entry_routes_event_callback(monkeypatch):
    monkeypatch.setattr(main, "verify_slack_signature", lambda r: True)
    called = []
    monkeypatch.setattr(main, "handle_slack_event", lambda p: called.append("B"))
    req = FakeReq(json_data={"type": "event_callback", "event": {"text": "x"}})
    body, status = main.daily_interview_bot(req)
    assert status == 200
    assert called == ["B"]
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_routing.py::test_entry_routes_generate -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: 최소 구현**

`main.py`에 추가:
```python
def daily_interview_bot(request):
    """통합 엔트리포인트. (body, status) 반환 (functions-framework 호환)."""
    # 루틴 A: Scheduler
    if request.args.get("action") == "generate":
        try:
            run_generate_routine()
            return ("OK: questions generated", 200)
        except Exception:
            logger.exception("루틴 A 실패")
            return ("error", 500)

    # 슬랙 경로: 서명검증 우선 (C-1)
    if not verify_slack_signature(request):
        logger.warning("Slack 서명검증 실패")
        return ("invalid signature", 401)

    # 재시도 즉시 차단 (Minor-3, 중복 채점 방지)
    if request.headers.get("X-Slack-Retry-Num"):
        return ("OK: retry ignored", 200)

    payload = request.get_json(silent=True) or {}
    if payload.get("type") == "url_verification":
        return (payload.get("challenge", ""), 200)

    if payload.get("type") == "event_callback":
        try:
            handle_slack_event(payload)
        except Exception:
            logger.exception("루틴 B 실패")
        return ("OK", 200)

    return ("ignored", 200)
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_routing.py -v`
Expected: PASS.

- [ ] **Step 5: 전체 테스트 + 구문 검증 (스펙 §12)**

Run: `pytest -v && python -m py_compile main.py`
Expected: 전체 PASS, py_compile 에러 없음(출력 없음).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_routing.py
git commit -m "feat: add daily_interview_bot entrypoint routing"
```

---

## Task 18: functions-framework 데코레이터 + 최종 검증

**Files:**
- Modify: `main.py`

- [ ] **Step 1: functions-framework 진입점 데코레이터 추가**

`main.py` 상단 import에 추가:
```python
import functions_framework
```
그리고 `daily_interview_bot` 정의 바로 위에 데코레이터 추가:
```python
@functions_framework.http
def daily_interview_bot(request):
    ...
```

> 데코레이터는 함수 호출 동작을 바꾸지 않으므로 기존 테스트(직접 호출)는 그대로 통과한다.

- [ ] **Step 2: 전체 테스트 + 구문 검증**

Run: `pytest -v && python -m py_compile main.py`
Expected: 전체 PASS, py_compile 무출력(성공).

- [ ] **Step 3: import 헬스체크 (모듈 로드 시 환경변수 미요구 확인)**

Run: `python -c "import main; print('import OK')"`
Expected: `import OK` (환경변수 없이도 import 성공해야 함 — 모듈 최상단에서 env 읽지 않음).

- [ ] **Step 4: Commit**

```bash
git add main.py
git commit -m "feat: wire functions-framework http decorator"
```

---

## Self-Review 결과 (스펙 대조)

- **§3 라우팅(challenge 서명검증 우선, retry-num 차단)** → Task 17 ✅
- **§4 모든 함수** → Task 1~16에 각각 매핑 ✅
- **§5 ID 매핑(parse/next_ids)** → Task 2,3 ✅
- **§6.A 루틴 A(미답변 채움→생성→append→Slack)** → Task 15 ✅
- **§6.B 루틴 B(필터→매핑→채점→피드백→커밋)** → Task 16 ✅
- **§7 마크다운 양식/AI 태그/미답변 판정** → Task 4,5,6 ✅
- **§8.1 Gemini REST/펜스/모델 env** → Task 11 ✅
- **§8.2/§8.4 GitHub get/commit-with-retry(409)** → Task 12 ✅
- **§8.3 Slack 래퍼/서명검증** → Task 9,13 ✅
- **§9 validate_env 8종** → Task 14 ✅
- **§10 로깅** → 각 I/O 함수에 logger 포함 ✅
- **§11 requirements pin** → Task 0 ✅
- **§12 py_compile 검증** → Task 17,18 ✅
- **배포 체크리스트(§9.1)** → 코드 외 운영 설정이므로 구현 범위 밖(README/배포 문서에 후속 기록 권장).

**타입 일관성 확인**: `github_commit_with_retry`는 전 호출부에서 `(content)->(new_content, result)` 단항 mutate를 받고 `(new_content, result)` 반환. `partial(fill_unanswered_questions, answer_map)`→`(answer_map, content)`, `partial(append_questions, questions, date_str=)`→`(questions, content)`, `lambda c: update_answer_block(c, ...)`→`(c=readme, ...)` 모두 §4 시그니처와 일치.

**플레이스홀더 스캔**: 모든 코드 스텝에 실제 코드 포함, TBD/TODO 없음. (유사도 재생성 루프 Mi-2는 1차 구현 범위에서 프롬프트 기반으로 단순화함을 Task 14에 명시.)
