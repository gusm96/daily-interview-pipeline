import base64
from datetime import date
import functions_framework
import hashlib
import hmac
from functools import partial
import json
import logging
import os
import re
import requests
import time
from slack_sdk import WebClient








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


_QID_RE = re.compile(r"\[Q(\d{3,})\]")


def parse_question_id(text):
    """대괄호 앵커 [Q###]에서만 ID를 추출. 없으면 None."""
    if not text:
        return None
    m = _QID_RE.search(text)
    return f"Q{m.group(1)}" if m else None


# 질문 헤더 라인만 스캔 (M-2): "- <details><summary><b>[Q###] ..." 형태만 매칭
_HEADER_ID_RE = re.compile(r"^\s*-\s*<details><summary><b>\[Q(\d{3,})\]", re.MULTILINE)


def next_question_ids(readme, count):
    """README의 질문 헤더 라인만 스캔해 최대 ID+1부터 count개 ID 반환."""
    nums = [int(n) for n in _HEADER_ID_RE.findall(readme or "")]
    start = (max(nums) + 1) if nums else 1
    return [f"Q{n:03d}" for n in range(start, start + count)]


def build_question_block(qid, title, question, date_str):
    """스펙 §7 접이식 질문 블록 마크다운 생성. 토글 요약엔 짧은 제목, 펼치면 전체 질문 + 답변/피드백 칸."""
    return (
        f"- <details><summary><b>[{qid}]</b> {title} <i>({date_str})</i></summary>\n"
        f"  \n"
        f"  **Q.** {question}\n"
        f"  \n"
        f"  ### 🧑‍💻 나의 답변\n"
        f"  \n"
        f"  ### 🤖 AI 피드백\n"
        f"  \n"
        f"  </details>"
    )


AI_AUTO_TAG = "[⚠️ AI 자동 작성 답변 - 미응시]"

# 한 질문 블록 전체를 캡처: summary의 ID + details 내부의 전체 질문(**Q.**) + 답변 본문
_BLOCK_RE = re.compile(
    r"<summary><b>\[(Q\d{3,})\]</b>.*?\*\*Q\.\*\*\s*(.*?)\s*"
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


def update_answer_block(readme, qid, answer, feedback):
    """qid 블록의 '나의 답변'/'AI 피드백' 칸을 치환. (new_content, None) 반환.
    AI 자동 태그가 있으면 제거(C-3). 대상 미존재/치환 실패 시 ValueError(Mi-1)."""
    # 해당 qid 블록의 details 내부 구간만 치환
    block_pat = re.compile(
        r"(-\s*<details><summary><b>\[" + re.escape(qid) + r"\]</b>.*?"
        r"### 🧑‍💻 나의 답변\n)(.*?)(\n\s*### 🤖 AI 피드백\n)(.*?)(\n\s*</details>)",
        re.DOTALL,
    )
    # 각 줄마다 2칸씩 들여쓰기 적용 (빈 줄도 들여쓰기하여 마크다운 양식 일관성 유지)
    indented_answer_lines = [f"  {line}" for line in answer.splitlines()]
    indented_answer = "\n".join(indented_answer_lines) if indented_answer_lines else "  "
    new_answer = f"{indented_answer}\n"

    indented_feedback_lines = [f"  {line}" for line in feedback.splitlines()]
    indented_feedback = "\n".join(indented_feedback_lines) if indented_feedback_lines else "  "
    new_feedback = f"{indented_feedback}\n"

    def _repl(m):
        return m.group(1) + new_answer + m.group(3) + new_feedback + m.group(5)

    new_content, n = block_pat.subn(_repl, readme)
    if n == 0:
        raise ValueError(f"질문 블록을 찾지 못함: {qid}")
    return new_content, None


def append_questions(questions, readme, date_str=None):
    """mutate_fn(partial(append_questions, questions, date_str=...)). 최신 readme 기준으로
    ID를 재할당해 카테고리 섹션 아래 append. (new_content, 할당된 ID 목록) 반환 (C-1).
    카테고리 섹션이 없으면 생성 (Mi-4/S-5)."""
    if date_str is None:
        from datetime import date
        date_str = date.today().isoformat()

    content = readme if readme.endswith("\n") else readme + "\n"
    ids = next_question_ids(content, len(questions))

    for qid, (category, title, question) in zip(ids, questions):
        block = build_question_block(qid, title, question, date_str)
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
    return content, ids


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
        tagged = f"{AI_AUTO_TAG}\n{answer_map[qid]}"
        content, _ = update_answer_block(content, qid, tagged, "(AI 자동 작성 - 검토 필요)")
        filled.append(qid)
    return content, sorted(filled)


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


logger = logging.getLogger("daily_interview_bot")

GEMINI_TIMEOUT = 30  # 초 (§8.5). 루틴 A는 Scheduler 호출이라 3초 제약 없음. 2.5-flash 지연 여유.


def call_gemini(prompt, temperature):
    """Gemini generateContent REST 호출 → 텍스트 추출 → 펜스 제거."""
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
    api_key = os.environ.get("GEMINI_API_KEY", "")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            # 2.5-flash 기본 thinking 비활성화 → 지연·비용 절감 (질문 생성/채점엔 충분)
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=GEMINI_TIMEOUT)
    if not resp.ok:
        logger.error("Gemini API 실패: status=%s", resp.status_code)
        resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    logger.info("Gemini 호출 성공 (model=%s)", model)
    return strip_markdown_fence(text)


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
    [(category, title, question), ...] 반환. temperature=0.1 (정확도)."""
    prompt = (
        "너는 백엔드 기술 면접관이다. 아래 기존 README의 질문들과 절대 중복되지 않는 "
        "한국어 백엔드 면접 질문 5개를 생성하라. 카테고리는 다음에서 골고루 분배: "
        f"{CATEGORIES}. Oracle Java/Spring Reference/AWS 가이드 기준으로 기술적으로 정확해야 한다.\n"
        "각 질문에는 토글 목록에 표시할 5~10단어의 짧은 한국어 요약 제목(title)을 함께 만들어라.\n"
        '출력은 순수 JSON 배열만: [{"category":"<카테고리>","title":"<짧은 제목>","question":"<질문>"}, ...]\n\n'
        f"=== 기존 README ===\n{readme}"
    )
    raw = call_gemini(prompt, temperature=0.1)
    items = json.loads(raw)
    return [(it["category"], it["title"], it["question"]) for it in items]


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
    for qid, (category, title, question) in zip(assigned_ids, questions):
        try:
            slack_post_message(channel, f"*[{qid}] {category} | {title}*\n{question}")
        except Exception:
            logger.exception("Slack 질문 전송 실패: %s", qid)


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


@functions_framework.http
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
















