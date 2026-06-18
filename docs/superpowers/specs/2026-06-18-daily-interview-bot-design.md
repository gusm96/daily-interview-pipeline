# 설계 명세서: Slack 양방향 연동 면접 챌린지 봇

- **작성일**: 2026-06-18
- **개정 1**: 2026-06-18 (codex-reviewer 검토 반영 — Critical 3 + Major 6 + 일부 Minor/Suggestion)
- **개정 2**: 2026-06-18 (Opus 4.8 재검토 반영 — commit 추상화를 mutate-콜백 기반으로 재설계하여 §8.4·§4 정합화, sha 책임 일원화, append ID 재할당 멱등성, AI 태그 정리, validate_env 8종)
- **개정 3**: 2026-06-18 (codex-reviewer 최종검토 반영 — commit 반환을 `(new_content, result)` 튜플로 확장해 append 확정 ID 회수(C-1) 해결, mutate_fn 단항 partial 명시(Major-3), 루틴 A IAM 보호·Scheduler 재시도 0 배포 체크리스트(Major-2/S-2), Slack scope·Gemini rate limit·유사도 임계값·펜스 trimming 범위·첫 실행 골격 생성 명시)
- **대상**: `daily-interview-pipeline` (GCP Cloud Functions, Python 3.11+)
- **산출물**: `main.py`, `requirements.txt`, 초기 `README.md` 골격
- **원본 요구사항**: `prompt.md`

---

## 1. 개요

GCP Cloud Functions 단일 엔트리포인트(`daily_interview_bot(request)`)로 구동되는 양방향 면접 챌린지 봇이다. 두 가지 모드로 분기한다.

- **루틴 A (질문 배달)**: 매일 오전 7시 Cloud Scheduler가 `?action=generate`로 호출. 미답변 자동 처리 → 신규 질문 5개 생성 → GitHub 커밋 + Slack 전송.
- **루틴 B (답변 검증)**: Slack Event Subscriptions Webhook이 스레드 답변 이벤트를 전송할 때 실행. 서명검증 → Gemini 채점 → Slack 피드백 + GitHub 갱신.

## 2. 확정된 핵심 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 스레드↔질문 매핑 | **질문 고유 ID(`Q001`) + 개별 메시지 + 부모 메시지 파싱** | 외부 저장소 없이 stateless, 텍스트 퍼지매칭 대비 견고 |
| ID 유일성 | **전역 누적 번호** (README 스캔 후 max+1) | Cloud Functions 무상태 환경에서 영구 유일성 보장 |
| Slack 3초 타임아웃 | **동기 처리 + `X-Slack-Retry-Num` 헤더로 중복 방지** | 추가 인프라 불필요, 재시도 중복 채점 차단 |
| GitHub 동시 쓰기 | **mutate-콜백 기반 commit + 409 재조회·재적용·재시도** (§8.4) | 무상태 병렬 인스턴스의 sha 경쟁(read-modify-write) 데이터 유실 방지. 변경을 "완성 content"가 아닌 "최신 content에 적용할 함수"로 표현해 멱등 재적용 보장 |
| Gemini 모델/호출 | **`gemini-2.0-flash`, REST 직접 호출** (모델명은 상수/환경변수로 분리) | 비용·속도 우수, 정답 있는 문제엔 충분, 의존성 최소화 |
| README 구조 | **카테고리별 고정 섹션** | prompt.md 문구 부합, 주제별 복습 용이 |

> **prompt.md와의 의도적 차이(S-3)**: 원본 양식은 `- **Q. 질문 내용**`이나, 본 설계는 스레드↔README 매핑을 위해 `- **[Q016] Q. 질문 내용**`처럼 ID를 의도적으로 추가한다. 이는 원본 양식의 확장이며 매핑 설계상 필수다.

## 3. 전체 아키텍처 / 라우팅

엔트리포인트는 라우팅만 담당한다. **서명검증을 분기 판단보다 먼저 수행한다(C-1).**

```
daily_interview_bot(request)
 ├─ (A) ?action=generate (GET/POST)            → run_generate_routine()
 └─ Slack POST (Content-Type application/json)
      ├─ verify_slack_signature()  실패 시 401   [challenge 포함 모든 슬랙 요청에 선적용]
      ├─ X-Slack-Retry-Num 헤더 존재 시 → 즉시 200 반환  [서명검증 후, 타입 분기 전 — Minor-3]
      ├─ type == "url_verification" → challenge 값 반환
      └─ type == "event_callback"   → handle_slack_event()
```

- **C-1 반영**: `url_verification`(challenge) 요청도 서명검증을 통과해야 응답한다. 서명 없는 임의 요청이 엔드포인트 응답·우회 진입점이 되는 것을 차단. (Slack은 challenge 요청에도 `X-Slack-Signature`를 부여한다.)
- **prompt.md와의 의도적 차이**: prompt.md는 challenge를 "코드 최상단에서 즉시 통과"하도록 요구하나, 본 설계는 보안을 위해 **서명검증을 challenge보다 먼저** 수행한다. 이는 원본 요구의 의도적 강화이며, 정상적인 Slack challenge는 서명을 포함하므로 핸드셰이크에 지장이 없다.
- 루틴 A(`?action=generate`)는 Scheduler 전용이며 슬랙 서명 경로와 분리된다. (Scheduler 호출은 GCP IAM/내부 트리거로 보호한다고 가정.)

## 4. 모듈 구조 (`main.py`)

책임 단위로 함수를 분리한다. 각 함수는 단일 책임 + 명확한 입출력을 가진다.

| 함수 | 책임 | 의존 |
|---|---|---|
| `daily_interview_bot(request)` | 엔트리포인트, 라우팅 | 아래 전부 |
| `validate_env()` | 시작 시 **필수 환경변수 8종**(§9) 검증, 누락 시 500 + 에러 로그 (Mi-3) | os |
| `verify_slack_signature(request)` | `SLACK_SIGNING_SECRET` 기반 서명검증 (raw body + `X-Slack-Request-Timestamp` + `X-Slack-Signature`, 5분 윈도우, 상수시간 비교) | hmac, hashlib |
| `run_generate_routine()` | 루틴 A 오케스트레이션 (sha 흐름 §6.A) | GitHub/Gemini/Slack 함수 |
| `find_unanswered_questions(readme)` | "나의 답변" 본문이 비고 **AND** AI 자동 태그가 없는 질문 (id, 질문텍스트) 추출 (C-3) | re |
| `fill_unanswered_questions(answer_map, readme)` | mutate_fn(`partial`로 단항화). 최신 readme에서 미답변(본문 공백 AND AI 태그 없음)인 qid에만 보존된 모범답안(`[⚠️ AI 자동 작성 답변 - 미응시]` 태그 포함)을 주입. **`(new_content, 채운 qid 목록)` 반환**(멱등) | update_answer_block |
| `next_question_ids(readme, count)` | **질문 헤더 라인만** 스캔(`^- \*\*\[Q(\d+)\]`) → max+1부터 count개 ID 반환 (M-2) | re |
| `generate_questions(readme)` | 중복 없는 질문 5개 **텍스트** 생성(temp 0.1, ID 미할당) + 유사도 후처리 검증·재생성 (Mi-2) | call_gemini |
| `handle_slack_event(payload, headers)` | 루틴 B 오케스트레이션 | 아래 함수 |
| `is_bot_or_self(event)` | `bot_id`/`subtype` 또는 `event.user == SLACK_BOT_USER_ID` 시 True (M-6) | - |
| `extract_user_answer(event)` | **`thread_ts` 없는 최상위 메시지**·봇/시스템/자기 메시지를 모두 필터(thread_ts 판정은 이 함수가 담당), 순수 답변 텍스트 추출. 무시 대상이면 None | is_bot_or_self |
| `call_gemini(prompt, temperature)` | Gemini REST 호출(타임아웃 상한) + 응답 추출 + trimming | requests, strip_markdown_fence |
| `strip_markdown_fence(text)` | ` ```markdown ... ``` ` / ` ``` ` 펜스 제거 전처리 | re |
| `github_get_readme()` | README content + sha 로드 (base64 디코딩) | requests |
| `github_commit_with_retry(mutate_fn, message)` | **README 조회→`(new, result)=mutate_fn(content)`→PUT. 409 시 재조회→재적용→재PUT(최대 3회, 지수 backoff)** (C-2). `mutate_fn`은 단항 `(content)->(new_content, result)`. 성공 시 `(new_content, result)` 반환(부산물 전달, C-1). 최종 실패 시 예외 | github_get_readme, requests, time |
| `slack_post_message(channel, text, thread_ts=None)` | 슬랙 메시지/스레드 댓글 전송 | slack-sdk |
| `slack_get_thread_parent(channel, thread_ts)` | `conversations.replies`로 부모 메시지 조회 | slack-sdk |
| `build_question_block(qid, category, question)` | 신규 질문의 접이식 마크다운 블록 생성 | - |
| `update_answer_block(readme, qid, answer, feedback)` | 특정 ID 블록 답변/피드백 갱신(순수 함수, 멱등). `(new_content, None)` 반환. 사용자 실답변 갱신 시 **`[⚠️ AI 자동 작성 답변 - 미응시]` 태그가 있으면 제거**(C-3 잔여). **대상 ID 미존재/치환 실패 시 명시적 예외**(Mi-1) | re |
| `append_questions(questions, readme)` | mutate_fn(`partial`로 단항화). 최신 readme를 **다시 스캔해 ID 재할당**(`next_question_ids`) 후 카테고리 섹션에 append. **`(new_content, 할당된 ID 목록)` 반환** → 409 재적용 시 ID 충돌·중복 방지 + 호출자가 확정 ID 회수(High/C-1) | next_question_ids, build_question_block |
| `parse_question_id(text)` | `\[Q(\d{3,})\]` 대괄호 앵커로 추출 (M-2) | re |

## 5. 질문 ID — 슬랙↔README 공통 키

`Q001` 형식의 ID가 두 시스템을 잇는 단일 진실 공급원(key)이다.

1. **생성**: `next_question_ids()`가 README의 **질문 헤더 라인만**(`^- \*\*\[Q(\d+)\]` 패턴) 스캔하여 코드블록·본문 텍스트의 오염을 배제(M-2). 최대값 +1부터 순차 부여(전역 누적, 영구 유일). (예: 기존 최대 `Q015` → 오늘 `Q016`~`Q020`)
2. **README 저장**: 각 질문 블록 헤더에 `[Q016]` 포함 (양식 §7).
3. **슬랙 전송**: 질문 5개를 **각각 개별 메시지**로 발송하여 질문마다 독립 스레드 형성. 메시지 본문에 `[Q016]` 노출.
4. **답변 매핑**: 스레드 답변 인입 → `event.thread_ts`로 부모 메시지 조회 → `parse_question_id()`가 `[Q###]` 대괄호 앵커로 추출 → `update_answer_block()`로 동일 ID 블록 갱신.

## 6. 루틴 상세

> **sha 책임 일원화(High)**: 모든 커밋은 `github_commit_with_retry(mutate_fn, message)`를 통해서만 수행한다. sha 조회·재조회·재적용은 전적으로 이 함수 내부 책임이며, 호출자는 sha를 직접 다루지 않는다(§6.A-4의 수동 sha₁/sha₂ 체인 폐기). 각 변경은 "최신 content를 받아 새 content를 돌려주는 순수 함수(mutate_fn)"로 표현한다.

### 루틴 A — 질문 배달 (`run_generate_routine`)

0. **첫 실행 골격(S-5)**: README에 고정 카테고리 섹션이 없으면(최초 실행), `append_questions`의 mutate가 누락 섹션 헤더(§7 골격)를 먼저 생성한 뒤 질문을 삽입한다. 즉 초기 골격 생성 주체는 루틴 A의 append mutate이며, 별도 부트스트랩 단계는 두지 않는다.
1. `validate_env()`.
2. **미답변 채움 커밋**: Gemini 모범답안은 비결정적/비용이 있으므로 먼저 `github_get_readme()`로 미답변 질문을 식별하고 각 질문의 모범답안 텍스트를 **선생성하여 맵(`answer_map`)으로 보존**한다. 그 뒤 `github_commit_with_retry(partial(fill_unanswered_questions, answer_map), "fill unanswered")`로 커밋. `fill_unanswered_questions`는 최신 content를 받아 "본문 공백 AND AI 태그 없음"인 질문에만 주입하므로 409 재적용 시에도 멱등(이미 채워진 블록은 건너뜀). 미답변 식별 시점과 mutate 시점 사이 README가 바뀌어도 이 멱등 가드가 커버한다(Minor-1). 미답변이 없으면 커밋 생략.
3. **신규 질문 생성**: `generate_questions()`로 기존 README를 컨텍스트로 "기존과 절대 중복되지 않는" 백엔드 면접 질문 5개 생성. CS(네트워크/OS)·Java·Spring Boot·Database·우대조건(MSA/CI-CD/대용량 트래픽/테스트) 골고루 분배. `temperature=0.1`. 생성 후 기존 질문과 유사도 검증, 미달 시 최대 2회 재생성(Mi-2, 임계값 §8.1). **이 단계에서는 ID를 확정하지 않는다.**
4. **질문 append 커밋 + 확정 ID 회수**: `new_content, assigned_ids = github_commit_with_retry(partial(append_questions, questions), "add questions")`. `append_questions`는 mutate 시점의 **최신 content를 다시 스캔해 `next_question_ids`로 ID를 재할당**하고 카테고리 고정 섹션 아래 날짜와 함께 Append하며(섹션 헤더가 없으면 생성, Mi-4), **할당한 ID 목록을 `result`로 반환**한다. 따라서 호출자는 new_content를 역파싱하지 않고 `assigned_ids`로 확정 ID를 안전하게 회수한다 — 동시 인스턴스가 함께 append해도 최종 성공 시도의 result만 돌아오므로 "내 5개"가 명확하다(C-1/High).
5. **Slack 전송**: 회수한 `assigned_ids`로 `slack_post_message()`를 호출해 질문 5개를 개별 메시지로 발송(ID 포함). Slack 실패는 GitHub 커밋과 독립적으로 로깅(Mi-5).

### 루틴 B — 답변 검증 (`handle_slack_event`)

1. `verify_slack_signature()` 및 `X-Slack-Retry-Num` 차단은 §3 엔트리포인트에서 이미 처리된 상태(중복 채점/커밋 방지). 처리 시간 상한은 §8.5 참조.
2. `extract_user_answer()`: `is_bot_or_self()`(M-6, `event.user == SLACK_BOT_USER_ID` 포함)로 봇 자기 메시지·시스템 메시지·최상위(thread_ts 없는) 메시지를 모두 무시. 순수 사용자 답변만 통과.
3. `slack_get_thread_parent()` → `parse_question_id()`로 `[Q###]` 추출. ID 없으면 무시(200).
4. `call_gemini(temperature=0.4)`로 채점·피드백 생성(S-4: 채점은 생성보다 약간 높은 temperature로 어조 다양성 확보). 방향이 옳으면 가벼운 칭찬 + 부족 키워드, 치명적 오개념이면 정중·명확히 교정 + 모범 답안 방향 제시. Gemini 호출은 타임아웃 상한 적용(§8.5).
5. `slack_post_message(thread_ts=...)`로 스레드에 피드백 전송.
6. `github_commit_with_retry(lambda c: update_answer_block(c, qid, answer, feedback), "update Q### answer")`로 커밋. 보존 입력 `(qid, answer, feedback)`만으로 최신 content에 멱등 재적용되므로 409 재시도가 안전. Slack 전송 성공 후 GitHub 실패 시 별도 에러 로그로 불일치를 알림(Mi-5).

> **2-phase 불일치(Medium, 알려진 한계)**: 5→6 순서상 Slack 피드백은 전송됐으나 GitHub 커밋이 끝내 실패하면 README가 누락된 불일치가 남는다. 단일 사용자·저빈도 시스템에서 허용 가능한 한계로 두되, 실패를 에러 로그로 가시화한다(Mi-5). 트래픽 증가 시 §11 로드맵의 비동기/재처리 큐로 보완.

## 7. 마크다운 양식

각 질문 블록(카테고리 섹션 아래 누적):

```markdown
- **[Q016] Q. 질문 내용** _(2026-06-18)_
  <details>
  <summary>💡 나의 답변 및 AI 피드백 보기/접기</summary>

  ### 🧑‍💻 나의 답변
  (사용자가 슬랙에 적은 답변 내용)

  ### 🤖 AI 피드백
  (Gemini가 검토해준 피드백 및 보완점)
  </details>
```

- **ID 앵커**: `[Q016]`을 질문 헤더 라인에 박아 매핑·갱신·스캔의 단일 앵커로 사용.
- **미답변 판정(C-3)**: "나의 답변" 헤더와 "AI 피드백" 헤더 사이 본문이 **공백(공백문자/빈 줄만)** 이면 미답변. **단, `[⚠️ AI 자동 작성 답변 - 미응시]` 태그가 이미 있으면 처리 완료로 간주하여 루틴 A의 재처리·재덮어쓰기 대상에서 제외한다.**
- **미답변 자동답변 형식(Minor-4)**: `fill_unanswered_questions`가 채울 때는 "나의 답변" 칸 첫 줄에 `[⚠️ AI 자동 작성 답변 - 미응시]`를 두고 다음 줄부터 모범답안 본문을 둔다. 예:
  ```markdown
  ### 🧑‍💻 나의 답변
  [⚠️ AI 자동 작성 답변 - 미응시]
  (Gemini 모범답안 본문)
  ```
  `update_answer_block`/판정 정규식은 이 형식을 기준으로 태그를 탐지·제거한다.
- **AI 태그 정리(C-3 잔여)**: 사용자가 나중에 해당 스레드에 실제 답변을 달아 루틴 B가 `update_answer_block()`로 갱신할 때는, 기존 `[⚠️ AI 자동 작성 답변 - 미응시]` 태그를 **제거**하고 사용자 답변·AI 피드백으로 교체한다. ("미응시" 태그가 사용자 실답변과 공존하는 모순 상태 방지.)
- `update_answer_block()`은 갱신 전 대상 ID 블록 존재를 확인하고, 치환 실패 시 예외를 발생시켜 무음 실패를 방지(Mi-1).

### README 초기 골격

현재 README는 제목+설명만 존재. 다음 고정 카테고리 섹션을 추가한다.

```markdown
## 🖥️ CS (네트워크/OS)
## ☕ Java
## 🌱 Spring Boot
## 🗄️ Database
## ⭐ 우대조건 (MSA / CI·CD / 대용량 트래픽 / 테스트)
```

## 8. 외부 API 스펙 및 안정성 (context7 검증 기준)

### 8.1 Gemini (REST)
- **엔드포인트**: `POST https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent`
- **모델**: `gemini-2.0-flash` (환경변수 `GEMINI_MODEL`로 분리, 품질 부족 시 `gemini-2.5-flash` 등 교체 가능)
- **인증**: 헤더 `x-goog-api-key: $GEMINI_API_KEY`
- **요청 본문**: `{ "contents": [{ "parts": [{ "text": ... }] }], "generationConfig": { "temperature": <루틴별 값> } }`
- **응답 파싱**: `candidates[0].content.parts[0].text` → `strip_markdown_fence()` 적용. **펜스 제거는 응답 전체를 감싼 최외곽 펜스만 제거**하고, 답변 본문 내부의 코드블록(예: ` ```java `)은 보존한다(S-4 과잉 trimming 방지).
- **타임아웃**: `requests` 호출에 명시적 timeout 적용(§8.5).
- **rate limit(Minor-5)**: `gemini-2.0-flash` 무료 티어는 분당 RPM/일일 한도가 있으므로, 루틴 A의 미답변 채움(질문 수만큼) + 질문 생성(최대 3회) 호출이 한도에 닿지 않도록 순차 호출. 한도 초과(429) 시 backoff 재시도.
- **유사도 임계값(S-3)**: 중복 검증은 생성 질문과 기존 질문 간 정규화 토큰 집합의 Jaccard 유사도가 임계(예: 0.6) 이상이면 중복으로 보고 재생성. 임계값은 상수로 분리.

### 8.2 GitHub (REST v3)
- **README 조회**: `GET /repos/{REPO_OWNER}/{REPO_NAME}/contents/README.md` → `content`(base64), `sha`
- **README 커밋**: `PUT /repos/{REPO_OWNER}/{REPO_NAME}/contents/README.md` (base64 content + 직전 `sha` + commit message)
- **인증**: 헤더 `Authorization: Bearer $GITHUB_TOKEN`
- **토큰 권한(M-3)**: `contents: write`만 가진 fine-grained PAT 또는 GitHub App 토큰 권장. 운영 시 GCP Secret Manager 사용 권장.

### 8.3 Slack (slack-sdk)
- **전송**: `chat.postMessage` (channel, text, 스레드는 thread_ts)
- **부모 조회**: `conversations.replies` (channel, ts)
- **인증**: `SLACK_BOT_TOKEN`
- **필요 Bot Token Scopes(Minor-2)**: `chat:write`(전송), `channels:history`(+ 비공개 채널이면 `groups:history`, `conversations.replies` 부모 조회용). 누락 시 루틴 B의 부모 메시지 조회·ID 파싱이 실패하므로 배포 체크리스트(§9.1)에 포함.
- **서명검증**: `v0=` HMAC-SHA256(`SLACK_SIGNING_SECRET`, `v0:{timestamp}:{raw_body}`), `X-Slack-Signature`와 상수시간 비교, timestamp 5분 윈도우.

### 8.4 GitHub 동시 쓰기 — mutate-콜백 낙관적 잠금 (C-2)
모든 README 커밋은 `github_commit_with_retry(mutate_fn, message)`를 통한다.

**mutate_fn 계약**: `mutate_fn(content: str) -> (new_content: str, result: Any)` — **단항 함수**로, 최신 content를 받아 (새 content, 부산물)을 반환한다. 부산물(`result`)은 호출자가 커밋 후 알아야 하는 값(예: append로 확정된 ID 목록)이며, 없으면 `None`. 두 개 이상의 인자가 필요한 변경 함수는 `functools.partial` 또는 lambda로 **단항으로 감싸서** 전달한다(C-1/Major-3).

```
def github_commit_with_retry(mutate_fn, message, max_retries=3):
    for attempt in range(max_retries):
        content, sha = github_get_readme()              # 항상 최신 조회
        new_content, result = mutate_fn(content)        # 최신 content 위에 재계산 + 부산물
        resp = github_put(new_content, sha, message)
        if resp.ok: return new_content, result          # 부산물을 호출자에 전달 (C-1)
        if resp.status == 409: backoff(attempt); continue   # 다른 인스턴스 선점 → 재조회·재적용
        raise GitHubError(resp)
    raise GitHubError("409 retries exhausted")
```

**핵심 1 — 멱등 재적용**: 변경을 "완성된 content 문자열"이 아니라 **"최신 content를 입력받아 새 content를 돌려주는 함수"** 로 표현하므로, 409가 나도 항상 **최신** content 위에 변경을 다시 적용한다.

**핵심 2 — 부산물 전달(C-1)**: 반환 타입이 `(new_content, result)` 튜플이므로, append처럼 "mutate 시점에 결정된 ID"를 호출자가 new_content를 역파싱하지 않고 **`result`로 직접 회수**한다. 재시도가 일어나 ID가 재할당돼도, 최종 성공한 시도의 `result`만 반환되므로 동시 인스턴스 환경에서도 "내가 이번에 추가한 ID"가 명확하다.

| 변경 | mutate_fn (partial로 단항화) | 보존 입력 | 반환 result | 멱등성 |
|---|---|---|---|---|
| 답변 갱신(루틴 B) | `lambda c: update_answer_block(c, qid, answer, feedback)` | qid, answer, feedback | None | 블록 치환이라 멱등 ✅ |
| 미답변 채움(루틴 A) | `partial(fill_unanswered_questions, answer_map)` | 선생성 모범답안 맵 | 채운 qid 목록 | "공백 AND 무태그"만 채워 멱등 ✅ |
| 질문 append(루틴 A) | `partial(append_questions, questions)` | 질문 5개 텍스트(ID 미확정) | **할당된 ID 목록** | mutate 내부 **ID 재할당** → 충돌·중복 없음 ✅ |

이로써 병렬 인스턴스 간 read-modify-write 유실(C-2), 재적용 시 ID 충돌·중복 append(High), append 후 확정 ID 회수(C-1)를 모두 해결한다.

### 8.5 처리 시간 / 타임아웃 (M-1)
- Gemini 호출 timeout 상한: 8초. GitHub PUT 재시도 포함 처리 상한을 함수 타임아웃 내로 설계(권장 Cloud Functions timeout ≥ 60초).
- 동기 처리상 첫 요청이 3초를 초과하면 Slack 재시도가 올 수 있으나 `X-Slack-Retry-Num` 차단으로 중복 처리를 막는다. 첫 요청 자체가 함수 타임아웃으로 실패하면 해당 답변은 누락될 수 있음을 허용 범위로 정의(향후 트래픽 증가 시 Pub/Sub 비동기화 — §11 로드맵).

## 9. 환경 변수 (`os.environ.get()` 격리)

**필수(8종)** — `validate_env()`가 시작 시 전부 검사, 누락 시 즉시 에러 로그 + 500 반환(Mi-3):

| 변수 | 용도 |
|---|---|
| `GITHUB_TOKEN` | GitHub API 인증 (`contents:write` 최소권한 권장, M-3) |
| `REPO_OWNER` | 대상 저장소 소유자 |
| `REPO_NAME` | 대상 저장소 이름 |
| `GEMINI_API_KEY` | Gemini API 인증 |
| `SLACK_BOT_TOKEN` | Slack 전송/조회 |
| `SLACK_SIGNING_SECRET` | Slack 서명검증 |
| `SLACK_CHANNEL_ID` | 루틴 A 질문 전송 대상 (없으면 전송 실패하므로 필수) |
| `SLACK_BOT_USER_ID` | M-6 봇 자기 메시지 필터 (없으면 무한루프 위험하므로 필수) |

**선택**: `GEMINI_MODEL`(기본 `gemini-2.0-flash`)

> prompt.md는 6종을 명시하나, 안정 운영에 `SLACK_CHANNEL_ID`·`SLACK_BOT_USER_ID`가 사실상 필수이므로 필수 목록에 포함한다(의도적 확장).

### 9.1 배포 체크리스트 (보안/동시성 — Major-2, S-2)

- **루틴 A 엔드포인트 보호(Major-2)**: Cloud Functions HTTP 트리거는 기본적으로 공개될 수 있으므로, `allUsers` invoker를 제거하고 **Cloud Scheduler 서비스 계정에만 `roles/cloudfunctions.invoker`** 를 부여한다. Scheduler Job은 OIDC 토큰으로 호출하도록 구성한다. (`?action=generate`가 인증 없이 외부 노출되는 것을 차단.)
- **Scheduler 재시도 비활성화(S-2)**: Scheduler Job `retryConfig.retryCount = 0` 으로 설정하여 루틴 A 동시 인스턴스 시나리오 자체를 제거한다(§6.A의 동시성은 §8.4로 방어되나, 재시도 0이 1차 방어선).
- **Slack Bot Token Scopes**: `chat:write`, `channels:history`(필요 시 `groups:history`).
- **함수 타임아웃**: ≥ 60초 (§8.5).
- **시크릿**: 가능하면 GCP Secret Manager로 토큰/키 주입(M-3/M-5).

## 10. 로깅 · 예외 처리 · 보안

- 각 분기 및 API 호출(GitHub/Gemini/Slack) 단계마다 `logging`으로 성공/실패와 에러 로그 출력 (GCP Cloud Logging 연동).
- **민감정보 마스킹(M-5)**: 로깅 시 `x-goog-api-key`, `Authorization`, 슬랙 토큰 등 헤더/토큰을 출력하지 않는다. `requests`/SDK의 DEBUG 로깅이 헤더를 흘리지 않도록 로그 레벨을 통제. (운영 시 Secret Manager 권장.)
- Gemini 응답의 ` ```markdown ... ``` ` / ` ``` ` 펜스를 정규식으로 완전히 제거하는 전처리(`strip_markdown_fence`) 필수.
- 루틴 B는 슬랙 재시도 폭주를 막기 위해 처리 불가/무시 케이스에도 가급적 200 + 로깅으로 응답. 서명검증 실패만 401.
- Slack 전송과 GitHub 커밋은 순차 실행이며 부분 실패(한쪽만 성공) 시 각각 독립 로깅하여 불일치를 가시화(Mi-5).

## 11. requirements.txt 및 로드맵

```
functions-framework==3.*
requests==2.*
slack-sdk==3.*
```
(Gemini는 REST 직접 호출이므로 별도 SDK 불필요. 표준 라이브러리 hmac/hashlib/base64/re/json/logging/time 사용. 버전은 구현 시 최신 안정 패치로 고정(pin)하여 재현성 확보.)

**규모 한계 / 로드맵(S-1)**: 본 설계는 README를 사실상 DB로 사용한다. 질문 ID가 수백 개를 넘거나 일간 활성 답변이 많아지면 (a) README 다운로드/정규식 전체 스캔 비용, (b) GitHub API rate limit(시간당 5,000), (c) 동시 쓰기 경쟁이 증가한다. **임계 기준(예: 누적 Q100 이상 또는 일간 답변 10건 이상)** 도달 시 Firestore 등 atomic 저장소 + Pub/Sub 비동기 처리로 전환을 검토한다.

## 12. 검증

작성 완료 후 `python -m py_compile main.py`로 구문 에러 검증, 결과 요약 보고.

## 13. 범위 밖 (YAGNI)

- 사용자별 점수 집계/리더보드, 다중 채널/다중 사용자 라우팅, 웹 대시보드.
- DB/Firestore 상태 저장 및 Pub/Sub 비동기화는 **현 단계 범위 밖이나 §11 로드맵의 전환 트리거를 충족하면 도입**한다(매핑 자체는 ID 파싱으로 해결).
