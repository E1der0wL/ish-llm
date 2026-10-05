# 기본 Tool 사용법

BuiltinTools는 호스트가 작업 루트와 실행 기능을 명시해 만드는 카탈로그다.
BuiltinToolComponent(toolkit, name="computer")는 카탈로그를 Project의 tools capability에 연결한다.
Project-owned Python 소스를 관리하는 ToolComponent와 저장소·수명이 다르다.

## 연결

1. BuiltinTools(workdir, ...)를 async with로 만든다.
2. BuiltinToolComponent(toolkit, name="computer")를 LargeLanguageModel components에 등록한다.
3. Project components에서 computer를 선택한다.
4. ProjectConfig.parameters.components.computer.enabled에 제공할 Tool 이름을 저장한다.

enabled가 없으면 builtin Tool을 노출하지 않는다. toolkit.registry.names()와 definitions()로
이름과 인자 schema를 조회한다. 호스트의 사용자 정의 Component가 registry를 반환하는 확장도
유지한다. Toolkit을 backend보다 바깥 async with로 관리하여 Run 종료 후 프로세스를 정리한다.
작업 루트와 llm workspace를 분리하고 서로 다른 작업 루트에는 별도 Toolkit을 연결한다.

[실행 가능한 개발·진단 예제](../../examples/llm/developer_assistant.md)와
[Component 공개 계약](../../llm/components/tools/builtin/README.md)을 참고한다.

## 카탈로그

| 등록 조건 | Tool |
| --- | --- |
| 항상 등록 | file_list, file_search, file_read, file_create, file_patch, file_move, file_delete, file_restore |
| allow_commands=True | shell_execute, process_start, process_write |
| 명령/검증/Git 기능 중 하나 사용 | process_status, process_output, process_cancel |
| checks에 명령 등록 | check_run, test_run |
| git=True | git_status, git_diff |
| diagnostics=True | system_inspect, process_list, process_inspect |
| bind_prompts(AgentData) | prompt_read |
| 해당 어댑터 제공 | web_search, web_fetch, browser_open, browser_snapshot, browser_act, kernel_execute, kernel_reset, external_rag_search, external_graphrag_search, context_expand |

Project에서 선택한 SkillComponent는 skill_list/skill_read를 별도로 제공한다.
내장 rag_search 및 Memory Tool도 각 Component가 제공하며 외부 검색 어댑터와 다르다.

## 소스·로그와 파일 변경

파일은 작업 루트 상대 경로와 UTF-8 텍스트를 사용한다. 상위 경로·절대 경로·symlink·내부
.llm-* 경로는 거부한다. max_file_bytes를 명시하면 큰 파일을 읽기/변경에서 거부하고 검색에서
skipped로 표시한다. 미설정이면 추가 바이트 제한은 없다.

- file_list/file_search의 include/exclude는 루트 상대 경로의 pathlib glob이다.
  include=["*.py"], exclude=[".git", ".venv", "__pycache__"]처럼 사용한다.
  제외한 디렉터리는 탐색하지 않는다. gitignore 자동 해석은 하지 않는다.
- file_search는 literal 검색이며 query, recursive, case_sensitive를 요구한다.
  context_lines로 주변 줄, offset/limit으로 페이지를 지정한다. 파일 해시·줄 번호와
  다음 페이지의 next_offset을 반환한다. 매번 현재 파일을 읽으므로 변경 후에는 다시 검색한다.
  limit이 없으면 일치 결과 전체를 반환한다.
- file_read는 start_line/max_lines 또는 tail_lines를 받는다. 두 방식을 함께 지정하지 않는다.
  sha256은 전체 파일의 버전이며 expected_sha256으로 같은 버전을 확인할 수 있다.
  next_line은 남은 페이지의 다음 줄 번호다. tail도 현재 파일 읽기 제한을 따른다.
- file_patch는 expected_sha256과 old_text의 정확히 한 번 일치를 확인한다.
  file_move/file_delete도 버전을 검사한다. create/move/restore는 기존 대상을 덮어쓰지 않는다.
- file_delete는 <work>/.llm-trash/<id>/content로 이동한다. file_restore는 trash_id와 path를 받는다.
  재귀 디렉터리 삭제는 제공하지 않는다.

변경은 인스턴스 잠금과 atomic replacement를 사용한다. 외부 편집기와 전체 검사/교체를
OS compare-and-swap으로 보장하지 않는다. 진단은 기존 로그를 읽고 새 상태 원본을 만들지 않는다.
llm 자신의 실행 이력은 계속 project.activity와 Run/Step으로 조회한다.

## 프로세스 실행·입력·출력

allow_commands=True일 때 shell=["/bin/sh", "-c"]처럼 셸 argv를 명시한다.
임의 호스트 명령 실행을 허용하는 선택이며 cwd가 파일/네트워크 sandbox를 만들지 않는다.

- shell_execute: command, 선택적 cwd/timeout_seconds. 종료를 기다린다.
- process_start: argv, 선택적 cwd/timeout_seconds/stdin. process_id를 즉시 반환한다.
  stdin=true일 때만 입력 pipe를 열며 PTY는 아니다.
- process_write: process_id, text, close. text를 그대로 보내고 close=true면 EOF를 보낸다.
  개행을 자동 추가하지 않는다. 빈 text는 close=true일 때만 허용한다.
  취소/오류로 입력 효과가 불확실해졌다면 임의로 재전송하지 않는다.
- process_output: process_id, 선택적 stdout_offset/stderr_offset/max_chars.
  offset은 각 스트림의 Unicode 문자 단위다. next_stdout_offset/next_stderr_offset을 다음
  호출에 전달한다. stdout_remaining/stderr_remaining은 현재 보관된 나머지 문자 수다.
  나뉘어 도착한 UTF-8 문자는 완성 후 공개한다. 스트림 사이의 사건 순서를 추측하지 않는다.
- process_status/process_cancel: 이 인스턴스의 process_id만 사용한다. status는
  PID·argv·cwd·시작/종료 시각·보관 출력·종료 코드를 반환한다.
- check_run/test_run: 호스트가 checks={name: argv}에 등록한 명령만 실행한다.
  status=completed는 프로세스 종료이며 검증 성공은 returncode와 출력으로 판단한다.
- git_status/git_diff: 상태와 변경을 읽고 외부 diff/textconv를 끈다.

max_seconds/max_output_bytes를 명시하면 실행 시간/보관 출력에 적용한다. 미설정이면 추가
제한을 만들지 않는다. 출력 제한에 도달해도 pipe는 계속 비우고 truncated=true를 반환한다.
잃은 출력은 cursor로 복구되지 않는다. max_chars는 보관 데이터가 아닌 조회 페이지만 제한한다.

전경 취소는 프로세스 그룹 종료를 기다린다. process_start의 백그라운드 작업은 process_cancel,
자체 종료/명시적 timeout, Toolkit.close까지 유지된다. ID/출력 버퍼는 영속 데이터가 아니며
재시작 후 연결하거나 자동 재실행하지 않는다. 호출과 결과는 기존 Tool Step에 저장된다.

## Linux 진단

diagnostics=True로 카탈로그를 만들고 필요한 이름을 Project enabled에 넣는다.
system_inspect는 커널/Python/CPU 개수/load/메모리와 작업 루트 디스크 정보를 반환한다.
process_list는 이름 query와 after_pid/limit을 받고 부모 PID·프로세스 그룹·터미널 정보를 포함한다.
process_inspect는 pid, 선택적 expected_start_ticks/include_files를 받는다.

명령/cwd/executable/표준 I/O를 읽고 include_files=true일 때 fd도 읽는다.
PID 재사용이 감지되면 다른 프로세스 정보를 합치지 않는다. 접근 불가·종료는 unavailable 또는
조회 실패로 표시한다. 관찰 시점이 다른 best-effort 표본이며 원자적 시스템 스냅샷이나 앱의
UI 포커스·과거 trace가 아니다. ish 입력 라우팅을 증명하려면 호스트의 해당 이벤트 계측이 필요하다.

## 외부 어댑터·프롬프트

어댑터는 async callable(arguments: dict) -> JSON 값이다. adapters에 실제 함수를 제공해야
등록된다. 브라우저/커널/검색 서비스를 자동 구성하지 않는다. 외부 검색의 corpus/query/options는
내장 rag_search와 다르다. 내장 검색은 현재 Project의 문서·관계·출처를 함께 반환한다.

bind_prompts(agents)는 Agent의 system_prompt를 읽고 revision 확인 후 수정한다.
PromptComponent 레코드 CRUD가 아니다. 먼저 bind한 뒤 Component enabled를 저장한다.
변경은 후속 실행에 반영되며 이미 시작한 Run 스냅샷은 바꾸지 않는다.
영속 프롬프트/지침의 모델 변경은 Refinement 제안·승인·CAS 경로를 사용한다. 기존 harness_propose_update 어댑터와 prompt_update Tool은 제공하지 않는다. 신뢰한 host는 AgentData.update_prompt를 직접 사용할 수 있다.

## 실행 경계·검증

LoopEngine과 Graph ToolNode는 공통 ToolExecutor를 사용한다. 승인·재시도·operation receipt·
Step 저장 책임은 builtin 구현으로 옮기지 않는다. 작은 모델의 Skill 준수 여부는 실제 모델
평가가 필요하며 강제 검증/승인은 Workflow와 실행 정책으로 구성한다.

Linux Python 3.12.14로 저장소 루트에서
python tests/llm/run_linux.py --full --modules tests.llm.test_builtin_tools tests.llm.test_skill_tools
명령으로 검사한다. [Skill 계약](../../llm/components/skills/README.md),
[기존 웹 조회 예제](../../examples/llm/bleach_research.py)도 참고할 수 있다.
