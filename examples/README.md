# 플러그인별 예제

예제는 플러그인에 포함하지 않습니다. 개발 checkout의 루트에서 Linux Python 3.12.14로 실행합니다.

```sh
.venv-linux312/bin/python -m examples.llm.graph_rag --help
.venv-linux312/bin/python -m examples.llm.configuration_workflow --help
.venv-linux312/bin/python -m examples.llm.custom_engine
```

ish에서 예제 함수를 임시 연결할 때는 개발 checkout의 루트를 Python import 경로에 별도로
추가해야 합니다. 제품 배포의 `plugin.get("llm")`는 예제에 의존하지 않습니다.

- [Graph/RAG 통합 검사](llm/graph_rag.md)
- [ish 개발용 등록 설정](llm/ishrc.example.py)
- [설정 파일 검토·승인 Workflow](llm/configuration_workflow.md)
- [성능 검사](llm/domain_performance.md), [장애 복구 검사](llm/recovery_probe.md)

샘플 JSON에는 인증값을 저장하지 마세요. 개인 실행 설정과 작업 결과는 별도 로컬 경로에 보관합니다.
