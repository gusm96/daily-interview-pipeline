import base64
from datetime import datetime, timedelta, timezone
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
from prompts import CATEGORIES, QUESTION_GENERATION_PROMPT, MODEL_ANSWER_PROMPT, FEEDBACK_PROMPT
import storage








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

_CONFIG_DEFAULT_RE = re.compile(r"<!--\s*config:default=(\d+)\s*-->")
_MENTION_TOKEN_RE = re.compile(r"<@[\w]+>")
_CONFIG_SET_RE = re.compile(r"--default=(-?\d+)")
_FIRST_INT_RE = re.compile(r"-?\d+")


def get_config_default(readme):
    """README의 config 마커에서 기본 생성 개수를 파싱. 없으면 5."""
    m = _CONFIG_DEFAULT_RE.search(readme or "")
    return int(m.group(1)) if m else 5


def set_config_default(readme, n):
    """config:default 마커를 갱신(없으면 README 상단에 삽입). (new_content, n) 반환.
    github_commit_with_retry(lambda c: set_config_default(c, n))로 사용."""
    marker = f"<!-- config:default={n} -->"
    if _CONFIG_DEFAULT_RE.search(readme or ""):
        return _CONFIG_DEFAULT_RE.sub(marker, readme), n
    content = readme if readme.endswith("\n") else readme + "\n"
    return marker + "\n" + content, n


def parse_mention_command(text, bot_user_id=None):
    """app_mention 텍스트를 파싱해 (command, arg) 반환. 범위 검증은 호출부 담당.
    command: 'help' | 'config_set' | 'config_show' | 'question' | 'unknown'."""
    body = _MENTION_TOKEN_RE.sub("", text or "").strip().lower()
    if "help" in body:
        return ("help", None)
    if "config" in body:
        m = _CONFIG_SET_RE.search(body)
        return ("config_set", int(m.group(1))) if m else ("config_show", None)
    if "질문" in body or "question" in body:
        m = _FIRST_INT_RE.search(body)
        return ("question", int(m.group(0)) if m else None)
    return ("unknown", None)


def build_help_text():
    """봇 사용법 안내 텍스트."""
    return (
        "🤖 *Daily Interview Bot 명령어*\n\n"
        "• `@봇 질문` — 질문 추가 생성 (기본 N개)\n"
        "• `@봇 질문 3` — 질문 3개 추가 생성 (1~10)\n"
        "• `@봇 config` — 현재 기본 생성 개수 확인\n"
        "• `@봇 config --default=5` — 기본 생성 개수 설정 (매일 자동 생성 수에도 적용)\n"
        "• `@봇 help` — 이 도움말\n\n"
        "💬 *답변 방법*: 봇이 보낸 질문 메시지에 *스레드 답글*로 답하면 AI가 채점·피드백합니다.\n"
        "ℹ️ 답변하지 않은 질문은 다음 날 오전 7시에 AI 모범답안이 자동 작성됩니다."
    )


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
        date_str = today_kst_iso()

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
    thread_ts 없는 최상위 메시지도 무시(이 함수가 thread_ts 판정 담당).
    봇 멘션 메시지는 명령 경로(handle_app_mention)가 처리하므로 채점 제외(R-1)."""
    if is_bot_or_self(event):
        return None
    if not event.get("thread_ts"):
        return None
    text = (event.get("text") or "").strip()
    bot_user = os.environ.get("SLACK_BOT_USER_ID", "")
    if bot_user and f"<@{bot_user}>" in text:
        return None
    return text or None


logger = logging.getLogger("daily_interview_bot")


# Cloud Functions 런타임은 UTC라 date.today()를 쓰면 07:00 KST 실행이 전날로 찍히는
# off-by-one을 유발한다(2026-06-30 관측). KST는 DST가 없어 고정 오프셋 +9로 다룬다.
KST = timezone(timedelta(hours=9))


def _now_kst():
    """현재 시각(KST). 테스트에서 monkeypatch로 시점을 고정하는 seam."""
    return datetime.now(KST)


def today_kst_iso():
    """KST(Asia/Seoul) 기준 오늘 날짜 ISO 문자열(YYYY-MM-DD)."""
    return _now_kst().date().isoformat()


# 트랜션트 네트워크 장애(SSL 핸드셰이크 끊김/연결 실패/타임아웃)는 재시도로 흡수.
# api.github.com·Gemini 호출 중 일시적 SSLEOFError로 루틴 A가 통째로 죽는 사례 대응.
_NETWORK_RETRY_EXCEPTIONS = (
    requests.exceptions.SSLError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_MAX_ATTEMPTS = 5      # 6/29 GitHub SSL 블립(~30s)을 견디도록 3→5
_RETRY_BACKOFF_CAP = 8       # 초. 지수 백오프 상한(폭주 방지)


def _request_with_retry(fn, max_attempts=_RETRY_MAX_ATTEMPTS, retry_statuses=_RETRYABLE_STATUS):
    """fn()을 호출. 트랜션트 네트워크 예외 또는 재시도 가능 HTTP 상태(429/5xx)면
    상한 있는 지수 백오프로 재시도. 소진 시 마지막 응답 반환 또는 마지막 네트워크 예외 전파."""
    last_exc = None
    for attempt in range(max_attempts):
        try:
            resp = fn()
        except _NETWORK_RETRY_EXCEPTIONS as e:
            last_exc = e
            logger.warning("네트워크 오류 재시도 %d/%d: %s", attempt + 1, max_attempts, e)
            if attempt < max_attempts - 1:
                time.sleep(min(2 ** attempt, _RETRY_BACKOFF_CAP))
            continue
        if (
            retry_statuses
            and getattr(resp, "status_code", None) in retry_statuses
            and attempt < max_attempts - 1
        ):
            logger.warning("HTTP %s 재시도 %d/%d", resp.status_code, attempt + 1, max_attempts)
            time.sleep(min(2 ** attempt, _RETRY_BACKOFF_CAP))
            continue
        return resp
    raise last_exc


GEMINI_TIMEOUT = 30  # 초 (§8.5). 루틴 A는 Scheduler 호출이라 3초 제약 없음. 2.5-flash 지연 여유.
FEEDBACK_THINKING_BUDGET = 1024  # 채점 품질 개선용. 비용 영향 미미(건당 출력 토큰 소량 증가).


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


GITHUB_API = "https://api.github.com"


class GeminiError(Exception):
    pass


class GitHubError(Exception):
    pass


def _github_headers():
    return {
        "Authorization": f"Bearer {os.environ.get('GITHUB_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "daily-interview-pipeline-bot",
    }


def _readme_url():
    owner = os.environ.get("REPO_OWNER", "")
    name = os.environ.get("REPO_NAME", "")
    return f"{GITHUB_API}/repos/{owner}/{name}/contents/README.md"


def _contents_url(path):
    owner = os.environ.get("REPO_OWNER", "")
    name = os.environ.get("REPO_NAME", "")
    return f"{GITHUB_API}/repos/{owner}/{name}/contents/{path}"


def github_get_file(path):
    """path 파일의 (디코딩 content, sha) 반환. 404면 (None, None)."""
    resp = _request_with_retry(
        lambda: requests.get(_contents_url(path), headers=_github_headers(), timeout=10)
    )
    if resp.status_code == 404:
        return None, None
    if not resp.ok:
        raise GitHubError(f"파일 조회 실패({path}): {resp.status_code}")
    data = resp.json()
    return base64.b64decode(data["content"]).decode("utf-8"), data["sha"]


def github_get_readme():
    """README content(디코딩) + sha 반환."""
    resp = _request_with_retry(
        lambda: requests.get(_readme_url(), headers=_github_headers(), timeout=10)
    )
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
        resp = _request_with_retry(
            lambda: requests.put(
                _readme_url(), headers=_github_headers(), json=payload, timeout=10
            )
        )
        if resp.ok:
            logger.info("GitHub 커밋 성공: %s", message)
            return new_content, result
        if resp.status_code == 409:
            logger.warning("GitHub 409 충돌, 재시도 %d/%d", attempt + 1, max_retries)
            time.sleep(2 ** attempt)
            continue
        raise GitHubError(f"커밋 실패: {resp.status_code}")
    raise GitHubError("409 재시도 소진")


def _git_url(suffix):
    owner = os.environ.get("REPO_OWNER", "")
    name = os.environ.get("REPO_NAME", "")
    return f"{GITHUB_API}/repos/{owner}/{name}/git/{suffix}"


def github_commit_files(files, message, branch=None, max_retries=3):
    """files({path: content})를 1커밋으로 원자적 반영. 새 커밋 sha 반환.
    ref 업데이트가 non-fast-forward(422/409)면 처음부터 재시도(C-2 대체)."""
    branch = branch or os.environ.get("REPO_BRANCH", "main")
    headers = _github_headers()
    ref_suffix = f"refs/heads/{branch}"
    for attempt in range(max_retries):
        ref = _request_with_retry(
            lambda: requests.get(_git_url(f"ref/heads/{branch}"), headers=headers, timeout=10))
        if not ref.ok:
            raise GitHubError(f"ref 조회 실패: {ref.status_code}")
        base_sha = ref.json()["object"]["sha"]
        commit = _request_with_retry(
            lambda: requests.get(_git_url(f"commits/{base_sha}"), headers=headers, timeout=10))
        if not commit.ok:
            raise GitHubError(f"base commit 조회 실패: {commit.status_code}")
        base_tree = commit.json()["tree"]["sha"]

        tree_entries = [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in files.items()
        ]
        tree = _request_with_retry(lambda: requests.post(
            _git_url("trees"), headers=headers,
            json={"base_tree": base_tree, "tree": tree_entries}, timeout=10))
        if not tree.ok:
            raise GitHubError(f"tree 생성 실패: {tree.status_code}")
        new_commit = _request_with_retry(lambda: requests.post(
            _git_url("commits"), headers=headers,
            json={"message": message, "tree": tree.json()["sha"], "parents": [base_sha]},
            timeout=10))
        if not new_commit.ok:
            raise GitHubError(f"commit 생성 실패: {new_commit.status_code}")
        new_sha = new_commit.json()["sha"]

        upd = _request_with_retry(lambda: requests.patch(
            _git_url(ref_suffix), headers=headers,
            json={"sha": new_sha, "force": False}, timeout=10))
        if upd.ok:
            logger.info("Git 커밋 성공: %s (%d파일)", message, len(files))
            return new_sha
        if upd.status_code in (409, 422):
            logger.warning("ref 충돌, 재시도 %d/%d", attempt + 1, max_retries)
            time.sleep(2 ** attempt)
            continue
        raise GitHubError(f"ref 업데이트 실패: {upd.status_code}")
    raise GitHubError("ref 충돌 재시도 소진")


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

def validate_env():
    """누락된 필수 환경변수 목록 반환(빈 리스트면 OK)."""
    return [k for k in REQUIRED_ENV if not os.environ.get(k)]


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


def generate_questions(readme, count=5):
    """기존 README를 컨텍스트로 중복 없는 면접 질문 count개 생성.
    [(category, title, question), ...] 반환. temperature=0.1 (정확도).
    category는 responseSchema enum으로 CATEGORIES 값만 나오도록 강제한다."""
    prompt = QUESTION_GENERATION_PROMPT.format(
        count=count,
        categories=", ".join(CATEGORIES),
        readme=readme
    )
    raw = call_gemini(prompt, temperature=0.1, response_schema=_QUESTION_SCHEMA)
    items = json.loads(raw)
    return [(it["category"], it["title"], it["question"]) for it in items]


_MAX_FILL_PER_RUN = 10  # 1회 실행당 모범답안 자동생성 상한(순차 Gemini 누적→타임아웃 방지)


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
        for qid, question in unanswered[:_MAX_FILL_PER_RUN]:
            answer_map[qid] = call_gemini(
                MODEL_ANSWER_PROMPT.format(question=question),
                temperature=0.1,
            )
        github_commit_with_retry(
            partial(fill_unanswered_questions, answer_map), "fill unanswered questions"
        )

    # 2) 신규 질문 생성 (ID 미확정, config default 개수 적용; 1~10 클램프 R-4)
    content, _ = github_get_readme()
    count = max(1, min(10, get_config_default(content)))
    questions = generate_questions(content, count)

    # 3) append 커밋 + 확정 ID 회수
    today = today_kst_iso()
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


def extract_question_from_parent(parent_text):
    """질문 카드 형식(*[Qxxx] 카테고리 | 제목*\n질문)에서 질문 본문만 추출.
    개행이 없으면 형식 불일치로 보고 원문 전체를 그대로 반환한다(방어적 폴백)."""
    if not parent_text:
        return ""
    idx = parent_text.find("\n")
    if idx == -1:
        return parent_text.strip()
    return parent_text[idx + 1:].strip()


# 부모 카드: *[Qxxx] 카테고리 | 제목*  (카테고리는 CATEGORIES 원문 중 하나)
_PARENT_HEADER_RE = re.compile(r"\[Q\d{3,}\]\s*(.+?)\s*\|\s*(.+?)\*")


def parse_parent_header(parent_text):
    """부모 카드에서 (qid, category, title) 추출. 카테고리 미확인 시 (qid, None, None)."""
    qid = parse_question_id(parent_text)
    if not qid:
        return None, None, None
    m = _PARENT_HEADER_RE.search(parent_text or "")
    if not m:
        return qid, None, None
    category = m.group(1).strip()
    if category not in storage.CATEGORY_SLUGS:
        return qid, None, None
    return qid, category, m.group(2).strip()


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

    question = extract_question_from_parent(parent_text)
    feedback = call_gemini(
        FEEDBACK_PROMPT.format(question=question, answer=answer),
        temperature=0.4,
        thinking_budget=FEEDBACK_THINKING_BUDGET,
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


def is_authorized_user(event):
    """SLACK_ALLOWED_USER_IDS(쉼표 구분)가 설정돼 있으면 해당 사용자만 쓰기/생성 명령 허용.
    미설정이면 제한 없음(기존 단일 사용자 동작 유지, R-2)."""
    allowed = os.environ.get("SLACK_ALLOWED_USER_IDS", "").strip()
    if not allowed:
        return True
    ids = {x.strip() for x in allowed.split(",") if x.strip()}
    return event.get("user", "") in ids


def handle_app_mention(event):
    """app_mention 명령 처리: help/config/question/unknown 분기.
    명령 경로는 자동 답변/채점(Gemini fill·grade)을 호출하지 않고 append만 한다."""
    if is_bot_or_self(event):  # 봇/시스템/자기 멘션은 무시(루프 방지, R-3)
        return
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")  # 멘션이 스레드 내부면 그 스레드, 아니면 None(채널)
    command, arg = parse_mention_command(
        event.get("text", ""), os.environ.get("SLACK_BOT_USER_ID", "")
    )

    def reply(text):
        slack_post_message(channel, text, thread_ts=thread_ts)

    # 전체 잠금: 화이트리스트 설정 시 등록 사용자만 모든 멘션 명령 허용(R-2)
    if not is_authorized_user(event):
        reply("이 명령을 실행할 권한이 없습니다. 관리자에게 문의하세요.")
        return

    if command == "help":
        reply(build_help_text())
        return

    if command == "config_show":
        content, _ = github_get_readme()
        reply(f"현재 기본 생성 개수: {get_config_default(content)}개")
        return

    if command == "config_set":
        if arg is None or arg < 1 or arg > 10:
            reply("기본 생성 개수는 1~10 사이로 입력해주세요. 예: `@봇 config --default=5`")
            return
        github_commit_with_retry(lambda c: set_config_default(c, arg), f"config default={arg}")
        reply(f"기본 생성 개수가 {arg}개로 설정되었습니다.")
        return

    if command == "question":
        content, _ = github_get_readme()
        n = arg if arg is not None else get_config_default(content)
        if n < 1 or n > 10:
            reply("질문 개수는 1~10 사이로 입력해주세요. 예: `@봇 질문 3`")
            return
        questions = generate_questions(content, n)
        today = today_kst_iso()
        _, assigned_ids = github_commit_with_retry(
            partial(append_questions, questions, date_str=today), "add questions on demand"
        )
        # 질문은 채널 최상위로 전송 (루틴 B의 [Q###] 매핑 보존)
        for qid, (category, title, question) in zip(assigned_ids, questions):
            try:
                slack_post_message(channel, f"*[{qid}] {category} | {title}*\n{question}")
            except Exception:
                logger.exception("Slack 질문 전송 실패: %s", qid)
        reply(f"질문 {len(assigned_ids)}개를 추가했습니다.")
        return

    reply("모르는 명령입니다. `@봇 help` 를 입력해 사용법을 확인하세요.")


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
            try:
                channel = os.environ.get("SLACK_CHANNEL_ID", "")
                if channel:
                    slack_post_message(
                        channel,
                        "⚠️ 오전 질문 자동 생성(루틴 A)이 실패했습니다. 함수 로그를 확인해주세요.",
                    )
            except Exception:
                logger.exception("루틴 A 실패 알림 전송 실패")
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
        event = payload.get("event", {})
        try:
            if event.get("type") == "app_mention":
                handle_app_mention(event)
            else:
                handle_slack_event(payload)
        except Exception:
            logger.exception("이벤트 처리 실패")
        return ("OK", 200)

    return ("ignored", 200)
















