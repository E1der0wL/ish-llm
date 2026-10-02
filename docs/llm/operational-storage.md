# 실행 자원과 저장 정책

Project → Session → Run → Step 관계는 바뀌지 않는다. Engine은 이벤트를
생성하고, RunManager가 검증·저장·알림을 조율한다. 아래 정책은 런타임 서비스 설정이며
ProjectConfig에 Python 객체를 직렬화하지 않는다.

```python
from llm.llm import LargeLanguageModel, LoopEngine, ServiceConfig, ProviderLimits, OutputPolicy, RunLimits

backend = LargeLanguageModel(
    "workspace",
    engines={"loop": LoopEngine(request_timeout=180)},
    services=ServiceConfig(
        provider_limits=ProviderLimits(max_active=4, max_waiting=16, wait_seconds=10),
        output_policy=OutputPolicy(batch_size=32, max_delay=0.025, max_chars=65536),
        output_index_stride=128,
    ),
)
project = await backend.projects.acreate(config={"policies": {
    "run": {"timeout_seconds": 600, "max_capability_rounds": 32},
}})
```

## 공급자 호출

`ProviderCalls`는 실제 동기 completion 호출과 스트림 close가 끝날 때까지 슬롯을
보유한다. 소비자 취소/Run 종료만으로 슬롯을 반환하지 않는다. 대기 수와 대기 시간도
유한하며 한도 초과는 `provider_capacity` Run 오류 코드로 조회한다.
`backend.provider_calls.stats`는 `active`, `waiting` 스냅샷이다.

기본값은 active=8, waiting=32, wait_seconds=30이다. `ServiceConfig.provider_calls`로
공유 객체/서브클래스를 주입하면 provider_limits 대신 그 객체가 정책을 소유한다.
Session와 중첩 Engine은 같은 객체를 사용한다. ContextVar는 호출 문맥만 전달하며
프로세스 전역 세마포어나 영속 필드가 아니다. 독립 스트림 사용자는
`stream_completion(..., calls=pool)` 또는 `with pool.scope():`로 선택한다.

적용 대상은 이 플러그인의 **동기 completion 스트림 어댑터**다. 직접 SDK를 호출하는
커스텀 Engine이나 RAG의 독립 비동기 모델 API가 자동으로 이 한도에 포함되지는 않는다.
다른 어댑터도 acquire/release 계약을 사용해 같은 제한에 참여할 수 있다.
동기 네트워크 읽기를 강제 종료하지는 않는다. 연결/읽기 transport timeout은 LiteLLM의
completion 인자로, LLM iteration 전체는 LoopEngine.request_timeout으로, Run 전체는
RunLimits.timeout_seconds로 조절한다. 실제 호출이 영원히 반환하지 않으면 슬롯은
계속 점유되지만 새 스레드를 무제한 생성하지 않는다.

## 델타 묶음 저장

기본 `batch_size=1`은 기존 즉시 저장 동작이다. 2 이상이면 **연속 TEXT_DELTA만**
개수/시간/문자 수 기준으로 모은다. Engine 생산자는 한 asyncio.Task에서 유지해
timeout/context manager의 수명을 보존한다. 생산 큐도 batch_size로 제한한다.
max_chars는 묶음을 내보낼 기준이며 단일 이벤트 자체를 자르는 제한은 아니다.

Run 출력 저널과 Conversation 각각 한 묶음을 append/fsync한 뒤 개별 이벤트 순서대로
알린다. 순번, append/replace, Step 소유권과 visibility는 유지한다. 두 파일이 하나의
원자적 트랜잭션이 되는 것은 아니다. 중간 I/O 실패 시 Run은 성공으로 보고되지 않으며,
출력 저널이 대화 투영보다 앞설 수 있다. 저장된 출력 조회와 Run 상태를 함께 확인한다.

Step/Tool 승인/체크포인트/종료/사용자 확장 이벤트는 경계다. 앞선 델타를 확정하고
해당 이벤트를 처리한 뒤에만 Engine을 계속 진행한다. QUEUED 요청은 묶지 않는다.
취소와 실패에서도 이미 수락한 버퍼는 저장을 마친다. 프로세스 강제 종료 시 아직
저장·알림하지 않은 버퍼는 유실될 수 있다. 즉시 내구성이 필요하면 기본값을 유지한다.

커스텀 Engine은 델타 뒤에서 임의의 외부 효과를 수행하기보다 승인/Step 같은 명시적
이벤트 경계를 먼저 제공해야 한다. 매 델타의 저장 ACK에 의존하면 batch_size=1을 쓴다.
느린 디스크는 역압력을 발생시키며 무제한 쓰기 작업을 쌓지 않는다.

사용자 RunRepository의 record_output 재정의와 Conversation.delta/_append 재정의는
보존한다. 묶음 성능은 record_outputs/deltas/_append_many를 구현한 저장소에서 얻는다.

## 출력 조회 인덱스

`Run/state/outputs.index.json`은 `sequence → byte offset` 희소 인덱스다. 원본은
`outputs.jsonl`이며 after/limit API는 동일하다. 첫 커서 조회에서 인덱스를 만들고,
이후 추가된 저널 구간만 인덱싱한다. 삭제·손상·파일 교체는 재생성으로 처리한다.
인덱스 저장 실패는 원본 읽기를 막지 않는다. `output_index_stride=0`은 비활성화다.

인덱스 간격은 기본 RunRepository 생성에만 사용한다. 직접 주입한 저장소에는
`RunRepository(output_index_stride=...)`로 지정하거나 조회 자체를 재정의한다.
전체 출력 투영과 최초 인덱스 작성은 여전히 전체 기록에 비례한다. 인덱스는 일반적인
append-only 파일을 전제로 하며 외부 작성자의 임의 변조를 검증하는 감사 장치는 아니다.

## 백업, 복원과 형식 변경

```python
# 프로젝트의 모든 Session 런타임을 먼저 해제한다. 활성 작업 중에는 백업을 거부한다.
await session.run.shutdown()
archive = await project.abackup("backups/project-20260927")

# 동일 Project ID가 없는 다른 workspace에 복원한다.
async with LargeLanguageModel("restored-workspace") as destination:
    restored = await destination.projects.arestore_backup(archive)
```

백업은 `manifest.json`과 `project/` 트리로 구성되며 모든 일반 파일의 SHA-256을
검증한다. 대화, Run/Step, 체크포인트, Component 디렉토리를 포함한다. 파일 이름을
통해 Component의 내부 구조를 추측하지 않는다. 같은 workspace 소유권 안에서 복사하고,
Component.backup_scope 문맥을 유지한다. 외부 DB를 가진 Component는 해당 문맥에서
checkpoint/flush/동시 쓰기 차단을 구현해야 한다. 기본 파일 Component는 빈 문맥이다.
RAG는 작업별 DB 연결을 닫으며 현재 세대의 corpus/Chroma/Kuzu를 함께 검증·복사한다.

`validate_backup(project)`는 각 Component의 추가 검증 확장점이다. 도메인 기록 검증은
공유된 각 Repository.read_backup에 위임한다. 복원은 스테이징을 검증하고 동기화한 뒤
새 경로로 공개한다. 기존 프로젝트를 덮어쓰지 않으며 Run을 실행하지 않는다. 저장된
QUEUED 요청은 나중에 Session 런타임을 명시적으로 시작하면 기존 큐 복구 정책을 따른다.

현재 휴대 가능한 백업은 file 대화 저장만 지원한다. memory나 외부 주입 대화를 조용히
누락한 불완전한 백업을 만들지 않는다. `ServiceConfig.backups`로 복사/검증 구현을
교체할 수 있지만 다른 대화 저장소의 내보내기 계약은 별도로 구현해야 한다.

Project/Session/Run/Step metadata는 `storage_version=1`를 명시한다. 지원하지 않거나
누락된 버전은 읽기/덮어쓰기 전에 거부한다. **버전 필드가 없던 기존 workspace도 명시적
변환이 필요하다.** 사용자 workspace는 이번 변경에서 자동 수정하지 않는다.
열린 ProjectConfig/Component 레코드에는 고정 설정 키를 강제하지 않는다.

`backend.projects.aupgrade_backup(source, destination, transform=...)`은 신뢰한 개발자
함수를 복사본에 적용하고 현재 저장소/Component로 검증한 뒤 새 백업을 공개한다.
변환 실패는 원본을 수정하지 않는다. 향후 알려진 형식 변경마다 변환 함수와 검증을
추가한다. 과거 형식을 추정하는 호환 리더는 제공하지 않는다. 버전 없는 기존 데이터는
먼저 DirectoryBackups.create로 원본을 보존한 뒤 명시적 변환 대상으로 사용할 수 있다.

## 구조 검토

| 책임 | 구현 위치 | 확장/설정 |
| --- | --- | --- |
| 모델 호출 슬롯 | providers/calls.py | ProviderLimits, ProviderCalls 주입 |
| 델타 저장 경계 | services/runtime/output.py | OutputPolicy |
| Run 출력 저장/조회 | RunRepository → infrastructure/journal.py | 저장소 주입, 인덱스 간격 |
| 대화 저장 | Conversation/ConversationStore | 기존 파일·메모리·주입 팩토리 |
| 백업 조율 | ProjectManager | Component 문맥/검증, Repository 검증 |
| 복사/체크섬/공개 | infrastructure/backups.py | ServiceConfig.backups |

Core는 서비스/UI에 의존하지 않고 Engine은 영속 파일을 쓰지 않는다. 실행과 조회는
동일한 주입 저장소를 유지한다. 저수준 ProjectManager/RunManager의 기본 Run 저장소도
SessionManager를 통해 공유한다. capability 탐색의 종전 고정 32회 상한도
RunLimits.max_capability_rounds로 노출했다. 저장 버전·이벤트 이름·파일 이름은 계약상
고정값이며 운영 튜닝 값과 구분한다. 기본 프로젝트의 loop 선택은 기존 명시적 편의 API
정책이며, 일반 요청은 계속 engine 이름을 필수로 지정한다.

추가 제한: 백업은 workspace 잠금을 유지하므로 큰 프로젝트에서는 다른 CRUD가 기다린다.
온라인 백업, 외부 SDK의 강제 종료, 파일 간 원자적 출력 커밋은 이번 구현의 보장이 아니다.
장시간 실제 공급자/배포 환경 검증은 자동 회귀 테스트와 별도로 수행해야 한다.
