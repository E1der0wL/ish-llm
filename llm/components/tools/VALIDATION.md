# Python Tool 전환 및 저장 형식 감사

## 실행 경계

Project → Session → Run → Step 소유 관계는 그대로다. Tool 패키지 로딩은 Component에,
실행·승인·재시도·원장은 기존 ToolExecutor에 남긴다. Run은 의존성을 설치하지 않는다.
Tool CRUD에는 이전 JSON Tool reader, migration, catalog 결합 경로를 두지 않는다.
configuration_workflow 예제의 Host-owned 적용 함수는 별도 ReviewTools Component가
tools capability를 제공한다. 소스 파일에 Host의 실행 객체를 저장하지 않는다.

## 검증 범위

`llm.tests.test_tool_packages`는 다음을 검증한다.

- 소스/requirements CRUD, 멀티 파일 쓰기 실패 rollback, 경로와 symlink 거부
- import 없는 clone/backup/restore, 의존성 없는 prepare의 함수 검증
- decorator identity/불변 계약, 지원 annotation, Field 제약, required/default
- 프로젝트별 동일 이름 격리, enabled 선택, 다음 Run의 소스 변경 반영
- 현재 ish Config의 ISH_HOME/plugin/lib 사용, 명시적 prepare만 설치
- 설치 버전 재사용/충돌/중복 metadata, extras/marker, 기존 dependency pin
- 실제 ish installer의 staging/rollback 연결(네트워크 pip 실행만 fixture로 대체)
- 파일 Tool의 Loop 실행, 승인/operation receipt, 안전한 retry, None 완료, CodedError

기존 전체 suite는 Loop/Graph/Agent, 격리 실행, 승인·재개, RAG/Memory Tool 제공자와
저장·장애 복구 계약을 함께 검사한다. 외부 모델/API 인증이나 실제 PyPI 다운로드는 필요 없다.

```bash
.venv-linux312/bin/python -m unittest llm.tests.test_tool_packages -v
.venv-linux312/bin/python -m unittest discover -s tests -v
```

## 출시 전 버전/호환성 코드 점검

버전 숫자 자체는 이전 버전을 지원하는 호환성 패치가 아니다. 현재 구현은 다음 형식을
검증하고 다른 버전은 거부한다. 출시 전 기준 번호를 1로 통일했으며 기존 저장 파일은 변경하지 않았다.

| 위치 | 의미 | 과거 형식 지원 여부 |
| --- | --- | --- |
| core/models.py, services/infrastructure/storage.py | Project/Session/Run/Step storage_version=1 | 1만 읽고 쓴다. 자동 변환 없음 |
| components/rag/component.py | corpus graph_schema_version=1 | 1만 지원. 이전 그래프를 자동 변환하지 않음 |
| components/workflows/graph.py | Workflow schema_version=1 | 현재 JSON 정의의 구조 검증 |
| services/runtime/checkpoints.py | checkpoint schema_version=1 | 현재 Run/Engine 소유 관계 검증 |
| services/runtime/operations.py | Tool 원장 schema_version=1 | 현재 Session/Tool 영수증 검증 |
| services/infrastructure/backups.py | backup format_version=1 | manifest/파일 무결성 검증 |
| services/infrastructure/transactions.py | WAL format_version=1 | 복구 시 지원 형식 검증 |

실제로 남아 있는 호환/전환 관련 기능은 별도로 구분된다.

- `llm/compat.py`: Python 3.9의 dataclass slots, StrEnum, timeout, aclosing 어댑터.
  과거 요청에 따른 Python 버전 호환 코드다. 이번 실행 검증은 3.12.14만 수행한다.
- `providers/runtime.py`: LiteLLM `max_retries or DEFAULT_MAX_RETRIES` 동작을 막기 위한
  `DEFAULT_MAX_RETRIES=0` 호환성 규칙. 명시적인 request retry는 변경하지 않는다.
- `ProjectManager.upgrade_backup` / `DirectoryBackups.upgrade`: 호출자가 직접 제공한
  transform을 별도 백업에 적용하는 공개 확장 API. 내장 구버전 변환기는 없다.
- `Component.configuration`: 일반 Component의 이전 component.json을 거부하는 검사.
  읽기/변환 지원은 아니다. ToolComponent의 새 경로에는 이 legacy 검사를 사용하지 않는다.
- `BaseEngine`: 이전 provider function_call 응답을 거부하는 검사. tools/tool_calls
  프로토콜로 자동 변환하는 호환 처리는 없다.

`LITELLM_LOCAL_MODEL_COST_MAP=True`는 초기화 네트워크 격리 규칙이며 저장 형식 호환과
무관하다. 버전 번호 통일은 형식이나 호환 어댑터를 변경하지 않는다.
