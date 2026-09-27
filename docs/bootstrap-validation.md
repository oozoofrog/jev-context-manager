# 기존 세션·새 세션 부트스트랩 검증 — 2026-09-27

**두 부트스트랩을 구현하고, hook 없이 시작한 실제 기존 Astra CLI 세션과 별도 새
Astra CLI 세션을 연결한 종단간 시험을 통과했다.** 수동 checkpoint·인계·fork·resume는
사용하지 않았다. 기능 범위는 계속 `limited`이며 Desktop 전체 출시 완료는 아니다.

## 구현과 저장 정책

- `bootstrap existing`: 현재 session ID 자동 확인 또는 명시 ID, 정확한 transcript
  발견·scope 검사, 공개 과거 기록의 중복 없는 편입, hook 병합, 로컬 follower 시작.
- `SessionStart`: 새 세션 복원 준비. 요청 전에는 task 추정과 Jev 호출을 하지 않는다.
- `bootstrap new --request-token`: 현재 요청의 정상 dispatch를 수행하고 **실제 pack
  본문**을 반환하며 `read_served`를 기록한다. blocked이면 읽기 성공으로 표시하지 않는다.
- follower는 hook hot reload에 의존하지 않는다. source별 lock으로 hook/복원과 cursor
  갱신을 직렬화하며, disable/삭제/idle/lifetime 한계에 따라 종료한다. Jev 전송은 없다.
- `status`는 두 bootstrap 상태와 follower heartbeat·종료 이유를 보여 준다.
- 얇은 routing skill은 `skills/astra-continuity/SKILL.md`에 있으며 전역 설치하지 않았다.

저장 정책은 **설계 6절 그대로**다. 사용자 전용 외부 `JCM_HOME`에 원문·원장·pack을
보관하고, 프로젝트에는 `.codex/jcm.json`의 identity와 approved config 참조를 둔다.
`.jcm/` 도입이나 데이터 이동은 적용하지 않았다. 외부 전송 기본값은 계속 deny다.

## 검증 구분

| 수준 | 결과 | 증거 |
|---|---|---|
| 소스 회귀·mock | 44/44 PASS | 기존 34개 + bootstrap 10개, `evidence/bootstrap-tests.log` |
| skill 문서 검증 | PASS | `evidence/bootstrap-skill-validation.log` |
| dev2 wheel 별도 설치·회귀 | 44/44 PASS | `evidence/bootstrap-package-validation.json`, `bootstrap-package-tests.log`, `bootstrap-wheel-install.log` |
| 실제 기존 세션 편입 | PASS | 자기 session ID로 편입, 과거 public item 2개; 기존 세션 hook lifecycle 수신 0건 |
| 편입 이후 자동 기록 | PASS | 편입 후 별도 도구가 생성한 무작위 표식이 follower만으로 확정 저장됨 |
| 실제 독립 새 세션 | PASS | 기존 요구와 후속 표식이 새 pack에 들어가고 실제 결과에 반영됨 |
| 실제 Jev 정상 경로 | PASS | 최종 시험 `jev-1.13.0` HTTP 3회 성공, 새 pack `quality=normal` |
| 일반 Desktop 지속 활성화 | NOT RUN | scoped fixture sandbox와 일회성 hook trust bypass를 Desktop 승인으로 해석하지 않음 |

최종 wheel은 별도 가상환경의 `site-packages/jcm`에서 import되었음을 확인하고,
`-I` 격리 모드에서 전체 시험을 실행했다. 소스 checkout의 editable import를 wheel
시험으로 오인하지 않았다. 최종 package에는 registry 연결의 명시적 close도 포함한다.
기존 연결 context manager는 transaction만 종료해서 follower 반복 조회 때 연결이 GC까지
남을 수 있었고, `contextlib.closing`으로 정리했다. 모델 경로를 바꾼 수정은 아니며,
실제 세션 시험 전체를 이 정리 수정 뒤 다시 반복한 것으로 주장하지 않는다.

## 최종 실제 세션 시험

증거: `evidence/live-bootstrap-ae93a080/result.json`.

- 기존 세션: `01a0e329-55c2-7e81-9804-b7370b95c1c0`
- 독립 새 세션: `01a0e329-bded-71c2-8538-0bfb6ce939b4`
- 두 세션 모두 실제 `gpt-6-astra`, reasoning `xhigh`, 정상 종료.

기존 세션을 시작할 때 프로젝트에 hook 파일이 없었다. 일반 사용자 요청에
`protocol_version=1`과 기존 `paused` 상태 유지 요구를 넣었다. 이 세션의 도구가
`bootstrap existing`을 실행하여 환경의 실제 session ID로 자신을 편입했다.
그 후 별도 일반 도구 호출이 무작위 `CONTINUITY_MARKER`를 출력했다. 표식을 파일에
저장하거나 수동 record/checkpoint로 넣지 않았다. 원래 코드는 변경하지 않았다.

시험기는 별도의 recovery 호출 없이 로컬 원장에 표식 도구 출력이 도착하는 것을
기다렸다. 표식 event의 의미 처리 상태는 새 세션 시작 전 `queued`였다. 기존 세션의
hook lifecycle 관측은 0건이었다. 이로써 설치 뒤 hook의 즉시 재적용에 의존하지 않는
기존 세션의 자동 기록 경로를 확인했다.

새 세션의 요청에는 앞선 요구·표식·대화 복사·인계문을 넣지 않았다. 정상 hook이
발급한 token으로 `bootstrap new`를 실행했다. pack에는 이전 사용자 요구와 편입 이후
도구 출력이 포함됐으며 실제 Jev 판단과 읽기 영수증이 있었다. 독립 검사기는 다음을
확인했다.

- `reconnect(True)`와 `result.json`이 `paused=true`를 유지.
- `reconnect(False)`는 `paused=false`를 유지.
- 함수 반환과 결과 파일에서 `protocol_version=1` 유지.
- `result.json`의 `continuity_marker`가 과거 도구 출력과 정확히 일치.
- 새 session ID가 기존 ID와 다르고, 실제 bootstrap 명령·pack·read receipt 존재.
- `quality=normal`, 실제 HTTP 판단 lane, 후속 tail의 포함.
- 시험 종료 후 fixture 비활성화와 follower 종료, 임시 trust 제거 및 다른 사용자 설정 보존.

최종 실행의 실제 Jev 호출은 **3회**, 반환 usage 합계는 **입력 5,428 / 출력 731 tokens**다.
이 수치는 해당 Jev 호출만 집계하며 Astra나 이전 실패 실행 비용을 포함하지 않는다.

## 실패와 시험기 수정도 보존

1. `live-bootstrap-f47916dc`: 기존 기록 편입은 이루어졌으나 sandbox가 보호된
   `.codex` 쓰기를 거부하여 hook 설치에서 실패했다. 전체 PASS가 아니다.
   재시험은 합성 프로젝트의 `.codex` 경로만 `--add-dir`로 허용했다. 전역 sandbox는
   변경하지 않았다. 현재 CLI는 이 경우 `PROJECT_HOOK_INSTALL_PERMISSION_DENIED`를 낸다.
2. `live-bootstrap-175760f5`: 실제 기록·새 세션·Jev와 결과 파일의 표식 회수는 성공했다.
   시험기는 요구하지 않았던 함수 반환값에도 표식을 기대하여 `later_marker_exact=false`였다.
   당시 FAIL 원본은 보존했다. 최종 결과 파일을 검사하도록 oracle을 수정하고, 새 표식과
   독립 thread 두 개로 전체 시험을 다시 수행한 결과가 `ae93a080`의 PASS다.
3. 첫 package 격리 시험은 repository script를 이름으로 import하던 기존 시험 하나가
   `-I`에서 실패했다. 해당 script의 실제 파일 위치로 import하도록 시험을 고쳤다.
   실패 로그는 `bootstrap-package-tests-isolation-failure.log`에 남겼다.
4. 최초 no-build-isolation wheel 시도는 개발 환경에 setuptools backend가 없어 실패했다.
   정상 build isolation으로 wheel을 만들었다. 전역 Python 패키지는 변경하지 않았다.

## 남은 경계

실제 CLI 전환 두 경로는 검증했지만 일반 Desktop hook trust/reload, 실제 자동 compact,
장기·다중 task recall, backup restore와 tombstone 전파, 전체 hostile-source 행동 평가
등 기존 v1.0 출시 gate는 남아 있다. follower는 30분 idle/24시간 lifetime으로 제한되며,
종료 후 미수집 tail은 다음 정상 요청/새 세션이 회수한다. 원래 host transcript가 삭제되면
아직 편입되지 않은 기록을 복구했다고 주장할 수 없다. 전체 coverage는 `partial`이다.
