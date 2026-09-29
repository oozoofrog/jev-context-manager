# 기존 세션·새 세션 부트스트랩

두 경로 모두 `jcm bootstrap`의 명시적 진입점이다. 저장소 등록은 `enable`로
한 번 수행한다. 이 문서의 명령은 등록한 root 및 선택한 private home에서 실행한다.
`--home`, `--repo`는 하위 명령 앞에 둔다. dev.7부터 Jev는 활성화된 프로젝트에서 별도 허용 절차 없이 사용한다. 이전 전송 금지 표시는 적용하지 않는다. dev.8부터 일일 호출 수·요청 바이트·후보 수의 로컬 제한도 적용하지 않는다.

## 기존 세션

```sh
jcm --repo /absolute/project enable
jcm --repo /absolute/project bootstrap existing
```

현재 Codex 세션의 `CODEX_THREAD_ID`/`CODEX_SESSION_ID`를 사용한다. 두 값이
다르면 거부한다. 셸 외부에서 운영자가 지정할 때는 `--session-id ID`와 필요하면
`--transcript PATH`를 쓴다. 기본 발견은 허용된 transcript root 아래 **해당 ID의
파일명**에 한정하며 다른 대화의 본문을 검색하지 않는다. 경로·metadata session ID·
project cwd·검증된 CLI 버전을 모두 검사한 뒤 공개 항목만 편입한다.

과거 원문 편입 → event/cursor/queue commit → 프로젝트 hook 병합 → 해당 source의
로컬 follower 시작 → 현재 snapshot과 활성화 보고서를 반환한다. 과거 사건의 snapshot은
빈 값으로 유지한다. 편입 시점의 현재 파일을 과거 검증 증거로 소급하지 않는다.
반복 실행은 같은 event·request token·hook·실행 중 follower를 재사용한다.

등록된 source는 새 요청 복원 때 다시 읽는다. 실행 중인 기존 세션의 hook hot reload는
가정하지 않는다. 이를 보완하는 follower는 0.5초 간격으로 **이 source만** 관찰하며,
새로 완성된 공개 기록을 로컬 저장한다. 모델·네트워크 호출은 없다. 30분 무변경 또는
24시간 총 실행 후 종료하며, `disable`, source 삭제 및 접근 실패에도 종료한다.
종료 후에도 다음 요청/새 세션 복원은 등록 source의 남은 tail을 회수한다. 계속되는
기존 세션에서 재활성화하려면 같은 bootstrap을 다시 실행한다. OS 서비스나 전역 daemon을
설치하지 않으며 프로세스·heartbeat·종료 이유는 `status`에서 확인한다.

`--no-install-hooks`, `--no-follow`는 각각 의도적으로 기능을 제한한다. report의
`capture`, `follower.running`, `hook_hot_reload`, `coverage`를 확인한다.
`request_token`은 편입된 이 세션의 가장 최근 사용자 요청에만 연결된다. `read_command`를
실행하면 해당 요청의 정상 Jev 경로와 pack 읽기까지 수행할 수 있다.

Sandbox가 `.codex` 쓰기를 차단하면 `PROJECT_HOOK_INSTALL_PERMISSION_DENIED`를
반환한다. 이미 확정 저장한 과거 기록은 보존하지만 설치 완료로 보고하지 않는다.
프로젝트 설정 쓰기 권한과 hook 실행 trust는 서로 별개다. 시험은 합성 root의 `.codex`
경로만 명시적으로 쓰기 허용하며 일반 Desktop trust를 검증한 것으로 간주하지 않는다.

## 새 세션

1. `SessionStart`: 등록 source의 tail을 회수하고 `awaiting_request`를 기록한다.
   이때 요청을 만들거나 Jev를 호출하지 않는다. 이력이 없으면 `history=empty`다.
2. `UserPromptSubmit`: 현재 요청을 확정 저장하고 opaque token을 발급한다. 고정
   bootstrap 문구는 `bootstrap new --request-token TOKEN`을 실행하도록 지시한다.
3. `bootstrap new`: token의 프로젝트·epoch·유효기간을 검사하고, 기존 dispatch의
   Jev 분류/관련성/관계/표현 판단과 현재 파일 대조를 수행한 뒤 실제 pack 본문을 반환한다.
4. `read_served` 영수증은 반환한 byte를 증명한다. agent가 읽고 정확히 이어서 작업했다는
   판정은 별도 산출물 검증이 필요하다. 제한 초과는 `stage=blocked`이며 읽기 영수증이 없다.

같은 token으로 재실행하면 최신 source와 현재 파일을 다시 대조하여 새 immutable pack을
만든다. 입력이 정확히 같을 때 provider cache는 기존 계약대로 적용되지만 과거 pack을
현재 상태인 것처럼 재사용하지 않는다. 기록이 많거나 API 키 누락/네트워크 오류가 있으면
`degraded`/`blocked`와 coverage gap을 반환한다. 수동 checkpoint로 우회하지 않는다.

## 추가 수용 시험

| ID | 요구 | 구현 / 시험 |
|---|---|---|
| B01 | 현재 기존 세션의 허용된 과거 기록 편입 | `bootstrap.existing`, discovery·idempotency·foreign/tombstone 및 Codex 버전 독립 수집 시험 |
| B02 | 기존 세션의 편입 이후 기록 자동 수집 | `follower`, partial-line·독립 프로세스·disable 시험 |
| B03 | hook/follower/다음 세션의 동시 회수 안전성 | source별 lock + lock 내부 최신 cursor 재조회, stale-reader 시험 |
| B04 | 새 세션 시작과 현재 요청 복원 분리 | `prepare_new` / `bootstrap.new`, 빈 history·무요청·무호출 시험 |
| B05 | 최신 tail + 실제 본문 읽기 + 현재 상태 대조 | new pack·receipt·파일 변경·blocked/revoked token 시험 |
| B06 | 실제 기존 세션과 새 세션의 정상 Jev 경로 | `scripts/live_bootstrap.py`, 합성 실제 thread와 산출물 독립 검사 |

검증 결과는 [부트스트랩 검증 보고서](bootstrap-validation.md)에 별도로 기록한다.
