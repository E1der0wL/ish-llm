# BuiltinTools — 호스트가 선택해 제공하는 Tool

선택 ComponentData의 `alist_processes()`와
`aread_process(id, stdout_offset=..., stderr_offset=..., max_chars=...)`는 기존 native 관찰과
저장 출력을 Project 범위에서 조회합니다. 프로세스 생성·재실행 권한은 주지 않으며 재시작 후에는
retained 출력으로 구분합니다. [관찰 계약](../../../../docs/llm/interactive-contracts.md#프로세스-관찰)을 참고하세요.

파일 읽기·편집과 선택적인 셸/프로세스·서비스 어댑터를 ToolRegistry로 묶습니다. Project의 Python Tool 패키지와 별개이며 호스트 코드가 등록하고 수명을 관리합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | BuiltinTools와 BuiltinToolComponent 공개 import입니다. |
| [catalog.py](catalog.py) | Tool schema와 구현을 조립하고 명시적으로 주입한 서비스 어댑터를 연결합니다. |
| [catalog_values.py](catalog_values.py) | Git·prompt·Host 어댑터 선언과 실행 환경/계약 지문 투영을 소유합니다. |
| [component.py](component.py) | 호스트 Toolkit을 Project의 명시적 enabled 선택에 연결합니다. |
| [files.py](files.py) | 명시한 경로 권한에 따른 파일 조회·읽기·작성·수정·삭제를 구현합니다. |
| [file_values.py](file_values.py) | 파일 Tool의 공통·작업별 입력 Schema, 조회·변경 결과와 휴지통 ID 계약을 선언합니다. |
| [path_access.py](path_access.py) | 작업 디렉터리와 독립된 읽기·쓰기·cwd 권한과 안전한 파일 열기를 검증합니다. |
| [file_text.py](file_text.py) | 전체 원문을 메모리에 올리지 않는 파일 해시·줄 위치·범위 조회를 제공합니다. |
| [file_cache.py](file_cache.py) | Host가 용량을 명시했을 때만 검증한 UTF-8 원문을 재사용합니다. 경로 권한은 매번 검사합니다. |
| [file_execution.py](file_execution.py) | 파일 준비 중 협력 취소와 게시 이후 fsync·결과 확정의 분리입니다. 승인·retry 정책은 소유하지 않습니다. |
| [output.py](output.py) | 명시한 Data 목적지에 명령 출력·참조를 함께 저장하고 재시작 후 범위 조회합니다. |
| [capture.py](capture.py) | 호출별 출력 저장 연결을 다른 동시 실행과 분리합니다. |
| [processes.py](processes.py) | 명령 실행·pipe 입력·출력 cursor와 프로세스 종료를 관리합니다. |
| [process_values.py](process_values.py) | 프로세스 상태·입력 Schema·명시적 실행 인자·공개 결과를 선언합니다. |
| [diagnostics.py](diagnostics.py) | Linux 시스템·프로세스·표준 I/O 연결을 읽기 전용으로 조회합니다. |
| [diagnostic_values.py](diagnostic_values.py) | 진단 Tool의 입력 필드·Schema와 시스템/프로세스 관측 응답을 선언합니다. |
| [arguments.py](arguments.py) | 내장 Tool 필드 선언에서 입력 조회와 공개 Schema를 파생합니다. |
| [workspaces.py](workspaces.py) | Project Tool 호출·취소 수명과 테스트 공간의 sandbox 실행 라우팅을 담당합니다. |
| [workspace_repository.py](workspace_repository.py) | 테스트 공간의 경로·복사·잠금·게시·조회·명시적 삭제를 소유합니다. |
| [workspace_records.py](workspace_records.py) | 소유권 기록·완료 상태와 불완전 공간의 관측을 구분하고 저장 읽기를 검증합니다. |
| [workspace_values.py](workspace_values.py) | 테스트 공간 Tool 입력·Schema·Host 실행 설정·환경 지문·실행 결과를 선언합니다. |

## 연결 방법

파일 변경, 프로세스 시작/입력/종료, shell/check/test 실행, 브라우저 이동/조작,
커널 실행/초기화는 ToolContract.approval_required=True입니다. 조회 Tool은 그대로입니다.
Host ASK 없이도 Run이 PAUSED가 되고, Project의 scheme/category/risk rule에 따라
policy 응답 또는 사용자 승인을 기다립니다. 자동 응답도 명시적 resume 전에는 실행하지 않습니다.
계약과 작업 환경 지문은 함께 binding되어 재개 시 변경을 거부합니다.

일반 파일/프로세스 Tool의 파일 크기·출력 크기·명령 시간은 생성자 제한이 아닙니다. Tool의 max_file_bytes,
max_output_bytes, timeout_seconds에 명시하고, 변경 불가/범위 제한이 필요하면
Project policies.tools.argument_constraints의 fixed/bounded/selectable을 사용합니다.

`BuiltinToolComponent(toolkit, name="computer")`를 등록하고 Project에서 `computer`를 선택합니다.
`ProjectConfig.parameters.components.computer.config.enabled`에 제공할 이름을 명시합니다.
enabled가 없으면 Tool을 노출하지 않습니다. 기존처럼 호스트의 사용자 정의 Component에서
Toolkit registry를 반환해도 됩니다. ToolComponent는 Project Python 소스만 담당합니다.

Component가 반환하는 Tool 계약에는 작업 루트·셸·검증 명령의 지문이 들어갑니다.
이 값이 바뀌면 옛 체크포인트의 실행 환경과 다르므로 재개를 거부합니다. 외부 어댑터의 내부
구현/상태 변경은 호스트의 계약이며 카탈로그가 자동 추론하지 않습니다.

파일 root를 명시하고, 셸 실행은 `allow_commands=True`를 선택한 경우에만 노출합니다. 웹 검색·브라우저·외부 서비스 기능은 `adapters`로 실제 구현을 전달해야 합니다. 이름이 등록되었다고 외부 서비스가 자동 구성되지는 않습니다.

셸 argv는 `shell=["/bin/sh", "-c"]`처럼 명시합니다. `diagnostics=True`는 작업 루트 밖의
Linux 프로세스 상태를 조회하는 system_inspect/process_list/process_inspect를 등록합니다.
권한으로 읽을 수 없거나 사라진 필드는 unavailable로 구분하며 앱의 과거 동작을 추측하지 않습니다.

file_search는 literal 검색, include/exclude glob, context_lines, offset/limit을 제공합니다.
file_read는 tail_lines 또는 줄 범위를 받으므로 기존 앱 로그도 새 저장소 없이 조회할 수 있습니다.
file_search 페이지는 현재 파일을 다시 읽으며 파일 변경 시 처음부터 검색해야 합니다.
file_read의 expected_sha256으로 동일 파일 버전 여부를 확인할 수 있습니다.
문자 범위(`offset`·`max_chars`) 조회는 전체 파일의 SHA-256·UTF-8·명시적 크기 제한을
한 번의 순회에서 검사하고 요청한 페이지와 다음 문자만 보관합니다. 줄 위치 임시 인덱스는
줄 범위·검색 경로에서만 만듭니다. 기본적으로 다음 조회는 파일을 다시 검증합니다.

Host가 `BuiltinTools(..., read_cache_bytes=...)`에 양의 정수 용량을 명시하면 문자 범위
조회에 검증된 UTF-8 원문 캐시를 사용합니다. 미설정이면 캐시를 만들지 않습니다.
용량은 보관하는 원문 바이트의 합이며 빈 파일은 관리 단위 1을 사용합니다. Python 객체
관리 비용·일시적 decoding 메모리는 별도입니다. 용량보다 큰 파일은 기존 streaming 경로를
사용하고, LRU 축출과 toolkit 종료 시 해제합니다. 캐시는 영속 데이터나 승인 근거가 아닙니다.
매 호출 경로·symlink·읽기 권한·명시적 크기 제한을 확인하고, 열린 파일의 device/inode/mode/
size/mtime_ns/ctime_ns가 같을 때만 검증 결과를 재사용합니다. 조회 전후 변경도 거부합니다.
이 opt-in은 변경 stat이 일관적인 로컬 파일시스템용이며, 외부 서버가 stat 변경을 숨기는
파일시스템에서는 선택하지 않아야 합니다. 원문 수정·교체 시 hash/UTF-8 검사를 다시 합니다.

file_move와 file_restore는 Linux `renameat2(RENAME_NOREPLACE)`로 이동합니다.
경로 검사를 통과한 뒤 다른 프로세스가 목적지 파일이나 심볼릭 링크를 만들어도 덮어쓰지 않습니다.
해당 연산을 지원하지 않는 환경·파일시스템이나 서로 다른 파일시스템 사이의 이동은 실패하며,
덮어쓰는 rename 또는 복사 후 삭제로 우회하지 않습니다. 이동 뒤 디렉터리 동기화에 실패하면
이미 이동했을 수 있으므로 no-effect 오류로 바꾸거나 자동 재실행하지 않습니다.

읽기·검색·해시·임시 쓰기·테스트 공간 복사는 블록/항목 사이에서 취소를 확인합니다.
잠금 대기도 취소를 확인하며, 새 timeout·크기 제한을 적용하지 않습니다. 파일 게시·이동·삭제 또는
완성된 테스트 공간 게시를 시작했다면 fsync와 결과 생성을 마친 뒤 반환합니다. 방향 전환은 이
완료 결과를 보존하지만 일반 Run 중단과 명시적 Tool timeout을 철회하지는 않습니다.
단일 OS I/O, 큰 문자열 변환, 게시 뒤 정리를 강제 종료하지 않으므로 모든 작업의 즉시 종료를
보장하지 않습니다. 취소된 준비의 임시 파일/미완성 테스트 공간은 회수하며, 이미 생성된 부모
디렉터리 등의 효과까지 없었다고 추정하지 않습니다. 변경 Tool의 결과 없는 취소는 기존처럼
uncertain으로 처리하고 자동 반복하지 않습니다. 경로 권한·CAS·원자적 no-overwrite는 유지합니다.

process_start에 stdin=true를 주면 process_write로 정확한 문자열 또는 EOF(close=true)를 보냅니다.
이것은 pipe이며 PTY·기존 ish 터미널 연결은 아닙니다. process_output은 stdout_offset,
stderr_offset, max_chars를 받고 next_stdout_offset/next_stderr_offset을 반환합니다.
offset은 Unicode 문자 단위이며 분할 도착한 UTF-8 문자는 완성 후 공개합니다.
max_output_bytes에 의해 버린 출력은 truncated로 표시하고 복원된 것처럼 반환하지 않습니다.
영속 출력을 선택하지 않은 프로세스 ID와 출력은 Toolkit 수명에 한정됩니다.
시작 결과가 호출자에게 전달되기 전까지 자식 프로세스는 `start()`가 소유합니다.
이 구간에 취소 또는 첫 상태 조회 실패가 발생하면 프로세스·monitor·pipe 정리를 마친 후
실패를 전달합니다. 결과를 전달한 `process_start`는 기존처럼 명시적으로 조회·취소합니다.
이미 발생한 효과를 없던 것으로 취급하거나 명령을 자동 재실행하지 않습니다.
`config.output`과 `policy.output_preview_chars`를 함께 지정하면 출력 원문을 Data에 저장하고
재시작 후에도 `process_output`으로 조회합니다. 재시작 후 자동 재실행하지 않습니다.
상태는 `process_values.py`의 `ProcessStatus`가 소유하며 생성·감시·취소·출력·입력 경계가
같은 선언을 사용합니다. snapshot은 기존 문자열 상태를 반환합니다.
출력 목적지와 preview의 Schema·해석·실행 binding은 같은 파일의 `OutputConfig`를 사용합니다.
공개 config/policy 경로, 저장 출력 참조와 승인 binding의 JSON 형식은 유지합니다.

shell_execute/process_start/test_workspace_execute는 선택적 문자열 딕셔너리 `env`를 받습니다.
생략하면 기존 실행 환경을 유지하고, 지정하면 전체 대체하며 `{}`는 빈 환경입니다.
전역 환경을 변경하거나 값을 자동 병합하지 않습니다. 일반 shell/process의 기존 cwd는 유지하고
sandbox는 선택한 테스트 공간에서 실행합니다. 명시 env도 승인·재개 binding의 Tool 인자에 포함됩니다.

BuiltinTools의 async 수명을 닫아 자식 프로세스를 정리해야 합니다. 파일 root 검사는 셸 전체를 격리하는 sandbox가 아닙니다. 승인·retry·실행 이력은 여전히 공통 ToolExecutor를 통과합니다.

`policy.read_access={"roots": [...], "follow_symlinks": true}`는 명시한 범위의 외부 파일·링크
읽기를 허용합니다. 수정·삭제와 sandbox 권한은 바뀌지 않습니다. `file_read.offset/max_chars`,
`file_search.max_chars`는 큰 텍스트의 반환 범위를 제한합니다. 생략한 제한을 자동 생성하지
않습니다. 자세한 설정·범위 단위·성능 한계는 [대용량 파일 계약](../../../../docs/llm/large-data-files.md)을 참고하세요.

등록 코드와 개별 Tool 목록은 [기본 Tool 안내](../../../../docs/llm/builtin-tools.md), Project Python Tool은 [상위 폴더](../README.md)를 참고하세요.

[상위 안내](../README.md)

`TestWorkspaces`를 `BuiltinTools(test_workspaces=...)`로 주입하고 `ToolRuntime.runner`에
`BuiltinToolRunner()`를 연결하면 별도 테스트 공간에서 Bubblewrap 명령을 실행할 수 있습니다.
기존 파일 Tool은 `workspace_id`로 그 공간을 선택합니다. 생성·실행·삭제는 승인 대상이며
종료/취소 후 파일은 유지됩니다. 자세한 설정과 격리 범위는
[테스트 공간 안내](../../../../docs/llm/test-workspaces.md)를 참고하세요.
