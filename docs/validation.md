# 구현·검증 보고서 — 2026-09-27

**첫 자동 continuity 종단간 경로를 실제 Astra CLI 세션과 실제 Jev로 검증했다.**
수동 checkpoint나 인계는 사용하지 않았다. 이는 P1과 한정된 P2 경로의 통과이며,
설계 v1.0 전체의 출시 완료를 뜻하지 않는다.

> 후속 구현: 기존 세션과 새 세션의 명시적 부트스트랩·44개 회귀·실제 세션 검증은
> [부트스트랩 검증 보고서](bootstrap-validation.md)에 기록했다. 아래 수치는 최초 slice의 증거다.

## 요청과 구현의 연결

사용자가 제공한 설계는 `docs/JCM_Astra_Continuity_Design_v1.0.md`에 원문 그대로
보존했다. 문서의 합성 예시·명령은 실행 권한으로 취급하지 않았다.
[수용 시험 연결표](acceptance-map.md)는 R01–R12, T01–T25를 모두 포함한다.

| 모듈 | 구현한 책임 |
|---|---|
| `src/jcm/config.py` | 선택한 root의 무작위 identity, private profile, 전송 기본 차단, 프로젝트 hook 병합과 백업 |
| `src/jcm/adapter.py` | 실제 Codex hook, 고정 bootstrap와 opaque token, 관측한 버전에 한정된 공개 transcript tail 회수 |
| `src/jcm/store.py` | 원문 blob 원자 저장, SQLite WAL/FULL, event/cursor/queue commit, source refs, lease, epoch, tombstone |
| `src/jcm/provider.py` | 실제 TypeSafe HTTP, 고정 Jev 모델, typed 응답 검증, 호출 예산·retry·cache·감사 기록 |
| `src/jcm/worker.py` | 분류 작업 lease·revision 검증, 출처 확인 수준 유지, 가설·요구·검증 주장 분리 |
| `src/jcm/coordinator.py` | 현재 요청별 후보, 보호된 사용자 원문·미처리 tail, Jev 관련성·관계·표현, immutable pack과 읽기 영수증 |
| `src/jcm/snapshot.py` | 현재 Git·branch·dirty·파일 hash 대조, 비 Git 작업공간 지원 |
| `src/jcm/cli.py` | JSON CLI, 진단·조회·정책·삭제 진입점 |

## 서로 다른 증거 수준

| 검증 수준 | 결과 | 증거와 한계 |
|---|---|---|
| 개발 설치 | PASS | `.venv` editable 설치 및 CLI 실행. 전역 설치·production 활성화로 해석하지 않음 |
| 배포 패키지 | PASS | wheel을 별도 가상환경에 설치하고 dependency check·CLI·34개 시험 실행. `evidence/package-validation.json`, `evidence/wheel-install.log` |
| 단위·replay·mock | 34/34 PASS | `evidence/unit-tests.log`. 실제 모델 판단의 정확도나 Desktop 동작을 대체하지 않음 |
| 실제 Jev 단독 경로 | PASS | 합성 이력, 실제 인증 호출 3건, `jev-1.13.0`, 입력 2,646 / 출력 518 tokens. 새 Codex 세션 시험과 별도 |
| 실제 새 Astra 세션 + Jev | PASS | 독립된 실제 CLI thread 3개, 최신 교정 직후 종료, 새 C의 pack read와 실제 코드·결과 검사. 정상 Jev 호출 6건 |
| 일반 Desktop 활성화 | NOT RUN | 일반 프로젝트/정의 trust, 현재 앱 세션 reload, Desktop lifecycle을 시험용 CLI bypass로 대체하지 않음 |
| v1.0 전체 출시 gate | NOT COMPLETE | 아래 잔여 항목을 통과해야 함 |

## 실제 새 세션 시험

성공 실행: `evidence/live-e2e-b917b743/result.json`.

1. **A — `01a0e309-380f-7141-8278-4e41afe06c56`**
   `connection.py` 작업의 결과 객체에 `protocol_version: 1`을 유지한다는
   요구를 전달했다. 구현은 아직 시작하지 않았다. 요청은 정상 hook에서 자동 저장됐다.
2. **B — `01a0e309-fd1a-7562-8172-22b5fbec9b2c`**
   재접속 시 기존 `paused` 값을 유지하라는 교정과 시험마다 새로 만드는
   무작위 `continuity_marker`를 전달했다. hook의 durable commit을 확인한 즉시
   해당 CLI 프로세스를 종료했다. B의 교정은 `queued`였고, Stop/완료 응답이 없었다.
3. **C — `01a0e30a-09b6-74c0-81cb-f726efc818cd`**
   새 thread에서 연결 복구 구현과 `result.json` 작성만 요청했다. A/B 대화,
   marker, protocol 값, checkpoint, 인계서는 새 요청에 넣지 않았다.
   `resume`이나 fork도 사용하지 않았다.

C는 hook이 준 opaque token으로 `dispatch`를 호출하고 `read`로 pack 본문을
읽었다. C의 pack에는 A의 이전 요구, B의 최신 교정, 미처리 tail이 들어 있었다.
실제 Jev 분류·관련성·관계·표현 판단이 참여했고 `quality=normal`이었다.
coverage는 관측 범위의 한계 때문에 계속 `partial`로 표시했다.

C는 현재 파일을 읽고 구현을 바꿨다. 별도의 검사기가 다음을 확인했다.

- `reconnect(True)`와 작성된 JSON에서 `connected=true`, `paused=true`.
- `reconnect(False)`는 `paused=false`를 유지.
- 이전 A의 `protocol_version=1`이 유지됨.
- 새 C 요청이나 원래 파일에 없던 B의 무작위 marker가 정확히 복원됨.
- C의 `read_served` 영수증과 실제 도구 호출이 존재.
- 서로 다른 thread ID 3개, B 종료 전 의미 처리 `queued`, Stop에 의존하지 않음.
- 정상 Jev 호출 감사 기록, 임시 trust 정리 후 기존 사용자 설정 보존.

이 실행의 Jev는 **6회 성공**, 반환 usage 합계는 **입력 9,272 / 출력 1,433 tokens**다.
이 수치는 Jev 호출만 집계하며 Astra 비용·실패한 이전 실행을 포함한 총비용이 아니다.
달러 비용은 계산하지 않았다. 두 read 영수증 중 C의 해당 pack 소비와 결과를 별도로
대조했다. 단순한 pack 생성이나 영수증만으로 성공 처리하지 않았다.

최종 패키지에는 이후 발견한 누락 cwd 거부와 비객체 tool_input 처리도 포함했다.
이 입력 검증은 최종 34개 회귀 시험에서 검증했다. 위 실제 세션 시험의 정상 payload
형태와 실행 경로는 변경하지 않았으며, 해당 전체 모델 시험을 반복한 것으로 주장하지 않는다.

## 실패했던 첫 실행도 보존

첫 실행 `evidence/live-e2e-12fee5f9/result.json`의 overall 결과는 **FAIL**이다.
새 세션은 과거 요구와 최신 교정을 읽고 올바른 코드·결과를 만들었지만, 새 세션의
Jev 호출은 네트워크 오류로 `degraded`였다. 이 결과를 정상 Jev 경로 PASS에 합산하지 않았다.

또한 Codex CLI가 새로 만든 시험용 프로젝트의 trust 항목을 사용자 설정에 남겨
바이트 hash 검사가 실패했다. 이후 설정을 백업하고 해당 임시 항목만 제거했다.
다른 설정을 보존했는지 비교한 기록은 `evidence/host-config-cleanup.json`에 있다.

성공한 재시험은 해당 세션의 workspace sandbox에 네트워크 접근을 명시했다.
전역 sandbox·모델·플러그인 설정은 바꾸지 않았다. 시험용 trust 항목도 종료 후
정리하고 전체 나머지 설정을 비교했다. 이는 production hook trust 승인을 대신하지 않는다.

## 주요 부정 시험

34개 시험은 최신 미처리 교정·여러 세션 요구 보존, 같은 이력의 요청별 선택,
commit 전후 실제 자식 프로세스 강제 종료, partial line·rotation·중복,
가설/패치의 PASS 승격 방지, 파일·branch·dirty 변경, 프로젝트·token 경계,
필수 후보/pack budget 차단, 401 재시도 중지, 429 bounded retry와 예산 예약,
응답 schema 검증, cache 입력 일치, 전송 금지, 정책 변경·삭제 중 결과 적용 차단,
재수집 tombstone, pack 생성/읽기 구분, 기존 hook·legacy 보존을 포함한다.

고정 bootstrap에 사용자 원문을 넣지 않으며, JCM 내부 읽기 결과는 다시 의미 처리
대상으로 수집하지 않는다. 단순히 명령에 `jcm`이 포함됐다는 이유로 정상 도구 기록을
제외하지 않는다. 실제 과거 악성 원문에 대한 광범위한 agent 행동 평가는 남아 있다.

## 아직 통과하지 않은 필수 gate

| 항목 | 현재 경계 |
|---|---|
| Desktop 일반 새 세션·지속 trust | CLI 실제 시험과 구분. 일회성 vetted hook bypass만 사용 |
| 자동 compact 및 직후 복원 — T24 | hook 연결은 있으나 실제 자동 compact 미실행 |
| 장기·다중 task 및 한국어 recall — T15/T16 | project scope의 보수적 후보와 한정된 corpus만 검증. 관계 기반 추가 탐색·task 분할 필요 |
| 삭제·백업 복원 — T21 | session tombstone·알려진 파생자료 무효화는 구현. export/restore 및 backup tombstone 적용 미구현 |
| fault tolerance 확대 | 프로세스 종료 시험은 통과. 실제 power loss·디스크 고갈·migration rollback은 미검증 |
| 보안 행동 평가 — T19/T23 | bootstrap·원문 경계 fixture는 통과. hostile source 행동 및 host spill 전체 평가 필요 |
| 전체 배포 P4 | 생산용 skill/plugin 배포·uninstall·upgrade·운영 profile approval은 미완료 |

따라서 runtime의 제품 capability는 의도적으로 `limited`다. 실제 정상 Jev dispatch의
`quality=normal`, capture/read 증거, 제품 전체의 automatic 지원을 서로 섞지 않는다.
현재 프로젝트 자체의 지속 기록·외부 전송을 전역으로 활성화하지 않았고, 실험은
명시적으로 생성한 합성 프로젝트에 한정했다. 완료한 시험 fixture는 비활성화했다.
