# 마크다운 RAG / GraphRAG 예제

현재 실행 코드는 `examples/llm/rag_components.py`다. RAGComponent 공개 API로 문서를
등록하고 일반 검색·관계 검색을 수행한다. `--ask`를 주면 같은 프로젝트의 내장 검색 Tool로
LoopEngine을 실행한다. 예제에 별도 Markdown 분할기·BM25·Chroma·Kuzu 구현은 없다.

## 실행

선택 의존성 chromadb/kuzu/rank-bm25와 실행할 모델의 인증이 필요하다. 이 예제는
GEMINI_API_KEY를 환경변수에서 읽고 모델 생성자에만 전달한다. 키를 소스에 넣지 않는다.
저장소 루트에서 현재 계정이 사용할 수 있는 모델명을 지정한다.

```sh
.venv-linux312/bin/python -m examples.llm.rag_components --model <completion-model> --embedding-model <embedding-model> --workspace tests/llm/reports/runs/research-demo/component-workspace
```

이 예제는 Gemini 문서/질의 임베딩의 session_type 옵션을 사용한다. 다른 공급자를 사용하면
예제의 해당 옵션과 인증 환경변수를 공급자에 맞게 조정한다.

| 옵션 | 의미 |
| --- | --- |
| --markdown | 입력 Markdown 파일. 기본값은 가상 운영 문서 fixtures/rag_operations.md |
| --query | 직접 문서 검색할 질의 |
| --seed | 직접 관계 검색할 정확한 엔티티 이름 |
| --ask | 내장 검색 Tool이 제공된 LoopEngine에 전달할 사용자 요청 |
| --exercise-crud | 조회 이후 문서를 수정·삭제하고 잔존 검색 결과가 없는지 검사 |
| --workspace | Project/Session/Run/Step과 컴포넌트 데이터를 저장할 작업 공간 |

예를 들어 위 명령에 `--ask "설명서를 검색하고 백업 정책을 출처와 함께 설명해 줘"`를
추가한다. 모델이 필요에 따라 검색을 선택하며 예제는 Tool 사용이나 특정 답변을 강제하지 않는다.
출력의 Run/Step 목록으로 실제 검색 호출을 확인할 수 있다. 매 실행 새 Project를 만들며
기존 Project/문서를 덮어쓰지 않는다. 출력에는 project_id/document_id, 직접 검색 결과와
선택적인 Run ID·상태·Step 목록·답변이 포함된다.

rag 하나에 원문·벡터·관계가 함께 저장된다. asearch와 rag_search Tool은
검색 문서와 관련 관계·출처를 함께 반환한다. --seed는 선택적인 직접 관계 조회 예제다.
설정과 저장 구조는 [RAG 공개 API](rag-components.md)를 참고한다.

## 로컬 검증

```sh
.venv-linux312/bin/python -m unittest tests.llm.test_markdown_rag tests.llm.test_search_tools -v
```

테스트는 실제 Chroma/Kuzu와 고정 모델 응답으로 예제의 문서 등록·검색·Loop Tool 호출·
Step 기록·수정·삭제·모델 없이 재열기를 검사한다. 모델 API를 호출하지 않으므로 실모델
답변 품질 검증과 구분한다. 선택 의존성이 없으면 DB가 필요한 테스트는 skip한다.

## 이전 명령과 데이터

이전 `examples.llm.markdown_rag` 호환 진입점은 제거했다. `python -m examples.llm.rag_components`를 사용한다.
`--model`은 필수이며 `--output` 대신 `--workspace`를 사용한다. 과거 `--verify`는 지원하지
않는다. 과거 records/operations.json + 독립 chroma/graph.kuzu 구조를 현재 컴포넌트가
자동으로 읽거나 변환하지 않는다. 원본 Markdown을 현재 aadd_document API로 새 Project에
등록한다. 과거 결과 파일과 로그는 기록으로 보존하고 변경하지 않았다.

## 과거 실모델 검증 기록 (현재 예제의 결과가 아님)

2026-09-25 Python 3.12.14에서 이전 독립 예제로 Gemini embedding-001과
gemini-3.1-flash-lite를 호출했다. 당시 8개 문단/3072차원 벡터, 8개 엔티티/6개 관계,
3개 Run/7개 Step을 확인했다. 답변은 백업 주기 15분·보존 기간 30일, 책임자 한지민·리전
서울이었고 인용, 절/문서 확장 및 별도 프로세스 재조회를 확인했다.

당시 결과는 `tests/llm/reports/history/workspaces/research-demo/markdown-rag-verified/result.json`, 로그는
`tests/llm/reports/history/markdown-rag-verified-python312.txt`다. 당시 전체 테스트는 345개 통과했다.
이 결과를 현재 공개 API 예제의 실모델 검증 결과로 해석하면 안 된다.
