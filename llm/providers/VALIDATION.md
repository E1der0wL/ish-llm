# Provider/RAG 설계 단순화 검증

2026-10-01. 구현 변경은 `llm/`에 한정했다. 사용자 승인에 따라 루트의
`tests/test_rag_models.py`, `tests/test_loop.py`에서는 retry=0 자동 삽입 기대값만
새 계약으로 바꿨다. Project → Session → Run → Step과 atomic RAG generation은 유지한다.

## 변경 계약

- ProviderRuntime은 지연 import, PRODUCTION/로컬 비용표, dotenv 차단, 로그 격리와 진단을 담당한다.
  SDK의 retry 환경변수·전역값을 변경하거나 호스트의 후속 변경을 거부하지 않는다.
- ModelClient와 streaming completion은 `num_retries`/`max_retries`를 그대로 전달한다.
  인자가 없으면 추가하지 않는다. Loop/BaseEngine/Memory의 자동 0 기본값도 제거했다.
- SDK retry가 활성화되면 llm invoke는 1회다. 설정이 겹쳐도 오류로 거부하지 않는다.
  SDK retry가 없는 경로는 기존 max_attempts/backoff/deadline/취소 계약을 유지한다.
- 요청 값뿐 아니라 주입 client, SDK 기본 retry 값과 retry_policy도 고려한다.
  SDK가 명시적 0을 기본값으로 되돌릴 수 있어 활성/불명확한 기본값에서는 보수적으로
  외부 반복을 추가하지 않는다. `provider_retry_delegated` 진단으로 유효 시도 횟수를 알린다.
- 기본 임베딩 호출은 모두 LiteLLM aembedding을 사용한다. SDK 선택 분기와 모델 접두사 변환은 없다.
- RAG EmbeddingModel에서만 caching=False, no-cache/no-store를 적용한다.
  일반 ModelClient의 cache kwargs와 호스트의 기존 SDK cache 객체는 그대로 둔다.
- reranker 없는 Tool은 schema const:false와 handler의 최종 False 덮어쓰기를 함께 적용한다.
  공개 Python 검색 API의 명시적 rerank=True 설정 오류는 유지한다.

## 저장·캐시 호환성

벡터 fingerprint에서 제거된 실행 경로 선택 정보 때문에 이전 fingerprint는 일치하지 않는다.
다음 문서 update에서 벡터를 재계산하며, 기존 corpus를 삭제하거나 이전 벡터를 잘못 재사용하지 않는다.
저장 형식 버전은 변경하지 않았다. 구성 fingerprint가 바뀐 미완료 Job은 새 Job으로 등록한다.
기존 설정에 제거된 provider 키가 있다면 삭제해야 하며 자동 설정 마이그레이션은 하지 않는다.

VectorCache의 크기 제한·원래 입력 위치 복원, 변경 없는 청크 재사용, 배치 분할,
Job/checkpoint 재개, JSON strict/auto/off, graph required/best_effort/disabled는 유지한다.
index 정규화는 cache hit와 실제 cached 위치·개수·패턴·벡터 검증 근거가 있을 때만 허용한다.
정규화 이후 strict validation을 다시 수행하며, 근거 없는 중복 index [0,0]은 계속 오류다.

## 회귀 검사

- 명시적 num_retries=2, max_retries=3의 embedding/extraction/rerank/completion 최종 전달값.
- 생략된 retry 인자가 SDK에 추가되지 않는지와 SDK 전역/환경변수의 보존.
- SDK retry 활성, SDK 기본값 활성, client retry, retry_policy에서 llm 시도 1회.
- retry 비활성 시 bounded attempts, timeout/deadline, 호출/대기 중 취소, 오류 분류.
- 스트리밍 retry 위임과 첫 delta 이후 외부 자동 재시도 금지.
- reranker 없는 handler에 직접 True를 전달해도 False, reranker가 있으면 True 허용.
- RAG cache 차단과 일반 ModelClient의 명시적 cache 설정 보존.
- UI effective configuration의 retry 보존, provider schema/default의 키 목록.
- 구 fingerprint의 벡터 비재사용과 기존 문서 무변경.
- 기존 index 엄격 검증/조건부 정규화, 부분 캐시, durable Job 재개, atomic publish, 사용량 한도.

Linux Python 3.12.14, LiteLLM 1.103.1, OpenAI 2.54.0에서 검사한다.
집중 검사는 `python -m unittest llm.tests.test_provider_runtime llm.tests.test_rag_resilience -v`,
전체 검사는 `python -m unittest discover -s tests -v`다. 이 워크스테이션에서는
`llm/tests/run_linux.py --python <Linux Python 3.12.14 경로> --full`로 Linux 파일시스템의
소스 사본을 만들고 SHA-256 manifest와 검사 로그를 기록한다.

최종 결과: **Provider/RAG 29개 통과(9.919초), 전체 루트 852개 통과(248.444초)**.
합계 **881개**, 실패·skip 0개다. 최종 사본은
`/home/user/.cache/ish-provider-x12ermpn`이며 현재 Python/JSON 254개 파일의 SHA-256이
일치한다. `focused.txt`, `suite.txt`, `provider-simplified-source-verification.json`에 기록했다.
추가로 수정 초기에 RAG 모델/Loop까지 묶은 집중 검사 63개도 통과했다.

실제 사용한 명령:

```bash
/home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python \
  /mnt/d/WorkSpace/ish/llm/tests/run_linux.py --source /mnt/d/WorkSpace/ish \
  --python /home/user/.cache/ish-provider-sdk-fbmj8dmh/.venv-linux312/bin/python --full
```

## 실제 SDK HTTP 검사

`python -m llm.tests.provider_probe --output /tmp/provider-probe.json`은 외부 인증 없이
고정 응답을 반환하는 로컬 HTTP 서버와 실제 SDK/Chroma/BM25/Kuzu/GraphEngine을 사용한다.

- sync/async LiteLLM 임베딩 결과 일치, SDK cache read/write 각각 0회.
- SDK max_retries=1 + llm max_attempts=4에서도 503 HTTP 요청은 2회.
- JSON-mode null 응답 이후 plain JSON fallback: 호출 2회로 성공.
- 165줄 공개 문서 등록, 검색, rerank 후보 역순 매핑, Graph 답변 검증·재수정과 재열기.
- 직접 검색 및 Graph Agent 2회에서 rerank HTTP 요청 3회.
- 별도 프로세스의 graph_rag CLI도 23개 확인 항목 통과, rerank HTTP 요청 3회.

이 검사는 호출·저장 계약을 검증한다. 실제 사내 모델의 추론 정확도나 처리 속도를 보장하지 않는다.
실행 결과는 Git에서 제외되는 `llm/tests/reports/provider-simplified-http.json`에 있다.

## 한계

SDK 내부 HTTP 재시도는 한 SDK 호출 안에서 발생하므로 별도 사용량 영수증으로 관찰하지 못한다.
llm은 각 외부 invoke를 계속 예약·집계한다. SDK 자체의 다층 재시도와 임의 주입 함수 내부의
반복까지 재작성하지 않으며, 불명확한 SDK 정책에는 llm의 외부 반복을 추가하지 않는다.
동기 SDK worker나 취소를 무시하는 사용자 함수의 강제 종료는 보장하지 않는다.
SDK 초기화 전에 다른 코드가 dotenv를 로드했다면 해당 부작용을 되돌릴 수 없다.
모델이 같은 이름으로 벡터 의미를 바꾼 경우 자동 감지할 수 없어 model revision/embedding_id 관리가 필요하다.
