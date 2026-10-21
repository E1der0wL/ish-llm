# A Provider 전환 검증 기록

H1–H5 및 [P1 최종 로컬 게이트](p1-verification.md)는 완료했다. 현재 A를 진행 중이며,
과거 backend 브랜치의 구현/시험은 읽기 전용 참고다. 이 문서는 A의 변경·실제 실행 증거만
추가한다. [Level 1–3](verification-policy.md)과 [공통 호출 값](model-protocol.md)을 따른다.

## A-b: LiteLLM wire 변환 — 로컬 Level 2

`providers/wire_codec.py`가 공통 message/Tool 정의/request를 SDK wire로 변환하고 SDK의
message/Tool 정의를 공통 값으로 읽는다. signed/opaque 확장, 원래 인자 문자열, multimodal
part와 누락/null/빈 값의 차이를 보존한다. 공통 인자와 원래 문자열의 JSON 타입·값이 같을 때만
원문을 재사용하며 False/0·True/1·int/float를 Python 동등성으로 합치지 않는다. 부분/비유한/
비객체 인자와 잘못된 falsey 호출 배열을 거부하고 opaque가 role·호출 ID·인자를 주입하지 못한다.
알려진 Adapter 상태의 필수 필드 소실과 손상된 content는 기본값으로 복원하지 않는다.

이전 backend wire_codec의 해당 변환 알고리즘을 읽기 전용으로 참고했다. 현재 공통 값과
정확한 상태 검증에 맞춰 구현했으며 파일 통째 복사나 구형 record Reader는 없다. 아직 필요한
소비자가 없는 raw request decoder/호환 진입점은 추가하지 않았다. 기존707개 제품/시험 소스는
A-a와 같고 codec/시험2개만 추가했다. 현재 제품 경로에는 아직 연결되지 않아 Hub 소비자
실행은 배정하지 않았다. 실제 Adapter·Engine·Processor·Tool 연결과 SDK/비 OpenAI 실제
파일 효과·승인·재개 검증은 후속 A에 남아 있다.

| 실행 | 발견/시작/통과 | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 초기 집중 LLM | 22/22/22 | 0/0/0 | 0.019초 | 0 |
| 현재 Level 2 LLM | 161/161/161 | 0/0/0 | 49.440초 | 0 |

expected failure·unexpected success·missing·unexpected·duplicates도 각 실행0이다.
초기22개 통과 후 코드 검토로 Tool 결과의 명시 null 이름과 빈 호출 필드 보존을 보강했다.
초기 소스 hash `3a00aed3e9060734ff1f01bb086d0016434ea4e4381f4604ca795b28154747e3`의 결과는 현재 결과로 재사용하지
않았다. 현재22개 계약 시험과 안전성9개 모듈116개·감사23개를 함께 새로 실행했다. 별도 실행을
합산한 전체 회귀로 표현하지 않는다. 기존 실제 효과·원본 보존·중단/재개 Assertion은 유지했다.

공통 모델 문서의 원문 전체를 보존한 채 별도 Provider 절만 추가했다. 이 문서를 참조하는
기존 S4 리뷰2건은 나머지 pin·사유·시험 ID가 같음을 확인하고 문서 hash만 재검토했다.
새 예외, checker/미해결 목록 변경은 없다. R3 True/exit0, 정적 raw171/reviewed153/
미해결18(S6), S1–S5·new/stale/review오류0/exit0, 문서 오류0/exit0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a2-focused/llm.json --modules tests.llm.test_model_protocol tests.llm.test_provider_codec
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a2-level2/llm.json --modules tests.llm.test_model_protocol tests.llm.test_provider_codec tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions tests.llm.test_architecture_checks tests.llm.test_architecture_reviews
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_budget_mutation
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --output /workspace/llm-clean-resume/a/a2-level2/aux/static.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/a/a2-level2/aux/docs.json
```

현재 실행 HEAD `c9c76d92b491191724494c71302b3301955b1c96`에 A-b 변경을 더했다. source709개 hash
`2853f9b81a23b091b62faca82fd7112e697368309cbef4d8cdcccad7c83f7272`, 입력/환경873개 hash
`c6cf339e09cee4ba6caa9964197cf9ecd901f679e295d8923e5c019d4248b617`가 실행 전후 같다. Python3.12.14·136 의존성·env5개와
host `d833790959a76ac457fdeea3f21a6bea1eecfe12`를 유지했다. 원시 명령/exit/ID/log/manifest·초기/현재 소스와
감사 근거는 `/workspace/llm-clean-resume/a/a2-focused/`, `/workspace/llm-clean-resume/a/a2-level2/` 및 a2 증거에 보존한다.

문서를 먼저 갱신하고 동일 소스·staged diff 확인 후 로컬 커밋한다. **이번 소단계는 전체 회귀와
원격 CI를 실행/확인하지 않았다.** 영향은 아직 연결되지 않은 변환 모듈로 한정되며 다음
연결 소단계의 묶음 Level3 후 Push한다. A 전체·B–F는 미완료이고 S6 18건도 남아 있다.

## A-a: 순수 공통 호출 값 — 로컬 Level 2

`core/model_protocol.py`가 메시지/content/Tool 제안·결과·정의/요청/응답과 role·종료·선택
선언을 소유한다. SDK/영속 도메인 의존성 없이 finite JSON·opaque 복사·nullable 사용량·
완성된 호출 인자·교환 짝과 현재 형식 Reader/Writer를 검증한다. 런타임 client는 호출 사본에서
identity를 유지하고 영속화는 거부한다. nested optional 필드도 Reader에는 필수이며 bool/
float 버전, 구형 wire dict·누락·추가 필드를 기본값으로 복원하지 않는다.

기존 705개 제품/시험 소스는 P1 게이트와 동일하며 새 모델/시험 2개만 추가했다. 실제 import
검색에서 새 모델의 소비자는 새 시험뿐이다. 기존 Adapter·Engine·Processor·Hub는 아직
전환하지 않았다. 따라서 이번 소단계에 Hub 소비자 실행은 배정하지 않았고, 전체 회귀는
후속 연결 소단계의 묶음/A 상위 게이트로 유예했다. 새 값 자체는 실행 권한·원장·외부 효과를
소유하지 않는다. 승인 snapshot과 영속 CompletionResult의 예약/사용량 계약은 그대로다.

이전 backend의 model_protocol 일부 알고리즘과 test_model_protocol/test_common_provider의
실패 시나리오를 참고했다. 파일 통째 복사·호환 투영·과거 통과 증거 승계는 하지 않았다.
현재 owner enum, 정확한 nested 필드, 유한/순환 JSON 거부와 mutable 증거 분리 검증을 구현했다.
비 OpenAI Adapter의 실제 파일 효과·재개 검증은 후속 A에 남아 있다.

| 실행 | 발견/시작/통과 | failure/error/Skip | 시간 | exit |
|---|---:|---:|---:|---:|---:|
| 집중 LLM | 11/11/11 | 0/0/0 | 0.009초 | 0 |
| Level 2 LLM | 150/150/150 | 0/0/0 | 49.296초 | 0 |

expected failure·unexpected success·missing·unexpected·duplicates도 각 실행0이다.
Level2는 새 계약11개와 공통 안전성9개 모듈116개, 감사 검사기23개를 실행했다. 새 계약11개는
Level2에서도 실행했으며 두 선택 실행을 합쳐 전체 회귀나 고유 시험 수로 표현하지 않는다.
승인·예산·효과·재개·RAG·삭제·필수 기록·트랜잭션 실제 원본 보존/장애 주입 Assertion을 유지한다.

초기 정적 보고는 `_record`의 정확한 정수/owner 버전 비교2건을 S4 개별 검토 대상으로
탐지했다. 당시 개별 checker exit는 따로 포착하지 못했고 요약을 출력한 외부 shell exit는0이다.
new2/required2였으므로 성공으로 처리하지 않았다. 실제 클래스 선언/호출 관계와 exact-field
검증 및 4개 정확한 시험 ID를 근거로 해당2건만 분류했다. 기존151개 리뷰·checker·미해결
목록은 변경하지 않았다. 새 Level2 runner가 실제 exit를 보존했다. R3 True/exit0,
정적 raw171/reviewed153/미해결18(S6), S1–S5·new/stale/review오류0/exit0, 문서 오류0/exit0이다.

```bash
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a1-focused/llm.json --modules tests.llm.test_model_protocol
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.run_regression --suite llm --output /workspace/llm-clean-resume/a/a1-level2/llm.json --modules tests.llm.test_model_protocol tests.llm.test_interaction_storage tests.llm.test_usage_deletion tests.llm.test_operation_integrity tests.llm.test_checkpoint_integrity tests.llm.test_rag_storage tests.llm.test_lifecycle_integrity tests.llm.test_lifecycle_deletion_integrity tests.llm.test_nested_graph tests.llm.test_transactions tests.llm.test_architecture_checks tests.llm.test_architecture_reviews
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_budget_mutation
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --output /workspace/llm-clean-resume/a/a1-level2/aux/static.json
/workspace/ish-llm/.venv-linux312/bin/python -m tests.llm.check_docs --output /workspace/llm-clean-resume/a/a1-level2/aux/docs.json
```

HEAD `81ce84f86246ba8b9470ae4e4e103876c53f255d`에 A-a 변경을 더해 실행했다. source707개 hash
`d2e08b5eb1be791d41ba5b4f6116ec768ebb5a4ccf70c17517b546c379a72fa5`, 입력/환경870개 hash
`bf3b8ce69c0118c91d75467a2272dd50a35708916321cbfe72f4952847fabd0b`가 실행 전후 같다. Python3.12.14·136 의존성·env5개,
host `d833790959a76ac457fdeea3f21a6bea1eecfe12`를 유지했다. 명령/exit/ID/log/manifest·초기 정적 실패 보고·
기존 소스 동일성/감사 근거는 `/workspace/llm-clean-resume/a/a1-focused/`, `/workspace/llm-clean-resume/a/a1-level2/`와 a1 증거에 보존한다.

문서를 먼저 갱신하고 동일 소스·staged diff를 확인해 로컬 커밋한다. **전체 LLM/Hub 회귀와
원격 CI는 이번 소단계에서 실행/확인하지 않았다.** 다음은 실제 Adapter와 공통 소비자 연결이다.
A 전체와 B–F는 미완료이며 S6 18건도 남아 있다. 다음 묶음 Level3 성공 후 작업 브랜치에 Push한다.
