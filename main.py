import base64
import hashlib
import hmac
import logging
import os
import re
import requests
import time
from slack_sdk import WebClient





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


# 질문 헤더 라인만 스캔 (M-2): "- **[Q###] ..." 형태만 매칭
_HEADER_ID_RE = re.compile(r"^\s*-\s*\*\*\[Q(\d{3,})\]", re.MULTILINE)


def next_question_ids(readme, count):
    """README의 질문 헤더 라인만 스캔해 최대 ID+1부터 count개 ID 반환."""
    nums = [int(n) for n in _HEADER_ID_RE.findall(readme or "")]
    start = (max(nums) + 1) if nums else 1
    return [f"Q{n:03d}" for n in range(start, start + count)]


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
        tagged = f"{AI_AUTO_TAG}\n  {answer_map[qid]}"
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












