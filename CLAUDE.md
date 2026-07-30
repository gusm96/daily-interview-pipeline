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
- **README 구조**: 카테고리별 고정 섹션(CS / Java / Spring Boot / Database / 기타(Python·FastAPI·Next.js·우대조건)).

## 환경 변수

`GITHUB_TOKEN`, `REPO_OWNER`, `REPO_NAME`, `GEMINI_API_KEY`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`
(선택: `GEMINI_MODEL`, `SLACK_CHANNEL_ID`, `SLACK_ALLOWED_USER_IDS`)

- `SLACK_ALLOWED_USER_IDS`: 쉼표 구분 사용자 ID 화이트리스트. 설정 시 **모든 멘션 명령**(help 포함)을 해당 사용자만 실행. 미설정이면 제한 없음.

## 설계 명세서

설계·계획 문서는 정책상 커밋하지 않고 로컬(`docs/superpowers/`)에만 보관한다.
초기 설계 명세: `docs/superpowers/specs/2026-06-18-daily-interview-bot-design.md` (로컬 전용).

`docs/` 전체가 `.gitignore` 대상이다 — 설계·계획서뿐 아니라 AI 생성 리포트(`code_review.md`, `project_introduction.md`)와 draw.io 다이어그램 원본도 로컬 보관한다. 특정 문서를 커밋해야 하면 `git add -f <path>`를 쓴다. 프로젝트 문서는 `docs/`가 아니라 이 파일(CLAUDE.md)에 쓴다.

## 검증

코드 변경 후 `python -m py_compile main.py`로 구문 검증.

## 오케스트레이션 워커 (Orca + Antigravity CLI)

계획서를 여러 워커에 나눠 실행할 때는 Orca 오케스트레이션을 쓰고, 구현 워커는 **Antigravity CLI(`agy`)** 로 띄운다.

### 워커 CLI 선택

| CLI | 인증 | 비용 |
| --- | --- | --- |
| `agy` (Antigravity) | Antigravity 계정 | Google AI Pro 구독에 포함 — **추가 과금 없음** |
| `claude` (Claude Code) | Claude 계정 | Claude Pro 구독에 포함 (사용량 한도 소모) |
| `gemini` (Gemini CLI) | ❌ 개인 티어 차단됨 | `GEMINI_API_KEY` 필요 = **종량제 과금** |

- `gemini` CLI는 신버전에서 `IneligibleTierError: UNSUPPORTED_CLIENT`로 개인 계정(무료·AI Pro 모두) 인증이 막혔다. 워커로 쓰지 않는다.
- 구현은 `agy`로 밀어내고 Claude Pro 한도는 설계·조율·리뷰에 남긴다. 구현이 토큰을 가장 많이 먹는 단계다.
- 실행 파일: `%LOCALAPPDATA%\agy\bin\agy.exe`. 모델 목록은 `agy models`로 확인한다(`gemini-3.5-flash-high`, `gemini-3.6-flash-high`, `gemini-3.1-pro-high`, `claude-sonnet-4-6`, `claude-opus-4-6-thinking` 등).
- `agy`는 `--effort low|medium|high`를 지원한다. 모델명 접미사(`-high`)와 `--effort`는 별개다.

### 기동 절차

```bash
AGY="C:/Users/gusm9/AppData/Local/agy/bin/agy.exe"

orca terminal create --worktree active --title impl-t1 \
  --command "$AGY --model gemini-3.5-flash-high --effort high --dangerously-skip-permissions" --json
orca terminal wait --terminal <handle> --for tui-idle --timeout-ms 120000 --json
orca orchestration task-create --spec "<태스크 스펙>" --deps '["<선행 task_id>"]' --json
orca orchestration dispatch --task <task_id> --to <handle> --inject --json
orca orchestration check --wait --types worker_done,escalation,decision_gate --timeout-ms 540000 --json
```

- `dispatch --inject`가 `agy`를 에이전트로 인식하며 `worker_done` 라이프사이클이 정상 동작한다(검증 완료).
- 멀티라인 스펙을 PowerShell에서 네이티브 인자로 넘기면 깨진다. 스펙을 파일에 쓰고 Git Bash에서 `--spec "$(cat spec.txt)"` 로 넘긴다.
- 워커가 에이전트 인식을 잃으면(터미널 제목이 리셋되면) `--inject`가 실패한다. 터미널을 새로 띄워 이어가면 된다.

### 태스크 스펙 작성 규칙

- **자기완결형으로 쓴다.** 계획서 경로를 스펙에 직접 박아 워커가 대화 맥락 없이도 실행할 수 있게 한다. 워커를 새로 띄워도 손실이 없다.
- 모든 구현 스펙에 **git 명령 금지**를 명시한다(계획서의 커밋 Step 건너뛰기, `git rm` 대신 일반 파일 삭제).
- 작업 디렉터리, 새 워크트리 금지, 새 의존성 금지, 계획서 코드 블록 그대로 사용을 함께 못박는다.
- 리뷰 스펙에는 **파일 수정 금지**(읽기·검증만)와 보고 형식(판정 / 실행한 검증 명령 / 심각도별 발견 사항)을 넣는다.

### 코디네이터 원칙

- 매 태스크 후 `git status`·테스트를 **코디네이터가 직접** 돌려 확인한다. 공짜 검증을 리뷰 에이전트에게 시키지 않는다.
- 사소한 수정(한 줄 추가, 공백 정리)은 워커를 띄우지 않고 직접 한다.
- 리뷰 등급을 나눈다. 계획서와의 기계적 대조는 값싼 모델로 충분하고, **코드 삭제·마이그레이션처럼 "정말 안전한가" 판단이 필요한 태스크에만 최상위 모델**을 쓴다.
- 직렬 의존 DAG는 오케스트레이션 이득이 적다. 워커 하나에 계획서를 통째로 주는 편이 싸다. 팬아웃은 **독립 태스크**에 쓴다.

## 작업 규칙

- **Git 작업은 반드시 사용자의 명시적 승인이 있을 때만 수행한다.** `git add`, `git commit`, `git push`, 브랜치 생성/이동 등 모든 git 명령은 사용자가 직접 요청하거나 승인하기 전에는 실행하지 않는다. 작업이 끝나면 변경 사항을 보고하고 커밋 여부를 물어본다.
