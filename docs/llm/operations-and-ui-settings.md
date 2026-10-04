# 운영 보강과 UI 설정 API

## Project 설정 폼

`LargeLanguageModel.project_schema()`는 JSON Schema Draft 2020-12 형식의 독립 사본을
반환한다. 파일 조회나 모델 호출은 하지 않는다. 등록된 Component와 Engine이 스키마를
제공하고 백엔드는 이를 조합한다.

```python
schema = backend.project_schema()  # 현재 등록된 전체 Component
schema = backend.project_schema(components=["tools", "rag", "memory"])

# 저장값, 스키마와 충돌 검사 버전을 한 번에 가져온다.
settings = await project.aconfiguration()
schema = settings["schema"]
form_values = settings["values"]  # schema와 같은 구조
fields = schema["properties"]["config"]["properties"]
print(fields["policies"]["properties"]["retention"]["properties"]["max_bytes"])
# {"type": ["integer", "null"], "minimum": 1, ...}
```

| 영역 | 내용 |
| --- | --- |
| `properties.title` | Project 제목, string |
| `properties.conversation_storage` | file/memory/null. null은 주입 저장소용이며 Session 존재 시 변경 제한 |
| `properties.components` | 등록된 Component 이름, 중복 없는 array |
| `properties.config` | policies, parameters, data 및 추가 JSON 키 |
| `properties.config.properties.parameters.properties.components` | 선택된 Component별 실제 설정 |
| `x-components` | 디렉토리, capability, 개별 데이터의 `record_schema` |
| `x-engines` | Engine 등록명과 `config.parameters.engines`에서 사용할 `configuration_key` |

`type`은 string/integer/number/boolean/object/array/null 또는 허용 타입의 배열이다.
`enum`, `minimum`, `exclusiveMinimum`, `description`을 폼에 사용한다. 미설정 값을 채우지 않는다.
`additionalProperties: true`는 추가 JSON 키 입력을 허용한다. `$ref`와 `$defs`가 있는
사용자 컴포넌트도 지원하므로 JSON Schema를 이해하는 폼 생성기를 사용하는 편이 편하다.

**임의로 추가 가능한 모든 키나 미래 LiteLLM 인자를 미리 열거할 수는 없다.**
API는 선언된 항목을 열거하고, 열린 영역을 명시한다. Loop completion 인자는 해당 엔진의 힌트이며
공급자별 인자·지원 여부와 Loop의 stream=True/n=1 및 messages/tools 소유 규칙은 별도다.
등록 이름이 바뀌면 Engine 선택지도 바뀐다. `LoopEngine(settings_name="shared")`이면
`x-engines[등록명].configuration_key`는 `shared`다.

Component는 `configuration_schema()`를 재정의하여 자료형·설명·제약을 제공한다.
스키마가 없으면 열린 JSON 설정을 유지하며 기본값을 추측하거나 생성하지 않는다.
이 힌트는 런타임 검증을 대체하지 않으므로
사용자 Component의 `validate_configuration()`도 같은 계약에 맞춰 구현한다.
Agent/Workflow/Skill/MCP의 **개별 레코드** 형식은 컴포넌트 공통 설정과 다르며
`x-components[name].record_schema`에 있다.

호스트 함수, Tool 승인/격리 어댑터, RAG 모델 클라이언트처럼 JSON으로 바꿀 수 없는
런타임 주입 항목은 Project 편집 필드로 가장하지 않는다. RAG의
`x-runtime-configuration`에 해당 생성자 인자를 안내한다. Loop의 명시적 생성자 제한은
`x-host-override`로 표시되며 UI 변경보다 우선한다.

저장은 기존 API를 사용한다. 각 저장은 별도 트랜잭션이며 설정 전체를 한 번에 커밋하는 API는 아니다.

```python
from copy import deepcopy

config = deepcopy(settings["values"]["config"])
config["policies"]["retention"].update(unit="run", keep_runs=3, max_bytes=500_000_000)
await project.asave(config=config, expected_version=settings["config_version"])

memory = deepcopy(settings["components"]["memory"]["configuration"])
memory["search_limit"] = 8
await project.components.memory.aconfigure(
    memory, expected_version=settings["component_versions"]["memory"])

# 선택 변경 후 최신 값/스키마를 다시 조회한다.
await project.components.aselect(["tools", "rag", "memory"])
settings = await project.aconfiguration()
```

## Tool 실행 계약과 외부 결과 확인

```python
from llm.llm import Tool, ToolContract, ToolPolicy

tool = Tool("deploy", "Deploy approved artifact", parameters, handler,
    contract=ToolContract(revision="2", effect="external",
        isolation="sandbox", approval_required=True, operation_key_required=True))

policy = ToolPolicy(authorize=authorize, runner=process_runner,
                    operation_key=operation_key, operation_probe=probe,
                    revision="policy-3")
```

계약은 신뢰한 개발자가 등록한다. Tool 레코드 편집으로 승인/격리 요구를 완화할 수 없다.
실행 전 승인·작업 키 어댑터 및 ProcessToolRunner의 명령/격리 수준을 검사한다.
`effect`는 개발자의 선언이며 실행 코드의 부작용을 자동 증명하는 기능은 아니다.
기존 Tool은 계약이 없으면 기존 ToolPolicy 규칙을 유지한다. sandbox는 Bubblewrap을
요구하며 사용할 수 없을 때 process 모드로 몰래 전환하지 않는다.

Tool 계약과 ToolPolicy fingerprint를 Loop/Graph 재개 검증에 포함한다.
승인 함수/runner 내부 코드를 변경했다면 개발자가 `ToolPolicy.revision`을 올려야 한다.
이번 변경 이전의 체크포인트는 새 실행 계약 fingerprint와 달라 재개가 거부될 수 있다.
기존 Run/대화 조회는 유지되며, 이 경우 실행 설정을 확인한 후 새 요청으로 시작한다.

`async probe(operation_record)`는 외부 시스템의 결과를 조회하는 호스트 어댑터다.
`{"status": "completed", "result": None, "evidence": "receipt 123"}`,
`{"status": "not_applied", "evidence": "not accepted by server"}` 또는
`{"status": "uncertain", "evidence": "server could not confirm"}`를 반환한다.
completed는 반환값이 없어도 명시적 `result=None`을 요구한다.

```python
await session.run.shutdown()
preview = await session.run.verify_operation("business-key")
receipt = await session.run.verify_operation("business-key", apply=True)
```

apply 호출은 다시 외부 결과를 조회한다. 조회 중 원장이 바뀌거나 Session이 재접속하면 반영을
거부한다. 증거와 관찰 이력을 저장한다. completed는 결과 재사용, not_applied는 이후 명시적
시도 허용, uncertain은 재실행 차단을 유지한다. 외부 시스템의 멱등 키/조회 기능 없이는
분산 작업의 exactly-once 실행을 보장할 수 없다.

## 무결성 검사와 복구

```python
await session.run.shutdown()
plan = await project.arecovery()
print(plan.issues, plan.repair_sessions)
result = await project.arecovery(apply=True, expected_version=plan.version)
```

Run/Step/대화 참조, 체크포인트, 저장 출력과 대화 본문, 컴포넌트 백업 검증을 수행한다.
손상·누락·활성 Session은 보고하며 추측해서 고치지 않는다. 안전한 stale 상태 전환과 출력
재조정만 적용하고 Tool/LLM을 재실행하지 않는다. RunManager 시작 복구와 같은 처리 함수를
사용한다. 검사 이후 원본이 바뀌면 거부한다. 적용 진행은 Project의 `state/recovery`에
기록한다. 복구 도중 실패한 경우 새 plan을 검사·적용하여 남은 불일치를 정리한다.
이전 실패 시도의 저널은 감사 자료로 남는다. 파일 저장 Session에 대한 오프라인 복구다.

## 보관 정리

기본은 무제한이며 자동 삭제를 예약하지 않는다. 기간·용량·토큰 상한을 설정한 뒤
`aretention()`의 후보와 보호 이유를 검토하고 같은 version으로 적용한다.

```python
await project.aconfigure_policies({"retention": {
    "unit": "run", "keep_runs": 3, "max_bytes": 500_000_000,
    "max_age_seconds": None, "max_tokens": None,
}})
plan = await project.aretention()
await project.aretention(apply=True, expected_version=plan.version)
# 이전 정리 도중 종료되어 새 실행이 막힌 경우: 이미 기록된 삭제 의도만 완료
await project.arecover_retention()
```

Run 단위는 완료 Run과 그 메시지만 제거하며 Session을 유지한다. 최신 Run, 재개 원본,
미완료 체크포인트의 대화, 활성 사용량, 외부 작업 원장, 컴포넌트의 `history_references()`를
보호한다. Memory 요약은 원본 접두부 hash 검증이 필요하므로 해당 Session을 보호한다.
보호 기록 때문에 상한 아래로 정리되지 않을 수 있다. Run 용량은 대화 크기의 추정치를
포함하며 전체 Project 디스크 사용량의 강제 상한이 아니다. 로그/컴포넌트 산출물은 별도다.

`Session.state/maintenance/retention.json`에 의도를 먼저 기록한다. 대화 교체, 디렉토리 부분
삭제, 감사 영수증 저장 중 실패해도 저널을 남기며 Session 실행을 차단한다.
`arecover_retention()`이 승인된 정리를 끝낸 뒤 차단을 해제한다. 진행 중 스트리밍 JSONL은
재작성하지 않는다. 이 정리는 비활성 파일 저장 Session에만 적용한다.

컴포넌트 자료는 소유 컴포넌트가 `maintenance()`로 정리 계획을 제공한다.
RAG는 아래 설정을 통해 완료·취소한 색인 작업 입력/영수증을 정리한다. 실패/대기/진행 작업,
OS lease를 가진 worker와 활성 문서 색인은 유지한다.

```python
await project.components.rag.aconfigure({
    "ingestion": {"max_active": 1},
    "retention": {"job_max_age_seconds": 30 * 86400},
})
plan = await project.amaintenance()
await project.amaintenance(apply=True, expected_version=plan["version"])
```

## 모델 사용량

Run Completion 기록과 `<component>/usage/<id>.json` 영수증을 함께 조회한다.
독립 Component 호출은 새 Run을 만들지 않는다. Tool 안의 RAG 호출은 현재 Run ID를
기록하여 해당 Run의 한도에도 포함한다. `project.amodel_usage()`는 호출 목록,
call_count/known_tokens/reserved_tokens/unknown_calls를 반환한다.

호출 전 예약과 검사에는 같은 workspace 잠금을 사용한다. RAG의 EmbeddingModel,
RerankModel, TripleExtractor는 공통 관찰 경계를 사용한다. 토큰 한도 사용 시 입력 계수기와
completion 출력 상한이 필요하다. 임베딩/rerank 입력은 계수기에 메시지 형태로 제공된다.
실제 usage가 있으면 실제 값을 사용하고, 없으면 예약을 유지한다. 예약도 없는 과거 호출은
unknown으로 남아 이후 토큰 한도 수락을 막는다. 예약은 모델별 과금의 정확한 예측이나
공급자 내부 재시도 횟수를 보장하지 않는다.

사용자 모델 클라이언트가 한도에 참여하려면 `observes_model_calls=True`를 선언하고 실제
호출을 `llm.providers.observations.observed_call()`로 감싸야 한다. 한도가 켜진 RAG에서
이 계약이 없는 주입 클라이언트는 실행 전에 거부한다. 일반 Component는 ComponentData의
`model_scope()` 안에서 같은 관찰 경계를 사용할 수 있다. 원시 SDK를 직접 호출한 사용자
코드를 자동 감시하는 기능은 아니다. 단독 ModelClient 호출도 Project 문맥이 없으면 기록하지 않는다.

등록을 해제하지 않은 컴포넌트는 프로젝트에서 비활성화되어도 사용량을 집계한다.
사용량 원본을 계속 해석할 수 있도록 재접속 시 해당 컴포넌트도 백엔드에 등록해야 한다.
진행 중 예약 또는 현재 Project 한도 기간에 필요한 영수증이 있으면 컴포넌트 영구 삭제를
거부한다. 사용량 영수증의 자동 삭제와 공급자 청구 내역 조정은 이번 API에 포함하지 않는다.

실제 5 Session × 3시간/2GB 부하·전원 장애·대상 Linux 배포판의 sandbox 검증은 별도다.
공유 저장 잠금과 전체 corpus JSON, 전체 사용량 영수증 조회 비용도 남아 있다.
