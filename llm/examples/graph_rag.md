# 사내 GraphEngine + RAG 통합 테스트

`graph_rag.py`는 실제 LiteLLM 모델과 Chroma/BM25/Kuzu를 함께 실행한다.
API 키는 환경 변수가 아닌 JSON을 역직렬화한 `ProjectConfig`에서 읽는다.
각 실행은 별도 테스트 Project를 만들며 기본 Project와 원본 Markdown 파일은 변경하지 않는다.
`llm/` 안에 있으므로 이 폴더만 ish에 배포해도 사용할 수 있다.

## 설정

`graph_rag.config.example.json`을 로컬의 `~/graph-rag-config.json`으로 복사한 뒤
다음 세 곳의 model, api_base, api_key를 수정한다. 실제 값은 생성된 Project에도 저장된다.

| ProjectConfig 위치 | 용도 |
| --- | --- |
| `completion` | Graph 노드의 Agent가 사용하는 대화 모델. 스트리밍과 Tool calling 필요 |
| `component_configurations.rag.embedding_params` | 문서·질의 벡터를 생성하는 임베딩 모델 |
| `component_configurations.rag.extraction_params` | 문서에서 엔티티·관계·인용을 추출하는 모델. JSON 응답 지원 필요 |

OpenAI 호환 서버는 `openai/모델명`을 사용한다. 다른 provider는 해당 LiteLLM 모델명을
사용하고 필요 없는 api_base를 삭제한다. 임베딩과 대화 모델은 서로 다른 서버·키를 써도 된다.
Gemini 등 공급자별 문서/질의 옵션이 필요하면 RAG 설정에 `document_kwargs`, `query_kwargs`를
추가할 수 있다. 예를 들어 Gemini 임베딩의 session_type은 각각 RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY다.

`engines.loop.max_iterations`는 Agent 한 번의 모델 호출 반복 상한이다.
`--max-attempts`는 답변 검증·수정 회차 상한이며 두 제한은 서로 다른 범위를 가진다.
실행 시 지정한 모델의 API 비용이 발생하며 등록/수정 시 임베딩과 관계 추출을 다시 호출한다.

## ish에서 실행

최신 llm 폴더를 `config.PLUGIN_SCRIPT_DIR / "llm"`에 배치한다.
다음 코드를 실제 `.ishrc.py`에 추가하고 ish를 다시 시작한다.

```python
from functools import partial

llm_plugin = plugin.get("llm")
if llm_plugin is not None:
    from llm.examples.graph_rag import main as graph_rag_test

    prompt.set_tool(
        "graph-rag-test",
        function=partial(
            graph_rag_test,
            "--config", "~/graph-rag-config.json",
            "--workspace", "~/.ish/graph-rag-test",
        ),
    )
```

```sh
# 내장 예제 문서: 문서 검색뿐 아니라 관계 검색 결과도 필수로 확인한다.
graph-rag-test

# 실제 사내 Markdown: 질의에는 문서에서 쓰는 고유명사/키워드를 포함한다.
graph-rag-test --markdown /home/user/manual.md --query "제품명 설정 파일의 필수 항목과 적용 순서를 설명해줘"

# 문서에 명확한 관계가 있어 관계 검색도 반드시 성공해야 하는 경우
graph-rag-test --markdown /home/user/manual.md --query "제품명과 저장소의 관계" --require-relations
```

별도 Python 환경에서 실행할 때는 llm 폴더의 부모를 import 경로에 두고 다음을 실행한다.
ish가 설치한 의존성을 외부 Python이 자동으로 공유하지는 않는다.

```sh
python -m llm.examples.graph_rag --config ~/graph-rag-config.json
```

## 수행하는 검증

1. RAG·Agent·Workflow 컴포넌트를 선택한 파일 저장 Project 생성.
2. Markdown 등록: 분할, 임베딩, 관계 추출, 실제 Chroma/Kuzu 색인.
3. 짧은 별도 문서로 생성·수정·revision 조회·삭제 검증. 본 문서는 유지.
4. BM25, vector, hybrid 검색과 section 확장 결과 확인. 삭제 문서의 출처가 남지 않는지 검사.
5. Agent와 Workflow JSON을 저장하고 공개 API로 다시 읽어 동일성 확인.
6. GraphEngine 실행: `Agent → JSON/원문 인용 검증 → 분기 → 오류 피드백 → 재실행`.
   검증에 성공하면 답변 출력 후 종료하고, 상한까지 실패하면 Run도 실패한다.
7. Agent마다 rag_search를 한 번 이상 성공적으로 실행하도록 정책을 설정.
   실행 후 검색 Tool 결과, Run·Step 완료 상태와 저장된 Assistant 응답 확인.
8. 백엔드를 종료하고 새로 열어 Run·Step·Workflow·검색 색인을 조회.

문서 CRUD는 Component API에서 수행하므로 별도의 Run을 만들지 않는다.
Graph 실행 중 검색과 모델 호출은 하나의 소유 Run에 Step으로 기록된다.
엔진이나 예제 검증 노드가 도메인 persistence 파일을 직접 수정하지 않는다.

답변 검증은 JSON 형식, 답변 존재, 문서 ID, 인용문의 원문 일치를 확인한다.
**답변의 의미적 정확성이나 질문에 대한 충분성까지 자동 판정하지 않는다.**
실제 모델이 첫 시도에 유효한 답변을 내면 재수정 분기는 실행되지 않으며,
보고서의 output.attempts/validation으로 실제 경로를 확인한다.
관계가 없는 사용자 문서는 정상일 수 있으므로 `--require-relations`를 생략하면 빈 관계도 허용한다.
이때 `relations_observed=false`를 성공적인 관계 추출 품질 검증으로 해석하지 않는다.

## 결과와 로그

- 종료 코드: 성공 0, 검증/실행 실패 1, Ctrl+C 130.
- `workspace/reports/graph-rag-<id>.json`: 단계별 소요 시간, 검색 원문·관계·출처,
  검증 결과, Project/Session/Run ID, Step 목록, 답변·인용·검증 회차, 실패 단계.
- `workspace/projects/<project-id>/`: 실제 도메인 기록과 RAG 데이터.
- `workspace/logs/providers-<pid>.log`: LiteLLM 등의 WARNING 이상 진단.

설정 파일 자체가 잘못되면 Project 생성 전에 종료한다. 작업 도중 실패하면 보고서를 남기고
만들어진 테스트 자료를 보존한다. 중단/실패를 자동 재개하거나 기존 Project를 덮어쓰지 않는다.
total_tokens는 Graph Run에서 보고된 사용량이며, Run 밖에서 수행한 임베딩·추출 비용까지
합친 테스트 전체 청구량이 아니다. usage를 제공하지 않는 모델에서는 null일 수 있다.

worker 초기화에서 LiteLLM의 로컬 비용표를 선택하므로 GitHub 비용표 갱신을 시도하지 않는다.
이 설정은 모델 API 접속을 끄는 옵션이 아니다. 임의 print나 네이티브 라이브러리의 출력까지
모두 차단하는 TUI 출력 격리 기능은 이 예제의 범위에 포함하지 않는다.

자동 회귀 검사는 실제 Chroma/Kuzu/BM25와 고정 모델 응답으로 재수정 성공, 상한 실패,
저장 후 재조회, worker import를 검증한다. CLI에는 모의 모델로 대체하는 fallback이 없으며
사내에서 실행하면 설정한 실제 모델을 호출한다. 장시간/대용량 성능 시험은 별도로 수행한다.
