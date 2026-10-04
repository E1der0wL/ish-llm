# BuiltinTools — 호스트가 선택해 제공하는 Tool

파일 읽기·편집과 선택적인 셸/프로세스·서비스 어댑터를 ToolRegistry로 묶습니다. Project의 Python Tool 패키지와 별개이며 호스트 코드가 등록하고 수명을 관리합니다.

## 파일 안내

| 파일 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | BuiltinTools와 BuiltinToolComponent 공개 import입니다. |
| [catalog.py](catalog.py) | Tool schema와 구현을 조립하고 명시적으로 주입한 서비스 어댑터를 연결합니다. |
| [component.py](component.py) | 호스트 Toolkit을 Project의 명시적 enabled 선택에 연결합니다. |
| [files.py](files.py) | 지정한 root 범위의 파일 조회·읽기·작성·수정·삭제를 구현합니다. |
| [processes.py](processes.py) | 명령 실행·pipe 입력·출력 cursor와 프로세스 종료를 관리합니다. |
| [diagnostics.py](diagnostics.py) | Linux 시스템·프로세스·표준 I/O 연결을 읽기 전용으로 조회합니다. |

## 연결 방법

`BuiltinToolComponent(toolkit, name="computer")`를 등록하고 Project에서 `computer`를 선택합니다.
`ProjectConfig.parameters.components.computer.enabled`에 제공할 이름을 명시합니다.
enabled가 없으면 Tool을 노출하지 않습니다. 기존처럼 호스트의 사용자 정의 Component에서
Toolkit registry를 반환해도 됩니다. ToolComponent는 Project Python 소스만 담당합니다.

Component가 반환하는 Tool 계약에는 작업 루트·셸·검증 명령·호스트 제한의 지문이 들어갑니다.
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

process_start에 stdin=true를 주면 process_write로 정확한 문자열 또는 EOF(close=true)를 보냅니다.
이것은 pipe이며 PTY·기존 ish 터미널 연결은 아닙니다. process_output은 stdout_offset,
stderr_offset, max_chars를 받고 next_stdout_offset/next_stderr_offset을 반환합니다.
offset은 Unicode 문자 단위이며 분할 도착한 UTF-8 문자는 완성 후 공개합니다.
max_output_bytes에 의해 버린 출력은 truncated로 표시하고 복원된 것처럼 반환하지 않습니다.
프로세스 ID와 출력은 Toolkit 수명에 한정됩니다. 재시작 후 자동 재실행하지 않습니다.

BuiltinTools의 async 수명을 닫아 자식 프로세스를 정리해야 합니다. 파일 root 검사는 셸 전체를 격리하는 sandbox가 아닙니다. 승인·retry·실행 이력은 여전히 공통 ToolExecutor를 통과합니다.

등록 코드와 개별 Tool 목록은 [기본 Tool 안내](../../../../docs/llm/builtin-tools.md), Project Python Tool은 [상위 폴더](../README.md)를 참고하세요.

[상위 안내](../README.md)
