# 이전 별도 실험: 질문별 전달 계약 검증 — 2026-09-30

이 문서는 1.0.2의 대표 7흐름 평가에 앞서 실행한 별도 실험 기록입니다. 아래 원문 수치와 실패 경계는 그대로 보존했으며 최신 완료 결과로 합산하거나 해석하지 않습니다. 1.0.2의 수용 범위와 결과는 [릴리스 노트](releases/1.0.2.md)와 [공개 검증 요약](../evidence/continuity-consumer-1.0.2.json)에 있습니다. 과거 raw 및 전체 집계는 로컬에 보존하며 이 배포에 포함하지 않습니다.

**검증은 완료했으며, 대규모 비용 비교와 전체 수용 판정은 통과하지 못했다.** 최종 런타임의 native 5단계와 실제 Codex 답변, 근거 보존 계약, 회귀·패키징은 통과했다. 대규모 brief도 159/159쪽의 실제 모델 노출과 10개 정답 기준을 모두 통과했다. 그러나 full 소비군이 필수 페이지 읽기를 완료하지 않아 유효한 대규모 비용 비교는 성립하지 않는다. 과거의 90% 입력 절감이나 API 페이지 저장을 이번 비교 성공으로 사용하지 않는다.

이번 마무리 검증은 제품 런타임을 바꾸지 않았다. `compare_host_delivery.py`에 실제 모델 노출 검사를 추가하고 두 회귀 사례를 추가했다. 전역 설치·배포·커밋·push는 수행하지 않았다. 집계는 로컬 원본 `evidence/context-delivery-20260930.json`, 상세 증거는 `.task-notes/final-validation-20260930/`에 보존했다. 기존 증거와 실패 실행은 삭제하지 않았다.

## 구현과 검증 범위

- `detail_plan`은 exit 0 또는 completed descriptor만으로 native 도구 본문을 제외하지 않는다.
- `query_context`가 질문에 필요한 span, 상태, 관계 양쪽과 조건, 선택된 원문 참조를 완성하고 전달 계획을 동결한다. 중첩 object 조건을 보존하며, 제외된 복사 구간의 참조를 되살리지 않는다.
- `core_context`는 깊이와 순서를 처리한다. native 도구 종류나 별도 보고 판단으로 필수 근거를 다시 제거하지 않는다.
- 렌더링 뒤 exact text/span/revision, provenance·상태, 관계 endpoint, task frame, 필수 metadata·순서를 검사한다. 실패하면 허용된 exact source span으로 한 번 복구하고 모든 view를 degraded로 표시한다. 재검증 실패, 삭제·revision 변경·정책 철회는 blocked다.
- 페이지·delta decoder로 논리 brief를 복원하며, 정확한 base와 결과를 검증한다. 계약 검증이 없는 구형 pack은 delta base로 사용하지 않는다. 임의 recent-N/top-N/API 횟수 제한은 추가하지 않았다.

guard가 0.2를 넘으면 보존하고 0.2–0.8의 불확실한 경우 enclosing admitted context를 남기는 현재 정책은 일반 recall 보장이 아니다. 이번 대규모 결과에서는 이 정책이 전달 크기의 주요 원인이었다.

## 최종 소스의 정확성

런타임 hash: `69131ea8008dbe2795b728b48a7af483554d8bf0beb596b7896ffb238b2b759c`.

| 경로 | 최종 결과와 경계 |
|---|---|
| Python 3.11 전체 회귀 | **261개 PASS**, 191.817초. `full-regression.log` |
| 변경 전 P1·손실 주입 | 기존 native 고유 blocker 손실 재현 후 수정. span/state/default 누락 검출·복구 및 정책 철회·forget의 blocked 경로 통과. 이전 RED/GREEN 증거 유지 |
| native adapter → 실제 Jev → 공개 API | cold, warm, 정정, 새 B, A 복귀의 필수 사실·계약·읽기 검사 모두 통과. **B의 작업 식별은 ambiguous**이며 해결됐다고 판정하지 않음. `native/result.json` |
| 실제 Codex 5단계 | full과 brief/delta 양쪽 **8개 정답 항목 × 5단계 PASS**. 각 단계의 전체 응답이 실제 tool output에 노출됐음을 추가 감사로 확인. `native-consumer/result.json`, `retroactive-exposure-audit.json` |
| 실제 노출 검증 회귀 | tool 내부 저장만 된 페이지는 제외, 잘린 출력은 정확한 재조회 전까지 제외, 이전 턴 출력은 재사용하지 않음. 두 신규 회귀 PASS. `exposure-tests.log` |
| 번들·wheel | 최종 bundle 일치 PASS. 런타임·bundle bytes가 같은 이전 격리 plugin **13개**, 설치 wheel native 계약 **9개** 결과 재사용 |

Jev fixture와 정답은 구현 전 고정한 `tests/fixtures/native_delivery_eval.json`을 그대로 사용했다. 초기에 B 단계의 false 의미를 “해당 artifact에 기록으로 확립되지 않음”으로 명확히 한 이력이 있으므로 별도 미사용 평가군에 대한 일반화 증명은 아니다. 기계적 보존, 모델에 대한 실제 전달, 의미적 정답은 각각 검사한다.

## 통과한 native 소비 비용

기존 기본 설정 `gpt-6.1-sol / low`를 양쪽에 동일하게 적용했다. 이 실행 동안 전역 설정 hash도 유지됐다. lookup·원문 확장·재조회·모든 model response를 포함하며 cached input은 input의 부분집합이다. CLI resume의 누적 snapshot을 합산하지 않고 고유 response별 `token_usage_record`를 합산한다.

| 지표 | Full baseline | Brief/delta candidate |
|---|---:|---:|
| Input tokens | 379,194 | 342,493 |
| Cached input | 325,120 | 299,520 |
| Uncached input | 54,074 | 42,973 |
| Output tokens | 783 | 793 |
| 소비 시간 합계 | 72.42초 | 72.61초 |

총 입력은 **9.7% 감소**, uncached 입력은 **20.5% 감소**했다. 소비 시간은 거의 같았다. 단일 소규모 5단계 사례로 반복 평균·가격 절감률·모델 비교를 뜻하지 않는다. 실제 Jev는 별도로 39회, input 120,110 / output 10,267 tokens였다. 단계별 사용량과 지연은 집계 JSON에 있다.

## 대규모 이력: brief 소비 통과, full 기준선 실패

7,230개 event의 불변 사본에서 중단된 재평가를 재개했다. 원본 ID/revision/blob fingerprint는 유지됐고, 원본·새 격리 fixture는 검증 후 비활성 상태다. 기존 source 및 의미 판단 캐시를 사용했으므로 이번 재개 시간을 **빈 캐시 cold 비용으로 해석하지 않는다**.

| 지표 | 재개 | 같은 이력 warm |
|---|---:|---:|
| dispatch 시간 | 192.05초 | 23.48초 |
| brief 공개 API 전체 읽기 | 별도 시간 미측정 | 126.45초 |
| 준비와 brief 읽기 합계 | 별도 합계 미측정 | 150.68초 |
| 추가 Jev 호출 | 275 | 0 |
| Jev input / output | 9,266,256 / 130,426 | 0 / 0 |

논리 brief는 522개 기록이며 **159쪽·6,400,415 bytes**, full은 **369쪽·15,736,173 bytes**다. 470개 기록이 `QUERY_ASSESSMENT_UNCERTAIN`으로 enclosing source 전체를 보존했다. 원문 text는 4,742,954자로, 기존 판단 재사용만으로 최종 전달량과 페이지 읽기 비용이 해결되지는 않았다. `history-size-analysis.json`에 근거를 남겼다.

첫 bulk 소비에서 baseline은 읽기 로그상 369쪽을 조회했지만, 실제 모델에는 **6쪽만 노출**됐다. 나머지는 exec 내부 메모리에 저장돼 있었으므로 10항목 정답이 맞아도 baseline으로 인정하지 않았다. candidate는 159쪽이 노출됐지만 context window 초과 오류로 답변을 만들지 못했다. 해당 실행은 비용 비교에서 제외했다.

이 결함을 잡도록 native `function_call_output`/`custom_tool_call_output` 안의 완전한 페이지 값을 대조하는 검사를 추가했다. 동일한 원문·정답·격리 모델 설정으로 페이지별 tool turn을 요구해 다시 실행한 결과는 다음과 같다.

| 최종 소비군 | 실제 노출 | 필수 읽기 완료 | 정확한 정답 일치 | 종료 코드 |
|---|---:|---|---|---:|
| Full baseline | 4/369 | False | False | 0 |
| Brief candidate | 159/159 | True | True | 0 |

Baseline은 4쪽에서 조기 답변했고, 이름도 고정된 한국어 oracle 대신 영어로 반환했다. 필수 읽기 실패만으로 수용 불가이며 이름 표기 차이를 별도의 의미적 오류로 확대하지 않는다. Candidate는 159/159쪽의 완전한 노출, 필수 읽기, 10개 정답 기준을 모두 통과했다. 답변·전체 실제 노출 목록은 `history-consumer-visible/result.json`과 native JSONL에 보존했다. **유효한 full 기준선이 없어 대규모 입력 절감률은 계산하지 않는다.**

최종 실행 자체의 소비 비용은 baseline input 255,432 (cached 177,024, uncached 78,408), output 664, 45.44초; candidate input 23,590,829 (cached 19,671,936, uncached 3,918,893), output 35,744, 1719.49초다. Candidate는 8번의 compaction을 거쳤다. 약 28분 39초와 입력 약 2,359만 tokens는 올바른 최종 답변과 별개로 남은 성능 문제를 보여준다. 이 수치는 성공한 두 경로의 비교가 아니다. 앞선 실패 bulk 실행 비용도 집계 JSON에 별도로 유지했다.

이력 재평가 전체 시도의 Jev 비용은 앞선 중단 실행을 포함해 **989회**, 반환된 input **28,425,542**, output **912,151** tokens다. 성공 789, context rejection 199, 응답 미기록 1이며 마지막 호출의 반환되지 않은 usage는 미측정이다. 앞선 비용을 재개 비용에 숨기거나 warm 비용으로 중복 합산하지 않았다.

## 환경과 남은 범위

- 이전 인증 부재는 기존 셸 설정의 인증값을 검증 프로세스 환경에만 전달해 해소했다. 인증값을 출력·저장하지 않았다.
- 최종 대규모 실행 중 전역 reasoning 설정이 low에서 xhigh로 바뀌었다. 이 검증은 전역 설정을 쓰지 않았고 원인은 단정하지 않는다. 격리 소비군은 시작 시 복사한 low를 유지했지만 엄격한 전역 환경 gate는 실패다. 설정을 되돌리지 않았다. 앞서 종료된 native 비교의 환경 gate 통과는 유지한다.
- 원본 이력에는 `TRANSCRIPT_SCAN_CEILING`, `UNKNOWN_TRANSCRIPT_RECORD`, `EVENT_TOO_LARGE` 등 수집 공백이 남아 있다. 이번 검사는 이미 수집된 사본의 보존을 확인하며 원래 세션 전체의 완전한 수집을 증명하지 않는다.
- native 입력 → adapter → 실제 Jev → 공개 API → 실제 Codex CLI 답변을 검증했다. Desktop 일반 hook/UI 전체 흐름, 실제 자동 compaction의 제품 동작, Blender·제품·물리 기기 성공은 검증 범위 밖이다.
- 다음 구현 과제는 필수 근거 보존을 유지하면서 불확실성으로 인한 과도한 원문 확대와 전체 페이지 읽기 비용을 줄인 뒤 full 기준선까지 완주하는 비교를 검증하는 것이다. 이번 검증에서 근거를 버리거나 임의 상한을 넣어 수용 조건을 낮추지 않았다.

## 재실행

```sh
PYTHONPATH=src python3.11 -m unittest discover -s tests
python3.11 scripts/build_plugin.py --check
PYTHONPATH=src python3.11 scripts/live_native_delivery.py --output-dir NEW_OUTPUT
PYTHONPATH=src python3.11 scripts/compare_host_delivery.py \
  --case NEW_OUTPUT/sequence-consumer-case.json --output-dir NEW_CONSUMER_OUTPUT
```

실제 Jev는 프로세스의 `TYPESAFE_API_KEY`, 소비자는 유효한 Codex 인증이 필요하다. 정답은 소비 workspace에 넣지 않는다. 검증 종료 후 해당 격리 fixture만 disable한다. 이전 검증 결과는 집계 JSON의 `previous_validation`에 원형으로 보존했다.
