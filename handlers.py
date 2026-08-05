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
from config import today_kst_iso, validate_env, shift_date_iso, parse_iso_date, days_left
from state import (
    parse_state, render_state, in_range, range_text,
    is_paused, get_paused_until, set_paused_until,
)
from commands import parse_mention_command, build_help_text, is_authorized_user
from prompts import MODEL_ANSWER_PROMPT, FEEDBACK_PROMPT

logger = logging.getLogger("daily_interview_bot")


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
    """루틴 A: 정지 판정 → 미답변 자동 모범답안 + 신규 질문 생성 → 1커밋 → Slack.

    정지 중이면 state.json만 읽고 즉시 반환한다. README(285KB)도 받지 않는다.
    """
    missing = validate_env()
    if missing:
        logger.error("필수 환경변수 누락: %s", missing)
        raise RuntimeError(f"환경변수 누락: {missing}")

    today = today_kst_iso()

    # 0) 정지 판정 — 가장 먼저. 정지 중이면 README 조회조차 하지 않는다.
    #    이 블록이 README 조회보다 아래로 내려가면 정지의 의미가 사라진다.
    state_text, _ = github_get_file("state.json")
    state_warnings = []
    cfg = parse_state(state_text, state_warnings)
    if is_paused(cfg, today):
        logger.info("질문 생성 정지 중(paused_until=%s), 루틴 A를 건너뜁니다",
                    get_paused_until(cfg))
        return

    if state_warnings:
        _notify_state_warnings(state_warnings)

    readme, _ = github_get_file("README.md")
    if readme is None:
        readme = storage.EMPTY_README
    files = {}

    # 1) 창 안 미답변 → 모범답안 생성 후 README 패치 + 문제 파일 + 인덱스
    for qid, slug, date, title, question in storage.scan_window_unanswered(readme)[:cfg["max_fill_per_run"]]:
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

    # 2) 만료된 정지 필드 정리. 여기까지 왔다는 건 정지가 아니라는 뜻이므로
    #    paused_until이 남아 있다면 만료됐거나 훼손된 값이다. 추가 커밋 없이 5)에 실어 보낸다.
    #    두 조건이 필요한 이유: 만료된 정상 값("2026-08-01")은 앞 조건이 잡고,
    #    훼손된 값("invalid")은 get_paused_until이 None을 돌려주므로 뒤 조건이 잡는다.
    if get_paused_until(cfg) is not None or "paused_until" in cfg:
        cfg = set_paused_until(cfg, None)
        files["state.json"] = render_state(cfg)

    # 3) 신규 질문 생성 (parse_state가 이미 범위로 클램프해 둔 값)
    count = cfg["daily_count"]
    files, ids, questions, readme = _generate_and_stage(readme, count, today, files)

    # 4) 카테고리별 상위 N개 초과분 prune
    readme = storage.prune_overflow(readme, cfg["readme_top_n"])
    files["README.md"] = readme

    # 5) 1커밋
    github_commit_files(files, "add daily questions")

    # 6) Slack 전송
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    for qid, (category, title, question) in zip(ids, questions):
        try:
            slack_post_message(channel, f"*[{qid}] {category} | {title}*\n{question}")
        except Exception:
            logger.exception("Slack 질문 전송 실패: %s", qid)


def _notify_state_warnings(warnings):
    """state.json을 보정한 사실을 Slack에 알린다.

    로그만 남기면 아무도 읽지 않아 잘못된 설정이 오래 방치된다. 전송이 실패해도
    질문 배달이 막히면 안 되므로 예외는 삼킨다(기존 Slack 전송과 같은 정책)."""
    channel = os.environ.get("SLACK_CHANNEL_ID", "")
    if not channel:
        return
    lines = "\n".join(f"• {w}" for w in warnings)
    try:
        slack_post_message(
            channel,
            f"⚠️ state.json 설정을 보정했습니다\n{lines}\n질문은 정상 생성했습니다.",
        )
    except Exception:
        logger.exception("state.json 보정 알림 전송 실패")


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

    if command == "stop_invalid":
        reply("정지 기간을 알아듣지 못했습니다. "
              "`@봇 stop` / `@봇 stop 3` / `@봇 stop 2026-08-10` 형식으로 입력해주세요.")
        return

    if command == "stop":
        today = today_kst_iso()
        # 인자 검증을 state.json 조회보다 먼저 끝낸다 — 잘못된 입력이 GitHub API를 건드리지 않는다.
        if arg is None:
            until = "forever"
        elif isinstance(arg, int):
            if not in_range("pause_days", arg):
                reply(f"정지 기간은 {range_text('pause_days')}일 사이로 입력해주세요. "
                      "예: `@봇 stop 3`")
                return
            until = shift_date_iso(today, arg - 1)   # 오늘 포함 N일 → 마지막 정지일은 오늘+(N-1)
        else:
            if parse_iso_date(arg) is None:
                reply("날짜를 YYYY-MM-DD 형식으로 입력해주세요. 예: `@봇 stop 2026-08-10`")
                return
            # 자릿수가 고정된 ISO 날짜는 사전순 비교가 날짜순 비교와 일치한다.
            if arg < today:
                reply(f"오늘 이후 날짜를 입력해주세요. 오늘은 {today}입니다.")
                return
            until = arg

        state_text, _ = github_get_file("state.json")
        cfg = set_paused_until(parse_state(state_text), until)
        github_commit_files(
            {"state.json": render_state(cfg)},
            "pause indefinitely" if until == "forever" else f"pause until {until}")
        if until == "forever":
            reply("질문 생성을 정지했습니다. `@봇 start`로 재개하세요.")
        else:
            reply(f"{until}까지 질문 생성을 정지합니다. "
                  f"{shift_date_iso(until, 1)} 아침부터 재개됩니다.")
        return

    if command == "start":
        state_text, _ = github_get_file("state.json")
        cfg = parse_state(state_text)
        if "paused_until" not in cfg:
            reply("이미 동작 중입니다.")
            return
        cfg = set_paused_until(cfg, None)
        github_commit_files({"state.json": render_state(cfg)}, "resume question generation")
        reply("질문 생성을 재개했습니다. 다음 아침 7시부터 질문이 도착합니다.")
        return

    if command == "config_show":
        state_text, _ = github_get_file("state.json")
        cfg = parse_state(state_text)
        lines = [f"현재 기본 생성 개수: {cfg['daily_count']}개"]
        paused_until = get_paused_until(cfg)
        if paused_until == "forever":
            lines.append("정지 중 — 무기한 (`@봇 start`로 재개)")
        elif paused_until is not None:
            # 만료된 값이 아직 정리되지 않은 창에서 D-0이나 음수가 찍히는 것을 막는다.
            # 만료 정리는 다음 아침 루틴 A가 하므로 그 사이 창이 존재한다.
            today = today_kst_iso()
            if is_paused(cfg, today):
                lines.append(
                    f"정지 중 — {shift_date_iso(paused_until, 1)} 아침 재개 예정 "
                    f"(D-{days_left(paused_until, today)})")
        reply("\n".join(lines))
        return

    if command == "config_set":
        if arg is None or not in_range("daily_count", arg):
            reply(f"기본 생성 개수는 {range_text('daily_count')} 사이로 입력해주세요. 예: `@봇 config --default=5`")
            return
        state_text, _ = github_get_file("state.json")
        cfg = parse_state(state_text)
        cfg["daily_count"] = arg
        github_commit_files({"state.json": render_state(cfg)}, f"config default={arg}")
        reply(f"기본 생성 개수가 {arg}개로 설정되었습니다.")
        return

    if command == "question":
        readme, _ = github_get_file("README.md")
        readme = readme or storage.EMPTY_README
        if arg is not None:
            n = arg
        else:
            state_text, _ = github_get_file("state.json")
            n = parse_state(state_text)["daily_count"]
        if not in_range("daily_count", n):
            reply(f"질문 개수는 {range_text('daily_count')} 사이로 입력해주세요. 예: `@봇 질문 3`")
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
