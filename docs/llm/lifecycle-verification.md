# 영속 데이터 수명주기 보강 검증

[수명주기 계약](data-lifecycle.md)의 H1부터 순서대로 검증한다. 구현한 범위와 남은
감사 항목을 구분하며, 원감사 실패 기록은 [P1 검증](p1-verification.md)에 보존한다.
시작점은 `refactor/llm-clean`의 `df6b50813eba11776282a3feed8a1abb26d49a7a`와
기존 P1-c Provider 변경이다. main·이전 backend 브랜치·Draft PR #1은 변경하지 않는다.

## H2-d 출력 원본·필수 manifest·현재 출력 필드

H2-c와 EOF 공백 정리 커밋 `25fd7c6868f0f09715eba1c877ad7d4efb063a85`에서 이어서 작업했다.
출력 끝 newline 소실을 빈 출력으로 읽기, 손상 끝줄 뒤 append, 저장된 final 누락을 True로
복원하던 3개 실패를 `tests.llm.test_output_integrity`에 편입했다. 구현 전 별도 probe는
3 failure, error·Skip 0, 0.002초, exit 1이었다.

### 구현 경계

- EngineDelta/EngineOutput Reader는 모든 현재 직렬화 필드를 요구한다. 생성 초기값을
  조회에서 적용하지 않으며 명시한 빈 텍스트·None·False는 각 필드 계약대로 보존한다.
- 새 Run 생성 때만 빈 outputs.jsonl과 outputs.manifest.json을 함께 확정한다. 출력 owner의
  필수 manifest는 정확한 버전·바이트 수·최종 순번·원본 SHA-256을 보유한다. 기존 파일·manifest
  소실, 완전한 마지막 기록 소실, 끝 newline 소실, 변조는 조회·append·재열기·백업에서 거부한다.
  원본을 자르거나 빈 출력으로 재초기화하지 않는다.
- 출력 추가·manifest·Conversation 갱신은 기존 workspace transaction에 함께 참여한다.
  atomic_bytes는 기존 atomic_json의 원자 파일 교체를 재사용한다. 입증된 미확정 append의
  transaction undo는 유지하며 도메인 상태를 저장 인프라에서 추측하지 않는다.
- outputs.index.json은 파생 캐시다. 검증된 원본에서만 사용·재생성하고 캐시 필드·순번·앵커·
  prefix 전체 해시를 확인한다. 읽기 전 원본 전체 바이트를 해시하므로 커서의 JSON 디코딩 절감과
  별개로 I/O가 추가된다. 대용량 누적 출력의 운영 비용은 별도 측정하지 않았다.

### 집중 검증과 수정

첫 관련 52개에서 과거의 끝줄 무시 기대값 1 error(9.531초)를 확인해 원본 보존·거부 계약으로
수정했다. 이후 82 pass(13.498초), 추가 인덱스 검증 48 pass(6.383초)였다.
새 13개 회귀는 필드/manifest 소실·타입, 완전한 tail 소실, 손상 prefix와 캐시, 새 Run 재열기와
실제 자식 프로세스 exit 77을 포함한다. 두 번째 출력의 원본 쓰기 직후와 manifest 저장 직후
종료 모두 재시작에서 마지막 확정 출력·대화만 유지하고 실행을 자동 재생하지 않는다.

첫 전체 LLM의 실패는 recovery_probe의 ENOSPC 주입이 새 빈 출력 파일 초기화에 먼저 걸린
경우다. 첫 실제 델타 쓰기에 주입하도록 예제를 수정했다. 실제 프로세스·원본 보존 assertion은
유지했고 관련 52개가 19.188초에 통과한 뒤 전체 LLM을 다시 실행했다.

### 전체 회귀 — 2026-10-21

| 검사 | 실제 결과 |
|---|---|
| 최초 전체 LLM | 1,779개 중 1,778 pass, failure 1, error·Skip 0, 542.199초, exit 1 |
| 최종 전체 LLM | 1,779 pass, failure·error·Skip 0, 535.788초, exit 0 |
| 전체 Hub | 540 pass, failure·error·Skip 0, 683.761초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 557개, exit 0 |
| R3 mutation | 한도 전달 누락 시 두 번째 실제 파일 효과 검출, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 931개, anchor 34개, 오류 0 |

발견/시작 ID는 각각 1,779/540개이며 누락·중복·예상 밖 ID는 0이다. owner의 버전 선언으로
해소한 정적 후보 3개만 제거했다. 남은 557개는 승인된 예외가 아니다. Hub 이후 변경된 Python은
examples/llm/recovery_probe.py 하나이며 제품·Hub 소스는 같다. Hub가 해당 예제를 소비하지
않음을 확인했고 전체 Hub는 반복하지 않았다. 차이는 source-change.json에 기록했다.

실행 환경과 명령은 H2-a/b와 같다. 최초 전체 실행 전후 Python 655개 manifest는
`aea803d073d6ed32fb0db5f610c31f2523d6c91af37600696bfcafe6eca2450b`로 같다.
최종 LLM 실행 전후 같은 655개 manifest는
`180602e75a48570ea4b119c39d1b2f6c6ffd3a84851a3e937a0cdbd6776ee94a`로 같다.
로그·JSON·전후 manifest는 `/workspace/llm-clean-resume/h2-d/final/`에 보존한다.
전체 검증 성공 후 아래 계약 문서를 먼저 갱신하고 문서 검사 뒤 커밋·Push한다.

### H2 잔여 항목

동일한 소스에서 별도 Component probe 두 묶음이 각각 2 failure였다. 사용량 probe는
영수증 파일 소실 뒤 project_max_calls=1에서 두 번째 실제 파일 효과, 완료 종료 시각 누락
수락을 확인했다(0.098초). 저장소 probe는 선택/비활성 Memory 루트 소실 뒤 재선택이 새
identity를 발급함을 확인했다(0.176초). 모두 error·Skip 0, exit 1이며 H2-d 성공에 합산하지
않는다. 원본은 component-usage-loss-probe.py와 component-root-loss-probe.py 및 각 로그다.
이 4개 실패와 나머지 Component/도메인 계약, H3–H5·P1 잔여 작업·A–F는 미완료다.

## H2-c Checkpoint 개별 기록·manifest·Run 연결

H2-b 커밋 `b2447472f6ba2e561ee91df2467e98c5f04f9a35`에서 이어서 작업했다.
완료된 Graph 노드의 기록 파일 하나를 제거하면 Resume가 실제 효과를 반복하던 실패를
`tests.llm.test_checkpoint_integrity`에 편입했다. 구현 전 저장소 회귀는 1 failure,
error·Skip 0, 1.181초, exit 1이었다.

### 구현 경계

- Checkpoint owner는 현재 manifest의 모든 필드와 객체 타입을 요구한다. `record_revisions`는
  개별 기록 파일의 존재·내용 리비전을 선언한다. Run의 `checkpoints`와 `checkpoint_revisions`는
  같은 이름 집합을 가지며 manifest의 리비전을 검증한다. 명시적으로 초기화한 빈 기록은 유효하다.
- 필수 필드·manifest·디렉터리·개별 파일 소실, 미선언 파일, key/파일명/리비전 불일치는
  원본을 보존하고 조회·Resume·갱신·백업에서 거부한다. 초기화한 이름을 파일 소실 뒤 다시
  초기화하지 않는다. Engine payload의 의미는 해당 Engine에 남기고 저장 owner가 추측하지 않는다.
- 노드 파일·manifest·Run의 리비전과 연결은 기존 transaction에서 ACK 전에 확정한다.
  명시적인 Checkpoint 삭제는 파일과 두 연결 필드의 제거·삭제 표식을 함께 확정한다.
  초기 Resume 사본 생성 실패에 한정한 검증된 원본 참조는 유지한다.
- EngineContext의 Run은 실행 시작 snapshot이므로 Repository 조회는 현재 Run의 저장된
  리비전을 읽는다. 최신 기록을 읽기 위해 EngineContext를 가변 실행 상태로 바꾸지 않는다.
  갱신 시 manifest와 Run도 쓰며 읽기 시 기록 전체의 리비전을 검사한다. 대용량 부하와
  이 추가 I/O의 운영 비용은 별도 측정하지 않았다.

### 집중 검증과 수정

첫 관련 62개 검증에서 기존 오류 기대값 4개가 실패했다. 저장 리비전 검사가 더 이른 경계에서
거부하기 때문이다. Tool/Agent 사용량 시험은 저장 owner를 통해 손상된 의미 payload를 기록해
물리 무결성과 독립적으로 `tool_usage_corrupt`를 계속 확인한다. 파일 변조/소실 시험은 별도로
리비전 오류와 실제 추가 효과 0·원본 보존을 확인한다. 의미 검증을 checksum 성공으로 대체하지 않는다.

수정 후 90 pass(26.168초)였다. 추가 180개 중 1 failure(52.346초)는 handler가 오래된
EngineContext의 Run으로 최신 Checkpoint를 조회하는 실제 경로였다. Repository의 현재 Run
조회로 수정한 뒤 관련 86 pass(20.247초)를 확인했다. 각 성공 묶음의 failure·error·Skip은 0이다.

새 회귀에는 완료 manifest 저장 실패 rollback과 실제 자식 프로세스 exit 77이 포함된다.
완료 파일 저장 직후 및 manifest 저장 직후/Run 저장 전 종료 모두 재시작 후 started 근거를
유지한다. 실제 파일 효과는 1회이고 자동/승인 없는 Resume의 추가 효과는 0이다.
기존 중첩 Engine·승인·추가 지시·계보·삭제·R3 예산 경계도 함께 검증했다.

### 전체 회귀 — 2026-10-21

| 검사 | 실제 결과 |
|---|---|
| 최초 전체 LLM | 1,766개 중 1,765 pass, 실패 test ID 1개(하위 assertion failure 2), error·Skip 0, 530.276초, exit 1 |
| 최종 전체 LLM | 1,766 pass, failure·error·Skip 0, 519.114초, exit 0 |
| 전체 Hub | 540 pass, failure·error·Skip 0, 681.701초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 560개, exit 0 |
| R3 mutation | 한도 전달 누락 시 두 번째 실제 파일 효과 검출, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 931개, anchor 34개, 오류 0 |

발견/시작 ID는 각각 1,766/540개이며 누락·중복·예상 밖 ID는 0이다.
상태 선언으로 해소한 기존 정적 후보 1개만 제거했고 남은 560개는 승인된 예외가 아니다.
최초 전체 실패는 재시도 증거를 물리적으로 변조한 시험의 공개 경로 오류 기대값 2개였다.
독립적인 Engine 의미 검증과 실제 효과·원본 보존 assertion은 유지하며 공개 경로의 리비전
오류 기대값만 수정했다. 관련 30개가 10.037초에 통과한 뒤 LLM 전체를 다시 실행했다.
제품과 Hub 소스는 Hub 전체 통과 때와 같고 바뀐 Python 파일은 `test_long_running.py` 하나다.
Hub 전체를 반복하지 않았으며 이 차이는 `source-change.json`에 기록했다.

실행 환경과 명령은 H2-a/b와 같다. 최초 전체 실행 전후 Python 654개 manifest는
`bfcc159602e6188bc7e975e6a98d4fb6b6558188f6236c483746a4467668b0b5`로 같았다.
최종 LLM 실행 전후 같은 654개 manifest는
`1b3ad766bfd7114c0417c4ef921ae7bde755792ecbfb8e45b34504f2c41dc774`로 같다.
로그·JSON·전후 manifest는 `/workspace/llm-clean-resume/h2-c/final/`에 보존한다.
전체 검증 성공 후 문서를 먼저 갱신하고 커밋·Push한다.

### H2 잔여 항목

별도 `output-integrity-probe.py`는 3 failure, error·Skip 0, 0.002초, exit 1이다.
출력 JSONL 끝 newline 소실을 빈 출력으로 읽고, 손상 끝줄 뒤에 append하며, 저장된
EngineOutput의 누락된 final을 True로 채우는 경로를 확인했다. 이 실패는 H2-c 전체 suite
성공에 합산하지 않는다. 이 출력 3개 실패는 위 H2-d에서 보강·검증했다. 나머지
Component/도메인의 존재·필드·상태 계약과 H3–H5·P1 잔여 작업·A–F는 아직 완료하지 않았다.

## H2-b 외부 작업 원장의 필수 존재·상태 증거

H2-a 커밋 `ad0eda7f532a7175293207cfc0bc8e85b2981afd`에서 이어서 작업했다.
아래 H2-a 당시의 원장 소실/무효과 증거 소실 2개 실패를 저장소 회귀로 편입했다.

### 구현 경계

- 새 Session 생성 때 `OperationRepository`가 명시적인 빈 `manifest.json`을 만든다.
  기존 Session의 조회·저장·재시작에서 누락된 manifest나 원장 루트를 다시 만들지 않는다.
  manifest는 현재 schema version·Project/Session owner·원장 파일 목록과 리비전을 요구한다.
  선언된 파일 소실·미선언 파일·링크·현재 내용 리비전 불일치를 거부한다.
- `OperationRecord`는 현재 직렬화 필드 전부를 요구한다. started의 종료 시각·증거·결과는
  명시 null이며, completed/not_applied는 종료 시각과 비어 있지 않은 근거가 필수다.
  완료 결과 null도 유효하다. 요청 digest, Tool 계약, 시각·시도 이력·외부 관찰을 검증하며
  증거가 빠진 not_applied를 새 실행 허가로 바꾸지 않는다.
- 원장 파일·manifest·Step receipt는 기존 workspace transaction에서 함께 확정한다.
  Session 백업은 원장 owner의 전체 검증을 호출한다. 보관 판단도 owner의 기록 존재 조회를
  사용하므로 생성 시 빈 manifest가 모든 Session을 불필요하게 고정하지 않는다.
  manifest와 checksum은 삭제 허가나 외부 효과 취소 증거가 아니다.

### 실패 재현과 검증 — 2026-10-21

보강 전 새 원장 회귀 2개는 모두 실패했다. 이후 집중 검증은 56 pass(10.774초),
69 pass(14.686초), 마지막 원장·정적 검사 계약 22 pass(3.392초)였다.
각 묶음의 failure·error·Skip은 0이다. 중복된 집중 시험 수를 독립 전체 시험 수에 더하지 않는다.

`tests.llm.test_operation_integrity`는 실제 파일 효과, 원장/manifest/루트 소실,
필수 필드·상태 증거, 완료 manifest 저장 실패 rollback을 검증한다. 별도 프로세스가 예약
레코드 저장 직후 manifest 저장 전에 exit 77로 종료되면 기존 transaction 복구로 미확정
예약을 되돌린다. 그때 효과는 0이며 재시작 후 첫 정규 실행만 효과 1회다. 완료 기록 저장
실패 후에는 started를 유지하고 같은 작업을 다시 실행하지 않는다.

| 검사 | 실제 결과 |
|---|---|
| 전체 LLM | 1,759 pass, failure·error·Skip 0, 504.286초, exit 0 |
| 전체 Hub | 540 pass, failure·error·Skip 0, 672.254초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 561개, exit 0 |
| R3 mutation | 한도 전달 누락에 따른 두 번째 실제 파일 효과 검출, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 931개, anchor 34개, 오류 0 |

발견/시작 ID는 각각 1,759/540개이고 누락·중복·예상 밖 ID는 0이다.
상태 선언으로 해소한 기존 정적 후보 16개만 baseline에서 제거했다. 남은 561개는 예외 승인이 아니다.
Python·잠긴 의존성·ish 호스트·실제 격리/X11 실행 환경은 H2-a와 같다.
전체 회귀 동안 제품·테스트·예제 Python 653개의 SHA-256이 동일했다. manifest SHA-256은
`01e057f3f7dd2f6a17fe27e7a0a1407575305346320b402c73e73ed8f30c81af`이며,
원시 로그·JSON·전후 manifest는 `/workspace/llm-clean-resume/h2-b/final/`에 있다.
사용자 지침에 따라 전체 검증 성공 후 문서를 먼저 갱신하고 커밋·Push한다.

### H2-b 당시 다음 차단 항목

별도 `checkpoint-loss-probe.py`에서 완료한 Graph 노드의 개별 기록 파일을 제거한 뒤
재시작·Resume하면 파일 효과가 `first`에서 `first/first/second`로 늘었다. 1 failure,
error·Skip 0, 1.241초, exit 1이며 전체 suite 성공에 합산하지 않는다.
이 Checkpoint 기록 소실은 위 H2-c에서 보강·검증했다. 출력 저널과 나머지 Component의
존재/필드/상태 계약, H3–H5, P1 잔여 작업과 A–F는 아직 완료하지 않았다.

## H2-a 도메인 필수 기록·사용량·Refinement

재개 시 실제 원격 `refactor/llm-clean`은 `89f3a771d4f21630c92f31827fe86868064c8554`였다.
같은 체크아웃에서 이어가며 실제 ish 호스트 소스는
`E1der0wL/ish@d833790959a76ac457fdeea3f21a6bea1eecfe12`를 `ish.platform`으로 연결한다.

### 구현 경계

- Project·Session·Run·Step·Message의 현재 직렬화 필드는 저장 Reader에서 필수다.
  생성 기본값으로 누락 필드를 채우지 않는다. nullable 필드는 명시적인 null을 유지한다.
  Project의 policies/parameters/data 컨테이너도 저장 시 필수다.
- Session·Run·Step 목록은 소유 디렉터리의 필수 레코드 소실을 건너뛰지 않는다.
  Run의 completions는 생성 때 빈 배열로 저장하고 이후 누락을 거부한다.
  Completion은 모든 필드, 시각·토큰·예약의 타입과 범위, 중복 ID를 검사한다.
  quota 투영도 같은 Completion Reader를 사용한다. 기존 Run 디렉터리에 run.json이
  없으면 오래된 메모리 핸들로 저장해 되살리지 않는다.
- Memory identity는 생성 때만 발급하고 조회·재선택·백업에서 누락을 거부한다.
  끝 newline이 없는 Conversation journal은 읽기·append를 막고 바이트를 보존한다.
  기존 트랜잭션이 입증하는 append rollback은 유지한다.
- Refinement의 상태는 자체 `lifecycle.py`가 선언한다. 비어 있거나 끊어진 승인 이력,
  마지막 이력과 다른 상태, 적용 상태와 맞지 않는 effect receipt, 누락된 평가 컨테이너,
  fork/binding의 누락된 revision 증거를 거부한다. 대상 변경과 적용 receipt·상태·이력은
  기존 workspace transaction에서 확정하며 proposal을 중간 상태로 저장하지 않는다.

### 실패 재현과 집중 검증

재개 작업 산출물은 `/workspace/llm-clean-resume/`에 보존한다. 새
`tests.llm.test_lifecycle_integrity`는 실제 호출 수·대상 snapshot·원본 바이트를 확인한다.

- 보강 전 Completion 컨테이너·run.json·session.json 소실과 Project policies 소실은
  호출 한도 1에서 두 번째 모델 호출로 이어졌다. 승인 이력을 비운 Refinement도 대상 변경을
  실행했다. Memory identity 재발급, Project 삭제 상태 기본값 복원, JSONL 끝줄 손실도 재현했다.
- 첫 관련 집중 묶음은 154개 중 error 2였다. 하나는 새 회귀에서 소실 뒤 핸들의 동적
  data 조회가 먼저 실패한 시험 구성 오류이며, 다른 하나는 기존 시험의 빈 completions
  배열 인덱싱이다. 손상 전 snapshot 확보와 실제 usage가 관측될 때까지 기다리는 조건으로
  수정했다. 기존 중단·사용량 assertion은 유지한다.
- 수정 후 lifecycle integrity·configuration results·Refinement 37개가 9.830초에 통과했다.
  정적 감사에서 새 후보는 없으며 Refinement 상태 선언 이관으로 해소된 기존 후보 3개만
  baseline에서 제거했다. 남은 후보 577개는 승인된 예외가 아니다.

### 전체 회귀와 소스 일치 — 2026-10-21

| 검사 | 실제 결과 |
|---|---|
| 최초 전체 LLM | 1,752개 중 1,745 pass, 실패한 test ID 7개(하위 assertion failure 8, error 4), Skip 0, 496.818초, exit 1 |
| 최초 전체 Hub | 540 pass, failure·error·Skip 0, 673.990초, exit 0 |
| 실패 관련 집중 재검증 | 100 pass, failure·error·Skip 0, 11.039초, exit 0 |
| 최종 전체 LLM | 1,752 pass, failure·error·Skip 0, 500.472초, exit 0 |
| 최종 전체 Hub | 540 pass, failure·error·Skip 0, 672.442초, exit 0 |
| 정적 감사 | new/stale/S1 0, 미정리 후보 577개, exit 0 |
| R3 mutation | 한도 전달 누락 시 두 번째 실제 파일 효과를 assertion이 탐지, 검사 도구 exit 0 |
| 문서 검사 | 130개 문서, Python fence 178개, 상대 링크 931개, anchor 34개, 오류 0 |

최초 전체 실패는 불명확한 JSONL 끝줄을 무시·수리하던 기대값, 필수 필드 오류 문구/타입,
Refinement의 빈 history fixture였다. 저장 오류에는 누락·미지원 필드 이름을 제공하고,
현재 형식의 fixture와 원본 보존 assertion으로 수정했다. 손상 허용·Skip·실제 효과 검사를
완화하지 않았다. 전체 발견/시작 ID는 각각 1,752/540개이며 누락·중복·예상 밖 ID도 0이다.

Python 3.12.14와 hash 고정된 `tests/llm/dependencies-linux.lock`, 위 ish 호스트 소스를 사용했다.
`.venv-linux312/bin/python -m tests.llm.run_regression --suite llm|hub --output <JSON>`으로
검증하고, 실제 Bubblewrap 검사는 일반 사용자 uid의 escalated execution에서 실행했다.
`TERM=xterm-256color`, `LITELLM_LOCAL_MODEL_COST_MAP=True`, `LITELLM_MODE=PRODUCTION`,
`HUB_TEST_XVFB=/workspace/.setup/x11/usr/bin/Xvfb`,
`HUB_TEST_XCLIP=/workspace/.setup/x11/usr/bin/xclip`을 사용했다. 실제 ish/X11·MCP·프로세스
격리 시험을 포함하며 인증 모델 endpoint 시험은 아니다.

최종 전체 회귀 동안 제품·테스트·예제 Python 652개의 SHA-256이 일치했다.
정렬된 manifest 파일의 SHA-256은
`73593a5ecf288f77e3c3ae6e517e4d421548b8d782d66b6eacd77c6b04c4a6cd`다.
원시 로그·JSON·전후 manifest는 `/workspace/llm-clean-resume/h2-a/final/`에 있다.

### H2-a 당시 추가로 확인한 실패

H2-a 통과는 H2 전체 완료가 아니었다. 전체 회귀의 소스를 고정한 상태에서 별도 실제 효과
시험으로 다음 두 경로를 확인했다. 위 H2-b에서 보강·검증했으며 원래 실패 기록을 보존한다.

1. 완료된 외부 작업 원장 JSON 하나를 제거하고 재시작하면 동일 business key의 Tool이 다시
   실행된다. 기존 Step receipt가 남아 있어도 원장이 재생성되고 파일에 효과 2회가 기록됐다.
2. `not_applied` 원장의 `evidence`를 제거하면 다음 호출을 허용한다. handler 진입 2회,
   새 파일 효과 1회, 손상 원본 덮어쓰기를 관찰했다.

`/workspace/llm-clean-resume/operation-loss-probe.py`의 별도 시험은 2 failure, error·Skip 0,
0.358초, exit 1이다. 기존 전체 suite 성공에 합쳐 통과로 계산하지 않는다.
원장 두 실패는 H2-b의 집중/전체 검증을 통과했다. Checkpoint 개별 기록·출력 저널·
나머지 Component 존재/상태 계약, H3–H5는 별도로 남아 있다.

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

## H1 Session 삭제와 Project 예산

RAG 단계 `6a6e33f`를 Push한 뒤 시작했다. 기존 Run Completion 영수증을 그대로 사용하며
사용량 합계 파일이나 중앙 데이터 관리자를 추가하지 않는다.

수정 전 영구 Session 삭제로 Project 호출량이 1→0이 되어 한도 1에서 실제 테스트 모델의
두 번째 호출이 실행됐다. 추가로 현재 Project 정책을 완화하고 과거 Session을 삭제하면
이미 실행 중인 Run의 고정된 한도 2도 우회해 세 번째 호출이 실행됐다.
Session/Run 정리 계획 역시 과거 사용량을 보호하지 못했다. 두 시험 모두 실제 후속 호출까지
관찰한 뒤 실패했다(2개 failure, error·Skip 0, 0.562초).

### 변경 소유자와 범위

| 구현체 | 변경 |
|---|---|
| `runtime/usage.py:period_entries` | Project 기간의 단일 계산. 호출 예약·사용량 조회·삭제 보호가 공유하며 누락/잘못된 기간 증거를 0으로 복원하지 않음 |
| `runtime/usage.py:project_usage_policies/protects_project_usage` | 현재 Project 정책과 활성 Session이 소유한 Run 정책 사본을 확인. 어떤 살아 있는 Project quota라도 필요한 영수증을 보호 |
| `lifecycle/sessions.py:SessionManager.delete` | workspace 잠금 안에서 최신 소유자·상태와 quota 증거를 검사한 뒤 명시적 영구 삭제를 허용/거부 |
| `lifecycle/projects.py:model_usage/remove_component` | 조회 기간과 Component 삭제 보호에 같은 사용량 owner 계약 사용. 미완료 예약 보호 유지 |
| `history/retention.py:HistoryRetention` | Session/Run 계획 모두 같은 quota 보호. 기존 revision 재검증과 트랜잭션을 사용하고 새 자동 삭제 정책은 추가하지 않음 |

### 검증

| 검사 | 실제 결과 |
|---|---|
| 수정 전 Project·활성 Run 한도 우회 | 2개 모두 failure |
| 최초 집중 회귀 | 68개 중 error 1, 10.239초. 새 시험에서 삭제 후 핸들의 data를 재조회한 시험 오류 |
| 시험 경로 캡처 수정 후 집중 회귀 | 68개 통과, failure·error·Skip 0, 10.245초 |
| 전체 LLM | 1,741개 통과, failure·error·Skip 0, 356.123초 |
| 전체 Hub | 540개 통과, failure·error·Skip 0, 493.166초 |

시험은 실제 테스트 모델 호출 수, 현재 정책 완화와 활성 Run의 정책 사본, soft delete 후
지속 집계, 현재 토큰 한도에서 미계수 과거 호출 보호, 명시 기간 밖의 정상 삭제,
기간 증거 손상 시 원본 보존과 정확한 경계 시각을 확인한다.

```bash
python -m unittest tests.llm.test_usage_deletion tests.llm.test_operations_schema \
  tests.llm.test_long_running tests.llm.test_lifecycle_logging -v
```

전체 회귀·정적 검사 명령과 환경은 앞 단계와 같다. 로그·JSON·소스 hash는
`/workspace/llm-clean-p1/h1-usage/`에 보존한다. 원본 Completion 컨테이너나 Run 파일 자체의
소실을 포함한 전체 Reader 무결성, Session 상위/하위 참조·효과 원장 수명주기,
손상 자료의 안전한 격리/삭제와 Backend 보존 정책의 Application 이관은 H2–H5에 남아 있다.
최종 전체 회귀 동안 650개 Python 파일의 hash가 일치했다. 정적 검사는 new/stale/S1 0,
잔여 후보 580개이며 새 면제는 없다. R3 한도 누락 mutation도 두 번째 실제 파일 효과로
탐지해 도구 exit 0이었다. 실제 ish 통합과 Xvfb/xclip 9개 시험을 포함해 Skip은 없다.

## 남은 게이트

H1의 승인·RAG 활성 포인터·Session 삭제의 예산 보호는 각 단계의 집중·전체 회귀 검증을 완료했다.
H2–H5의 owner별 필수 존재·손상 처리·안전한 격리/삭제·Application 정리 정책·참조 보호도
남아 있다. 이 안전성 보강 전 P1의 나머지 계약군과 A–F를 완료로 표시하지 않는다.
실제 인증 모델·커널 short-write·전원 손실·네트워크 파일시스템은 미검증이다.

다음 H2는 `core/models.py`, `services/lifecycle/projects.py`,
`services/runtime/runs.py`, `components/memory/component.py`,
`services/history/conversation.py`와 각 Component Reader의 존재·필수 상태 계약부터 다룬다.
Project 삭제 상태·Completion 컨테이너 누락, Memory identity 소실, 불명확한 JSONL tail의
기본값 복원·자동 절단이 남은 주요 위험이다. 변경 시 생성 기본값과 저장 Reader를 분리하고
실제 원본 손상·재시작 시험을 각각 먼저 만든다. H3–H5에서는 소유자별 격리/삭제 증명,
상위 삭제·참조·백업과 Application 정책 이관을 별도로 검증한다.
