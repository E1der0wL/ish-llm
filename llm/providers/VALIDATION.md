# Provider/RAG 안정화 검증 기록

## 추가 검증: 165줄 문서의 rerank와 Graph 검색

`examples/graph_rag.py`는 이제 `rag.rerank_params.model`을 필수로 확인한다.
일반 BM25/vector/hybrid 검색 다음에 동일 후보로 rerank 검색을 실행하고, 유한한 점수와
원본 후보 내용·출처 보존을 검사한다. Graph Agent의 rag_search 결과에도 rerank 점수가
있어야 통과한다. 설정이 없거나 rerank 호출이 실패하면 성공으로 보고하지 않는다.

`examples/data/graph_rag_165.md`에 165줄 공개 문서를 포함했다.
설정 예제의 `cohere/`와 `/v1/rerank`는 Cohere 호환 rerank 서버용이며 chat/embedding의
OpenAI 호환 여부와는 별개다. 실제 서버의 rerank 프로토콜에 맞춰 변경해야 한다.

Linux Python 3.12.14 / LiteLLM 1.103.1 / OpenAI 2.54.0에서 `provider_probe`를 실행했다.
실제 SDK와 로컬 HTTP 서버를 거쳐 165줄 문서(23 chunks)를 등록했고 다음을 확인했다.

- 직접 hybrid 검색 후보 3개가 서버의 반환 index에 맞춰 역순으로 재정렬됨.
- 직접 검색 1회, Graph 검증·재수정 Agent 2회에서 실제 rerank HTTP 요청 총 3회.
- 별도 프로세스의 실제 graph_rag.py CLI에서도 23개 확인 항목 통과, rerank HTTP 요청 3회.
- SDK 준비 후 등록 2.139초, rerank 검색 0.184초, Graph 2.113초.
- 최초 SDK 초기화를 포함한 CLI 등록 6.141초, rerank 검색 0.167초, Graph 2.167초.
- reranker 누락 시 생성 전 실패, 호출 실패 시 실패 보고서, index/후보 매핑 회귀 검사 통과.

이 결과는 고정 HTTP 응답을 이용한 연동 검사다. 실제 사내 모델의 관련성 판단 품질과
모델 추론 성능을 검증한 결과는 아니다. API 키·사내 문서는 사용하지 않았다.
이번 실행 로그는 Git에서 제외되는 `llm/tests/reports/rerank-http-probe.json`에 있다.

첫 전체 검사에서 공유 validate_config를 쓰는 설정 편집 예제 12건이 reranker 필수 검사로
실패했다. `require_rerank`를 graph_rag 실행에서만 명시하도록 수정했다. 기존 설정 편집
예제의 요구 모델은 유지하며, 테스트를 제외하거나 기대 오류를 약화하지 않았다.

최종 Linux 검사: 관련 검사 45개 통과(36.612초), 전체 루트 테스트 **852개 통과**
(253.841초). 관련 검사에 포함된 신규 Provider/RAG 23개와 합쳐 고유 테스트 총 **875개**이며
최종 실패·skip은 0개다. 최종 Linux 소스 사본과 현재 Python/JSON 255개 파일의 SHA-256이
일치한다. `rerank-source-verification.json`, `focused.txt`, `suite.txt`에 기록했다.

---

2026-09-30. 구현 범위는 `llm/`이며, 사용자 승인 후 루트 `tests/`의 변경된 계약 검사 4개 파일도 수정했다.
기존 `ish/`/ish 본체, 영속 도메인 소유 관계, 트랜잭션·세대 공개 방식은 변경하지 않았다.

## 설계와 설정

초기화·진단은 `providers/runtime.py`, 비스트리밍 안전 재시도는 `providers/requests.py`,
응답 정규화·벡터 검증은 `providers/embeddings.py`가 담당한다. RAG는 기존 ComponentData/
RAGJobs를 통해 자료와 체크포인트를 저장한다. Engine이 영속 파일을 직접 쓰는 경로는 추가하지 않았다.

새 설정의 타입과 기본값 전체는 [RAG 설정표](../components/rag/README.md)에 있다.
라이브러리 기본은 provider 2 attempts/120초, JSON strict, graph required, batch 128,
문자 제한 없음, 자동 분할 끔, 메모리 vector cache 16MiB다. 운영자가 저사양 프로필을
선택하도록 예제 JSON에는 batch 16/16,000자, split depth 2, JSON auto를 넣었다.

LiteLLM 1.103.1에서는 SDK request의 retry 두 인자와 전역 DEFAULT_MAX_RETRIES를 모두 0으로
만든다. embedding에는 caching=False와 no-cache/no-store를 함께 전달한다. 호스트의 기존
cache 객체는 소유권을 유지한다. 정상 모델 설정이 UI effective configuration에도 반영된다.

정규화는 **cache 사용 근거와 실제 cached 위치 정보**가 확인된 경우에만 배열 순서를 유지해
index를 복구한다. cache_hit만 보고 임의로 순서를 추측하지 않는다. 정상 호출은 SDK cache를
끄므로 중복 index를 교정하지 않고 거부한다. `embedding_index_normalized` 진단과 후속
엄격 검증은 정규화 함수 자체의 계약이다. 알려진 SDK 구현이 바뀌어도 일반 오류를 숨기지 않는다.

JSON auto는 빈 공급자 응답/파싱 실패에서만 response_format을 제거하고 다시 호출한다.
인증/400/모델 설정 문제에는 fallback하지 않는다. JSON/인용/참조 실패는 semantic repair만
사용한다. 기본 few-shot, 온도 0, literal evidence 검증을 유지하며 Python이 ID/타입/중복을
정규화한다. transport retry·JSON fallback·repair 전체가 추출 호출 하나의 deadline을 공유한다.

## 검사 범위

신규 `llm/tests/test_provider_runtime.py`, `test_rag_resilience.py`의 23개 테스트에서
여러 입력 조합을 반복 검사한다.

- import 전 PRODUCTION/로컬 비용표, dotenv 차단, 실제 최종 SDK kwargs/global retry
- transient 분류, 인증/400/의미 오류 비재시도, bounded retry, deadline, backoff 중 취소
- 정상/역순 index, 중복/누락/개수 오류, NaN/Inf/0/차원 오류, 근거 있는 cache 정규화
- cache off 및 근거 부족일 때 정규화 거부, 정규화 후 strict validation, diagnostic
- partial cache/repeated input의 original position mapping, 변경 없는 vector 재사용/설정 변경 무효화
- 문자 제한, timeout 분할, 크기 1 종료, 무결성 오류 비분할, 하위 checkpoint 재사용
- JSON strict/off/auto, 잘못된 JSON repair, 대명사 해소와 원문 인용 분리
- graph required/best_effort/disabled, 부분 관계 보존, extractor 없는 disabled 설정
- 영속 Job 중간 실패 후 재개, 취소 시 기존 generation 보존
- 사용량 예약·한도 준수, 재시도에 의한 quota 우회 방지
- TUI 출력 없음, 로그 비밀값/본문 제외, 백엔드 로그 분리, 로그 디스크 장애 카운터
- SDK import 중 다른 진단 문맥이 막히지 않는지, reranker capability schema

기존 테스트의 변경 사항:

- `tests/test_rag_models.py`: SDK cache/retry 차단 인자의 실제 전달을 단정한다.
- `tests/test_graph_rag_example.py`: 보고서 파일에 원문·모델 본문·예외 원문이 없는지 확인한다.
- `tests/test_unified_rag.py`: 검색 응답의 graph completeness 필드를 검증한다.
- `tests/test_operations_schema.py`: 단일 호출 예약 테스트에 max_attempts=1을 명시하고
  공급자 오류의 공통 타입을 확인한다. 기본 재시도의 quota 검사는 신규 테스트에서 따로 수행한다.

## 실제 SDK HTTP 통합 검사

Linux Python **3.12.14**, LiteLLM **1.103.1**, OpenAI **2.54.0**.
네이티브 Linux 파일시스템의 소스 사본과 가상환경에서 실행했다.
`python -m llm.tests.provider_probe --output /tmp/provider-probe.json`은 고정 응답을 주는
로컬 OpenAI-compatible HTTP 서버를 사용한다. API 키나 사내 문서는 사용하지 않는다.

최종 HTTP 검사 결과:

- sync LiteLLM / async LiteLLM / direct AsyncOpenAI 임베딩 결과 동일
- 호스트 SDK cache 객체 존재 시 읽기 0회·쓰기 0회
- 503 실패의 실제 HTTP 요청 정확히 2회, 종료 코드 provider_unavailable
- JSON mode HTTP null 응답은 JSON mode 2회 후 plain JSON 1회로 복구
- graph_rag.py 문서 CRUD·BM25/vector/hybrid·Graph Agent 검색 Tool·검증/재수정·Step/Run·재열기 통과
- 별도 프로세스에서 실제 `python -m llm.examples.graph_rag ... --require-relations` CLI도
  종료 코드 0, 20개 확인 항목 통과. LiteLLM WARNING/feedback/dotenv 문구가 콘솔에 없음을 확인.

165줄 공개 문서(23 chunks, 10,573 chunk chars) 측정:

| 항목 | 결과 |
|---|---:|
| 문서 등록 전체 | 3.944초 |
| embedding 6 batches | 0.788초 |
| extraction 6 batches | 0.363초 |
| Graph 실행 | 3.560초 |

이 수치는 **로컬 고정 응답 서버의 라이브러리/저장 경로 측정**이다. 실제 사내 모델의
embedding/추출 속도나 추론 정확도를 예측하지 않는다. 사내에서는 동일 예제로 다시 측정해야 한다.
위 표는 SDK 준비 후 실행이다. 별도 CLI 프로세스의 최초 실행에서는 SDK 초기화가 첫 임베딩에
포함되어 문서 등록 8.940초, embedding 6.342초, extraction 1.039초, Graph 2.699초였다.
전체 회귀 검사와 함께 실행한 개발 호스트의 관찰값이며 통계적 성능 보증이 아니다.
보고서와 콘솔 로그는 로컬 `llm/tests/reports/`에 있으며 Git에서 제외된다.

## 전체 테스트와 소스 검증

최종 테스트는 `llm/tests/run_linux.py --python <Python 3.12.14 경로> --full`로 실행한다.
각 실행은 소스 해시 manifest를 남기며 신규 테스트와 루트 전체 테스트를 분리해 기록한다.
최종 결과는 **신규 23개 통과(12.939초), 기존 전체 850개 통과(310.710초), 실패·skip 0개**다.
검증 사본과 현재 Python/설정·테스트 파일 208개의 SHA-256이 일치했다.
검사 결과는 `llm/tests/reports/focused.txt`, `suite.txt`, `source-verification.json`을 참고한다.
실행 도중 발견한 이전 계약 단정 6건은 새 요구사항에 맞게 보완했고, extractor 없는
disabled 설정 전환에서 발견한 구현 오류는 수정했다. 실패를 제외하거나 skip하지 않았다.

## 남는 한계

- 실제 사내 endpoint/저사양 CPU에서의 대용량·장시간 검증과 모델의 관계 추출 품질은 별도다.
- 임의 호스트 함수가 취소를 무시하면 강제 종료할 수 없다. 동기 LiteLLM 스트림은 소비자를
  취소한 뒤에도 provider timeout/실제 종료까지 worker가 호출 슬롯을 유지한다.
- 비동기 deadline 만료 후 사용량/저장 취소 정리는 내구성을 위해 drain한다. 외부 SDK/OS가
  취소나 I/O 종료에 협조하지 않는 상황까지 절대적인 wall-clock 강제 종료를 보장하지 않는다.
- 모델 서버가 같은 모델명·설정으로 벡터 의미를 바꾸는 경우 자동 감지할 수 없다.
  모델 revision/embedding_id를 변경하고 새 corpus로 등록해야 한다.
- graph_complete=true는 모든 추출 batch가 형식·출처 검증을 통과했다는 뜻이다.
  모델이 원문의 모든 사실을 발견했다는 품질 보증은 아니다.
- SDK의 임의 로그는 민감한 원문 대신 발생 위치/등급으로 기록한다. 별도 원본 debug 로그가
  필요하면 호스트가 데이터 노출 정책을 정해야 한다. 디스크 자체가 기록 불가능하면
  `logging_status().failed_writes`로 손실을 확인한다.
- 이미 다른 코드가 LiteLLM을 import했다면 그 시점의 dotenv 부작용은 되돌릴 수 없다.

## 변경 파일

```text
llm/llm.py
llm/README.md
llm/providers/runtime.py
llm/providers/requests.py
llm/providers/embeddings.py
llm/providers/openai.py
llm/providers/litellm.py
llm/providers/retry.py
llm/providers/README.md
llm/providers/VALIDATION.md
llm/components/rag/_client.py
llm/components/rag/component.py
llm/components/rag/data.py
llm/components/rag/extraction.py
llm/components/rag/ingestion.py
llm/components/rag/jobs.py
llm/components/rag/prompts.py
llm/components/rag/tools.py
llm/components/rag/README.md
llm/services/lifecycle/components.py
llm/services/runtime/runs.py
llm/services/runtime/usage.py
llm/examples/graph_rag.py
llm/examples/graph_rag.md
llm/examples/graph_rag.config.example.json
llm/examples/recovery_probe.py
llm/tests/__init__.py
llm/tests/.gitignore
llm/tests/run_linux.py
llm/tests/setup_linux.py
llm/tests/provider_probe.py
llm/tests/test_provider_runtime.py
llm/tests/test_rag_resilience.py
tests/test_rag_models.py
tests/test_graph_rag_example.py
tests/test_unified_rag.py
tests/test_operations_schema.py
```
