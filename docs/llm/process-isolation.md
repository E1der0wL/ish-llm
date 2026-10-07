# Tool 프로세스 격리와 외부 작업 중복 방지

`ProcessToolRunner`를 `ToolRuntime.runner`에 주입하면 등록된 Tool을 별도 프로세스에서
실행할 수 있다. 기본 Python 핸들러와 기존 BuiltinTools의 shell/process 구현을 자동으로
변경하지 않는다. 격리할 명령을 명시적으로 등록하고 JSON 입출력 worker를 연결한다.
클로저, 살아 있는 RAG/DB 연결, asyncio 객체를 자식으로 직렬화하지 않는다.

## 실행기 설정

```python
from llm.llm import LargeLanguageModel, BackendServices, ToolRuntime, ProcessToolRunner

runner = ProcessToolRunner(
    {"external_job": ["/usr/bin/python3", "-I", "/opt/my-tools/worker.py"]},
    cwd="/srv/llm-tool-work",       # 미리 만든 전용 작업 디렉토리
    read_only_paths=["/opt/my-tools"],
    isolation="sandbox",          # 기본값: Linux Bubblewrap 필수
    timeout_seconds=60,
    max_output_bytes=1_000_000,
    memory_bytes=512 * 1024 * 1024,
    max_concurrency=4,
    env={"LANG": "C.UTF-8"},
)
services = BackendServices(tool_runtime=ToolRuntime(
    runner=runner,
    operation_key=lambda call: "external-job:v1:" + call.arguments["operation_id"],
))
backend = LargeLanguageModel("./workspace", services=services)
```

프로젝트에는 `external_job` Tool 정의도 등록하고 선택해야 한다. 모델에는 일반적인
Tool schema를 제공하며, 실제 실행기는 등록된 argv만 선택한다. 인자를 명령 문자열에
이어 붙이거나 shell=True로 실행하지 않는다. 등록하지 않은 Tool 이름은 거절한다.
ToolRuntime 전체에 runner를 지정하면 모든 Tool이 이 runner를 통과하므로, 다른 Tool도
등록하거나 개발자가 별도의 명시적 라우터를 제공해야 한다.

Worker는 stdin의 ToolCall JSON을 읽고 stdout에 UTF-8 JSON 값 하나를 출력한다.
진단 출력은 stderr로 보낸다. 예를 들어 worker.py의 구현은 다음처럼 시작할 수 있다.

```python
import json
import sys

call = json.load(sys.stdin.buffer)
arguments = call["arguments"]
# 실제 외부 API 호출에는 call["idempotency_key"]를 제공자 규약에 맞춰 전달한다.
result = {"received": arguments, "idempotency_key": call["idempotency_key"]}
sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
```

ToolCall에는 name, arguments, step_id, project_id, session_id, run_id,
operation_key, idempotency_key가 있다. 프로그램 내부 API 키 환경변수는 자동 상속하지
않는다. 개발자가 env로 지정한 값만 전달한다.
Python worker에 직접 -I를 지정하면 PYTHON* 환경변수도 무시하므로 인코딩은 위처럼
바이너리 스트림에서 명시적으로 처리하는 편이 안전하다.

## Linux 실행 보장

Linux sandbox 모드는 Bubblewrap의 사용자/PID/네트워크 등 namespace, capability 제거,
읽기 전용 런타임 mount와 명시적인 작업 디렉토리 쓰기 mount를 사용한다. 네트워크는
기본적으로 공유하지 않는다. 네트워크가 필요한 worker만 개발자가 `allow_network=True`로
구성하면 호스트 네트워크 전체를 공유한다(도메인별 allowlist는 아니다). process 모드는
이 옵션과 무관하게 네트워크를 제한하지 않는다. `/usr`, `/bin`, `/lib`, `/lib64`의 존재하는 경로는 런타임 용도로
읽기 전용 노출한다. 추가 read_only_paths는 개발자가 신뢰하는 의존성만 지정한다.
호스트 전체와 proc/dev/sys는 추가 mount로 노출할 수 없다. 작업 디렉토리에는 신뢰된
실행기 소스나 Python 자체를 포함할 수 없다. 노출 디렉토리에 호스트 소켓/비밀 파일을
두지 않고 별도 작업 디렉토리를 사용한다.

`bwrap`이 없거나 OS가 다르거나 namespace 설정이 실패하면 실행을 거부한다. 자동으로
비격리 모드로 전환하지 않는다. Bubblewrap 설치/커널 사용자 namespace 정책은 배포
환경에서 준비한다. seccomp 프로파일이나 Linux cgroup의 전체 메모리/PID quota까지
제공하는 컨테이너 플랫폼은 아니다. 기본 런타임 읽기 영역도 최소 rootfs 수준은 아니다.
[Bubblewrap 공식 옵션](https://github.com/containers/bubblewrap/blob/main/bwrap.xml)

`isolation="process"`는 개발자가 명시적으로 선택하는 프로세스 분리 모드다.
**파일·네트워크 접근을 제한하는 보안 sandbox가 아니다.** 신뢰된 Python 게이트를
-I -S로 실행하고 프로세스 그룹 종료와 부모 사망 신호를 사용한다. Linux sandbox에서는
Bubblewrap 부모 사망/PID namespace 종료가 자손을 정리한다. process 모드에서 setsid로
분리된 임의 자식까지 종료를 보장하지는 않는다. memory_bytes는 프로세스별 RLIMIT_AS다.
프로세스 트리 전체의 합산 메모리/개수 제한은 제공하지 않으며, Windows 전용이던
max_processes 인자는 제거했다. 합산 제한이 필요하면 별도 cgroup 실행기를 주입해야 한다.

실행 대기열 대기를 포함한 timeout, 전체 stdout+stderr 크기, 동시 실행 개수를 제한한다.
정리는 최대 추가 시간이 필요하며 종료 확인에 실패하면 성공으로 처리하지 않는다.
파이프가 출력 한도로 정지해도 버퍼를 비우며 종료한다. 이미 발생한 파일 변경이나
원격 요청은 프로세스를 죽여도 취소/롤백되지 않는다. 기존 LiteLLM 스트림 스레드는
이 Tool runner의 종료 대상이 아니다.

## 영속 작업 키

operation_key 콜백은 동기 함수이며 문자열 또는 None을 반환한다. None은 중복 방지를
사용하지 않는 해당 호출이다. 키는 **같은 Session 안에서 같은 업무 요청을 식별하는 값**으로
선택한다. 단순히 인자를 해시하거나 모델의 일회성 tool_call_id를 사용하는 기본값은 없다.
대상 서비스, 업무 ID와 의미가 달라지는 버전을 포함한다. 모델이 임의 키를 바꾸면 중복
방지를 우회할 수 있으므로 중요한 업무 키는 호스트/UI가 관리하고 승인 정책에서 검증한다.

저장 구조:

```text
project/sessions/<session_id>/state/tool_operations/<sha256>.json
```

기록에는 원본 Run/Step ID, 업무 키, Tool 이름과 인자의 digest, started/completed 상태,
완료 결과 및 확인 근거가 들어간다. 실행 기록의 별도 계층을 만드는 것이 아니라 Session
안의 여러 Run에서 같은 외부 작업을 반복하지 않기 위한 인덱스다. Run/Step 저장은
계속 기존 서비스가 맡는다. `RunRepository`의 메서드로 사용자 저장소도 대체할 수 있다.

1. 승인 기록을 Step에 저장한다.
2. 동일한 workspace 소유권 잠금 아래에서 작업 키를 started로 원자적으로 기록한다.
3. 실제 Tool을 실행한다. runner에 전달하는 idempotency_key는 Session/Project/업무 키의
   안정적인 해시이며 HTTP 등 외부 제공자의 규약에 맞춰 전달할 수 있다.
4. JSON 결과를 원자적으로 completed로 저장한 뒤 Tool Step을 완료한다.

같은 키·같은 요청의 completed 기록이 있으면 결과를 재사용한다. 같은 키에 다른 Tool/
인자는 operation_conflict다. started 기록은 실행 중이거나 완료 여부가 불확실하므로
operation_uncertain으로 차단한다. 병렬 Graph 분기도 동일한 원장을 사용한다.
승인과 호출 예산은 결과 재사용 때도 적용한다. 원장 예약/결과 저장 실패, 취소, timeout,
프로세스 crash 뒤에는 재실행하지 않으며, 실행되지 않은 작업도 보수적으로 차단될 수 있다.

이 설계는 자동 재실행을 막는 것이며 외부 서버의 exactly-once를 단독으로 보장하지 않는다.
제공자의 idempotency 지원/보관 기간과 조회 API가 필요하다. 키가 없는 호출, 다른 Session,
원장 삭제/오래된 백업 복원, 핸들러 내부의 독자적인 재시도는 별도 고려 대상이다.
원장은 memory 대화 모드에서도 영속적이며 Session 복제는 새 작업 범위를 만든다.

## 결과 확인과 복구

```python
record = await session.run.aoperation("external-job:v1:job-123")

# 외부 제공자의 조회 API에서 완료 결과를 확인한 뒤:
await session.run.shutdown()  # 활성 Tool과 결과 확정이 경쟁하지 않게 Session을 해제
await session.run.areconcile_operation(
    "external-job:v1:job-123",
    result={"receipt_id": "receipt-456"},
    evidence="외부 job-123 조회에서 receipt-456 완료 확인",
)
```

재확인은 명령을 재실행하지 않고 확인한 결과만 확정한다. 활성 Session에서는 거부한다.
아무 근거 없이 started를 지우거나 재시도 가능 상태로 바꾸는 API는 제공하지 않는다.
원격 조회/확인 UI는 호스트가 구현한다. Graph 재개에서 이미 완료된 노드는 기존 체크포인트를
쓰며, 불확실한 노드를 명시적으로 재시도해도 그 안의 동일한 외부 작업 키는 이 원장으로
다시 검사한다.

## 검증 범위

Linux / Python 3.12.14에서 전체 테스트를 실행한다. Bubblewrap 실행 검사는 필수이며,
의존성이나 namespace 권한이 없으면 검사를 건너뛰지 않고 실패한다. 실제 프로세스/손자
종료, 백엔드 강제 종료, 출력 폭주, 한글 JSON, 환경변수 격리, 외부 효과 직후 crash와
재시작 차단, 동일 키 병렬 Graph 충돌, 결과 재사용과 수동 확인을 검증한다.
최신 실행 결과와 환경별 한계는 docs/llm/handoff.md에 기록한다.
