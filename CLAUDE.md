# CLAUDE.md

이 파일은 이 저장소에서 작업하는 Claude Code에 대한 가이드입니다.

## 프로젝트 개요

`daily-interview-pipeline` — GCP Cloud Functions(Python 3.11+)에서 구동되는 Slack 양방향 연동 백엔드 기술 면접 챌린지 봇.

- **루틴 A (질문 배달)**: 매일 오전 7시 Cloud Scheduler가 `?action=generate`로 호출 → 미답변 자동 처리 → Gemini로 중복 없는 면접 질문 5개 생성 → GitHub README 커밋 + Slack 전송.
- **루틴 B (답변 검증)**: Slack 스레드 답변 Webhook → 서명검증 → Gemini 채점/피드백 → Slack 스레드 댓글 + GitHub README 갱신.

단일 엔트리포인트 `daily_interview_bot(request)`에서 요청 종류로 분기한다.

## 핵심 아키텍처 결정

- **스레드↔질문 매핑**: 질문 고유 ID(`Q001`) 기반. README와 Slack 메시지에 동일 ID를 박아 공통 키로 사용. ID는 README를 스캔해 전역 누적 부여(stateless).
- **Slack 3초 타임아웃**: 동기 처리 + `X-Slack-Retry-Num` 헤더로 재시도 중복 방지.
- **Gemini**: `gemini-2.0-flash` REST 직접 호출(`requests`), 모델명은 상수/환경변수로 분리.
- **README 구조**: 카테고리별 고정 섹션(CS / Java / Spring Boot / Database / 우대조건).

## 환경 변수

`GITHUB_TOKEN`, `REPO_OWNER`, `REPO_NAME`, `GEMINI_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`
(선택: `GEMINI_MODEL`, `SLACK_CHANNEL_ID`, `SLACK_ALLOWED_USER_IDS`)

- `SLACK_ALLOWED_USER_IDS`: 쉼표 구분 사용자 ID 화이트리스트. 설정 시 **모든 멘션 명령**(help 포함)을 해당 사용자만 실행. 미설정이면 제한 없음.

## 설계 명세서

`docs/superpowers/specs/2026-06-18-daily-interview-bot-design.md`

## 검증

코드 변경 후 `python -m py_compile main.py`로 구문 검증.

## 작업 규칙

- **Git 작업은 반드시 사용자의 명시적 승인이 있을 때만 수행한다.** `git add`, `git commit`, `git push`, 브랜치 생성/이동 등 모든 git 명령은 사용자가 직접 요청하거나 승인하기 전에는 실행하지 않는다. 작업이 끝나면 변경 사항을 보고하고 커밋 여부를 물어본다.
