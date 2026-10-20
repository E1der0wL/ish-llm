# 영속 데이터 수명주기 보강 검증

[수명주기 계약](data-lifecycle.md)의 H1부터 순서대로 검증한다. 구현한 범위와 남은
감사 항목을 구분하며, 원감사 실패 기록은 [P1 검증](p1-verification.md)에 보존한다.
시작점은 `refactor/llm-clean`의 `df6b50813eba11776282a3feed8a1abb26d49a7a`와
기존 P1-c Provider 변경이다. main·이전 backend 브랜치·Draft PR #1은 변경하지 않는다.

## H1 승인 기록

### 구현 범위

- `core/interactions.py`의 Request·Option·Response Reader는 현재 직렬화 필드를 모두
  요구한다. 생성 기본값으로 만료·actor·상태를 복구하지 않으며 명시적인 None·False·0·빈 값은
  필드 계약에 따라 보존한다. 날짜·ID·지문·정책 응답의 policy_id도 검증한다.
- `services/runtime/interactions.py:InteractionRecord`가 승인 저장 형식과 버전을 소유한다.
  요청 공개 시 미응답 상태를 명시적으로 저장하고, 이후 조회는 필수 파일을 생성하지 않는다.
  response/replacement의 명시적인 null과 필드·파일 소실을 구분한다.
- 요청 공개와 checkpoint 저장, 갱신된 새 요청과 이전 요청의 연결은 기존 workspace 잠금과
  저장 트랜잭션에 묶는다. 응답·취소·갱신은 같은 owner Reader를 사용한다.
- 새 Resume Run은 기존 요청의 응답·취소·갱신 증거를 보존한다. 결과가 불확실한 실행의
  재시도 요청은 Run·checkpoint·실행 레코드에 묶이며 새 시도의 효과를 과거 승인으로 허용하지 않는다.
- 보존한 Graph 확인 응답과 명시적 `{}` 입력은 Request의 기존 선택·입력 규칙으로 비교한다.
  일반 Tool approval에는 빈 확인을 허용하지 않고, 거절·입력 변경·bool/number 혼동을 거부한다.
- 백업은 RunRepository를 통해 승인 owner의 전체 제어 기록·갱신 연결 검증을 호출한다.
  공통 백업 인프라에 승인 형식 해석을 추가하지 않는다.

손상된 승인 자료는 원위치에서 보존하고 사용을 차단한다. 이 단계에는 격리·물리적 삭제 API,
보존 기간·자동 삭제 정책·구 형식 Reader·자동 migration이 없다. 초기 Resume의 승인 증거
복사가 끝나기 **전** 프로세스가 종료된 경우의 복구는 별도 미검증이며, 필수 증거가 없을 때
미응답으로 복원하지 않는다. 모든 영속 owner의 무결성 보강 완료를 뜻하지 않는다.

### 실행 기록

Python 3.12.14, 실제 ish 호스트 `d833790959a76ac457fdeea3f21a6bea1eecfe12`,
설치된 Chroma/Kuzu와 실제 Xvfb/xclip을 사용한다. 외부 인증 모델은 호출하지 않는다.
Bubblewrap의 user namespace를 허용하는 실행 환경에서 제품 격리 설정은 유지한다.

| 시점·검사 | 실제 결과 |
|---|---|
| 수정 전 승인 저장 시험 | 8개, 실패 34건(하위 사례 포함), error 2, 1.558초, exit 1 |
| 첫 집중 회귀 | 103개, failure 5·error 1, 21.308초 |
| 두 번째 집중 회귀 | 106개, failure 5·error 0, 23.023초 |
| 승인·checkpoint·운영 저장 집중 회귀 | 132개 통과, 26.404초 |
| 승인 저장·정적 검사 자체 시험 | 27개 통과, 6.295초 |
| 첫 전체 LLM | 1,727개 중 1,725 통과, failure 1·error 1·Skip 0, 352.032초 |
| 첫 전체 Hub | 540개 통과, failure·error·Skip 0, 491.982초 |
| 재개 비교 수정 후 집중 회귀 | 70개 통과, failure·error·Skip 0, 11.500초 |
| 수정 후 전체 LLM | 1,728개 통과, failure·error·Skip 0, 352.880초 |
| 수정 후 전체 Hub | 540개 통과, failure·error·Skip 0, 491.762초 |

첫 전체 LLM의 error는 초기 checkpoint 복사 실패 후 재개할 때 보존된 응답과 `{}` 확인을
원시 JSON으로 비교한 것이었다. Request의 선택·입력 해석으로 수정하고 충돌 거부 시험을 추가했다.
failure는 container 필드 소실 시 필수 승인 제어 기록 검증이 더 일찍 거부하여 오류 문구가
달라진 경우다. 엔진의 원래 무결성 거부를 직접 검증하고 공개 API의 조기 거부도 검증한다.
추가 Run·모델 호출·실제 파일 효과가 없고 손상 원본을 보존한다는 assertion은 유지한다.

`test_interaction_storage`는 모든 직렬화 필드 누락, 제어 파일·필드 소실, 잘못된 타입·버전,
손상 백업, 동시 갱신, 재시작 후 응답·재개, 시도별 재승인과 실제 효과 횟수를 검사한다.
갱신 트랜잭션의 각 fsync 직후 별도 프로세스에서 `os._exit(75)`를 주입하여 복구 후
이전 상태 또는 완전한 새 연결만 남는지 확인한다. 이는 커널 short-write·전원 손실 시험이 아니다.

R3 mutation 검사는 `max_tool_calls=1` 전달을 의도적으로 제거했을 때 두 번째 파일 효과로
실패하는지 확인한다. 예상한 실패 1건을 탐지해 검사 도구가 exit 0으로 끝났다.
정적 검사는 new/stale/S1 후보 0, 전체 미정리 후보 580개다. H1에서 기존 후보 7개만
실제 변경에 맞춰 제거했으며 새 면제는 없다. 현재 형식 버전의 정확한 타입 검사도
dataclass의 `self` 필드에 대해 검증하도록 AST 검사를 보강하고 반례 시험을 추가했다.

```bash
python -m unittest tests.llm.test_interaction_storage tests.llm.test_interaction_decisions \
  tests.llm.test_conversation_turns tests.llm.test_long_running -v
python -m tests.llm.run_regression --suite llm --output /tmp/llm-regression.json
python -m tests.llm.run_regression --suite hub --output /tmp/hub-regression.json
python -m tests.llm.check_architecture --require S1 --output /tmp/architecture.json
python -m tests.llm.check_budget_mutation
python -m tests.llm.check_docs --output /tmp/docs.json
```

실행 환경은 `TERM=xterm-256color`, `LITELLM_LOCAL_MODEL_COST_MAP=True`,
`LITELLM_MODE=PRODUCTION`이고 `HUB_TEST_XVFB`·`HUB_TEST_XCLIP`은 설치된 실행 파일을
가리킨다. 이번 실행의 Python은 `/workspace/.setup/ish-llm/verified-next/bin/python`이다.
원시 로그·JSON·검사 시점 소스 hash는 `/workspace/llm-clean-p1/h1-approvals/`에 보존한다.
전체 회귀 중 제품·테스트 Python 파일은 변경하지 않는다.
최종 전체 회귀에서 648개 Python 파일의 전후 hash가 일치했다. 실제 ish 통합과
Xvfb/xclip 9개 시험도 실행되었으며 Skip은 없다.
기존 Provider 작업은 `7b1120b`, 승인 보강은 `fdc2718`로 분리하여 원격 브랜치에 Push했다.
[승인 단계 GitHub Actions](https://github.com/E1der0wL/ish-llm/actions/runs/39425735628)의
contracts (llm)·contracts (hub)도 모두 success다.

## H1 RAG 활성 포인터

승인 단계 `fdc2718`을 Push한 뒤 시작했다. 수정 전 실제 문서 등록 후 active.json을
제거하면 조회가 빈 코퍼스를 반환하고 compact가 원래 세대를 삭제했다. identity.json을
제거하면 조회가 새 ID를 만들었다. 영구 회귀 두 개 모두 실패했다(0.705초, exit 1).

RAGComponent가 identity/active/generations 경로·UUID 형식·graph schema 버전을 소유한다.
새 Component의 명시적 초기화에서 필수 identity와 `generation=null` 포인터를 함께 만들며,
기존 자료를 조회·재선택할 때는 필수 기록을 검증한다. nullable 포인터와 실제 세대 존재가
모순되거나 활성 corpus가 사라지면 거부한다. 백업도 같은 snapshot 검증을 사용한다.
bool·float로 저장된 버전을 정수 버전으로 수락하지 않는다.

| 검사 | 실제 결과 |
|---|---|
| 수정 전 실제 포인터·identity 소실 | 2개 모두 failure, error·Skip 0 |
| 최초 집중 RAG·운영 저장 회귀 | 60개 통과, 10.563초 |
| 확대 RAG·운영 저장 회귀 | 124개 통과, failure·error·Skip 0, 21.545초 |
| 첫 전체 LLM/Hub | 실행 세션 중단, 완료 JSON 없음; 통과로 집계하지 않음 |
| 옛 빈 상태 기대값의 독립 재현 | 1개 failure, 0.153초 |
| 기대값 수정 후 RAG·checkpoint | 26개 통과, failure·error·Skip 0, 4.919초 |
| 재실행 전체 LLM | 1,734개 통과, failure·error·Skip 0, 354.185초 |
| 재실행 전체 Hub | 540개 통과, failure·error·Skip 0, 491.192초 |

첫 전체 실행 중 `test_cancel_preserves_finished_chunks_then_explicit_delete`가 옛 형식의
active.json 부재를 기대해 실패했다. 공개 세대가 없다는 의미를 현재의 필수
`{"generation": null}` 포인터·세대 디렉터리 부재·빈 문서 목록으로 확인하도록 변경했다.
완료한 배치 보존·lease·명시 삭제의 기존 assertion은 유지했다.

새 시험은 조회·새 등록·compact·백업·재선택의 거부와 원본 파일 hash 보존, 모델 호출 없음,
정상 빈 저장소, 재선택 시 identity 유지, 초기 포인터 저장 실패의 rollback을 검증한다.
실제 Chroma/Kuzu를 사용하고 임베딩은 테스트 모델이다.

```bash
python -m unittest tests.llm.test_rag_storage tests.llm.test_rag_components \
  tests.llm.test_rag_optimization tests.llm.test_rag_resilience \
  tests.llm.test_rag_extraction_repair tests.llm.test_unified_rag \
  tests.llm.test_markdown_rag tests.llm.test_operational_storage -v
```

전체 회귀·정적 검사 명령과 환경은 승인 단계와 같다. 원시 로그·JSON·소스 hash는
`/workspace/llm-clean-p1/h1-rag/`에 보존한다. Component 루트 전체 소실의 존재 증거,
상위 삭제/참조 보호와 기존 publish 자동 compact의 Application 이관은 이 단계에서
해결했다고 주장하지 않는다.
최종 회귀 동안 649개 Python 파일의 hash는 일치했다. 정적 검사는 new/stale/S1 0,
잔여 후보 580개이고, 문서·링크·Python 예제 구문 검사의 오류도 없다.

## 남은 게이트

H1의 승인·RAG 활성 포인터 보강은 검증을 완료했으며 Session 삭제의 Project quota 초기화 차단은 아직 미구현이다.
H2–H5의 owner별 필수 존재·손상 처리·안전한 격리/삭제·Application 정리 정책·참조 보호도
남아 있다. 이 안전성 보강 전 P1의 나머지 계약군과 A–F를 완료로 표시하지 않는다.
실제 인증 모델·커널 short-write·전원 손실·네트워크 파일시스템은 미검증이다.
