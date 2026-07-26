import os
import logging
import storage
from github_client import github_get_file, github_commit_files
from gemini_client import (
    call_gemini, generate_questions, drop_duplicate_titles, FEEDBACK_THINKING_BUDGET,
)
from slack_client import (
    slack_post_message, slack_get_thread_parent,
    extract_user_answer, parse_parent_header, is_bot_or_self,
)
from config import today_kst_iso, get_config_default, set_config_default, validate_env
from commands import parse_mention_command, build_help_text, is_authorized_user
from prompts import MODEL_ANSWER_PROMPT, FEEDBACK_PROMPT

logger = logging.getLogger("daily_interview_bot")


_MAX_FILL_PER_RUN = 10  # 1회 실행당 모범답안 자동생성 상한(순차 Gemini 누적→타임아웃 방지)


def _generate_and_stage(readme, count, today, files=None):
    """신규 질문 count개를 생성해 (files, ids, questions, new_readme) 반환. 채점/모범답안 없음.
    files를 넘기면(예: 루틴 A의 미답변 채움 단계에서 이미 쌓인 파일들) 이어서 누적한다."""
    if files is None:
        files = {}
    index_map = {s: _index_text(files, s) for s in storage.SLUGS}
    # 중복 방지 컨텍스트는 README 윈도우(상위 5개)가 아니라 인덱스 전체 이력을 사용한다.
    questions = generate_questions(storage.existing_titles_block(index_map), count)
    questions = drop_duplicate_titles(questions, storage.existing_titles(index_map))
    ids = storage.next_question_ids(list(index_map.values()), len(questions))
    for qid, (category, title, question) in zip(ids, questions):
        slug = storage.slug_for(category)
        q = storage.Question(qid, slug, category, title, today, question)
        files[f"{slug}/{qid}.md"] = storage.render_question_file(q)
        files[f"{slug}/{slug}.md"] = storage.upsert_index_row(
            _index_text(files, slug), slug, category, qid, title, today, storage.status_label(q))
        readme = storage.insert_toggle(readme, storage.build_readme_toggle(q))
    return files, ids, questions, readme


def run_generate_routine():
    """루틴 A: 미답변 자동 모범답안 + 신규 질문 생성 → 파일/인덱스/README 1커밋 → Slack."""
    missing = validate_env()
    if missing:
        logger.error("필수 환경변수 누락: %s", missing)
        raise RuntimeError(f"환경변수 누락: {missing}")

    readme, _ = github_get_file("README.md")
    if readme is None:
        readme = storage.EMPTY_README
    files = {}
    today = today_kst_iso()

    # 1) 창 안 미답변 → 모범답안 생성 후 README 패치 + 문제 파일 + 인덱스
    for qid, slug, date, title, question in storage.scan_window_unanswered(readme)[:_MAX_FILL_PER_RUN]:
        answer = call_gemini(MODEL_ANSWER_PROMPT.format(question=question), temperature=0.1)
        feedback = "(AI 자동 작성 - 검토 필요)"
        readme = storage.patch_toggle_body(readme, qid, answer, feedback, ai_auto=True)
        category = storage.category_for_slug(slug)
        q = storage.Question(qid, slug, category, title, date, question,
                             answer=answer, feedback=feedback, ai_auto=True)
        files[f"{slug}/{qid}.md"] = storage.render_question_file(q)
        idx_text = _index_text(files, slug)
        files[f"{slug}/{slug}.md"] = storage.upsert_index_row(
            idx_text, slug, category, qid, title, date, storage.status_label(q))

    # 2) 신규 질문 생성 (config default, 1~10 클램프)
    count = max(1, min(10, get_config_default(readme)))
    files, ids, questions, readme = _generate_and_stage(readme, count, today, files)

    # 3) 카테고리별 상위 N개 초과분 prune
    readme = storage.prune_overflow(readme)
    files["README.md"] = readme

    # 4) 1커밋
    github_commit_files(files, "add daily questions")

    # 5) Slack 전송
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    for qid, (category, title, question) in zip(ids, questions):
        try:
            slack_post_message(channel, f"*[{qid}] {category} | {title}*\n{question}")
        except Exception:
            logger.exception("Slack 질문 전송 실패: %s", qid)


def _index_text(files, slug):
    """커밋 대기 중 files dict에 있으면 그걸, 없으면 원격 인덱스를 조회(없으면 '')."""
    path = f"{slug}/{slug}.md"
    if path in files:
        return files[path]
    text, _ = github_get_file(path)
    return text or ""


def _find_slug_for_qid(qid):
    """카테고리 미확인(부모 헤더 파싱 실패)일 때만 실행되는 폴백.
    5개 인덱스를 순차 조회하므로 정상 경로에서는 호출되지 않는다.
    빈도가 높아지면(로그로 측정) 배치 조회 도입을 검토한다. 없으면 None."""
    for slug in storage.SLUGS:
        text, _ = github_get_file(f"{slug}/{slug}.md")
        if text and qid in text:
            return slug
    return None


def handle_slack_event(payload):
    """루틴 B: 답변 추출 → 채점 → Slack 피드백 + 문제 파일/인덱스/README 1커밋."""
    event = payload.get("event", {})
    answer = extract_user_answer(event)
    if not answer:
        logger.info("무시할 이벤트(봇/시스템/최상위/빈 답변)")
        return

    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts")
    parent_text = slack_get_thread_parent(channel, thread_ts)
    qid, category, title = parse_parent_header(parent_text)
    if not qid:
        logger.info("부모 메시지에서 질문 ID를 찾지 못함")
        return

    if category:
        slug = storage.slug_for(category)
    else:
        logger.info("부모 헤더 카테고리 파싱 실패, 인덱스 스캔 폴백: %s", qid)
        slug = _find_slug_for_qid(qid)
    if not slug:
        logger.warning("qid의 카테고리를 찾지 못함: %s", qid)
        return

    qfile_text, _ = github_get_file(f"{slug}/{qid}.md")
    if not qfile_text:
        logger.warning("문제 파일 없음: %s/%s.md", slug, qid)
        return
    q = storage.parse_question_file(qfile_text)

    feedback = call_gemini(
        FEEDBACK_PROMPT.format(question=q.question, answer=answer),
        temperature=0.4,
        thinking_budget=FEEDBACK_THINKING_BUDGET,
    )

    try:
        slack_post_message(channel, f"🤖 *AI 피드백*\n{feedback}", thread_ts=thread_ts)
    except Exception:
        logger.exception("Slack 피드백 전송 실패")

    # 문제 파일 갱신
    q.answer, q.feedback, q.answered, q.ai_auto = answer, feedback, True, False
    files = {f"{slug}/{qid}.md": storage.render_question_file(q)}
    # 인덱스 상태 배지
    idx_text, _ = github_get_file(f"{slug}/{slug}.md")
    files[f"{slug}/{slug}.md"] = storage.upsert_index_row(
        idx_text or "", slug, q.category, qid, q.title, q.date, storage.status_label(q))
    # README 토글(창 안일 때만). 토글 본문이 손상돼 패치가 실패해도 문제 파일/인덱스
    # 커밋은 유지되도록 국소 try로 감싼다(채점 결과 유실 방지).
    readme, _ = github_get_file("README.md")
    if readme and storage.has_toggle(readme, qid):
        try:
            files["README.md"] = storage.patch_toggle_body(readme, qid, answer, feedback, ai_auto=False)
        except ValueError:
            logger.warning("README 토글 패치 실패(본문 손상 가능), 문제 파일만 갱신: %s", qid)

    try:
        github_commit_files(files, f"update {qid} answer")
    except Exception:
        logger.exception("문제 파일 갱신 실패: %s", qid)


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
        readme, _ = github_get_file("README.md")
        reply(f"현재 기본 생성 개수: {get_config_default(readme or '')}개")
        return

    if command == "config_set":
        if arg is None or arg < 1 or arg > 10:
            reply("기본 생성 개수는 1~10 사이로 입력해주세요. 예: `@봇 config --default=5`")
            return
        readme, _ = github_get_file("README.md")
        new_readme, _ = set_config_default(readme or storage.EMPTY_README, arg)
        github_commit_files({"README.md": new_readme}, f"config default={arg}")
        reply(f"기본 생성 개수가 {arg}개로 설정되었습니다.")
        return

    if command == "question":
        readme, _ = github_get_file("README.md")
        readme = readme or storage.EMPTY_README
        n = arg if arg is not None else get_config_default(readme)
        if n < 1 or n > 10:
            reply("질문 개수는 1~10 사이로 입력해주세요. 예: `@봇 질문 3`")
            return
        today = today_kst_iso()
        files, ids, questions, new_readme = _generate_and_stage(readme, n, today)
        files["README.md"] = new_readme
        github_commit_files(files, "add questions on demand")
        # 질문은 채널 최상위로 전송 (루틴 B의 [Q###] 매핑 보존)
        for qid, (category, title, question) in zip(ids, questions):
            try:
                slack_post_message(channel, f"*[{qid}] {category} | {title}*\n{question}")
            except Exception:
                logger.exception("Slack 질문 전송 실패: %s", qid)
        reply(f"질문 {len(ids)}개를 추가했습니다.")
        return

    reply("모르는 명령입니다. `@봇 help` 를 입력해 사용법을 확인하세요.")
