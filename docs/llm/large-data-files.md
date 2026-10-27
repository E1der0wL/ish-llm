# 대용량 파일·명령 출력과 경로 권한

큰 원문은 파일로 저장하고, 모델에는 애플리케이션이 정한 범위만 전달한다.
Backend는 저장·범위 읽기·revision 검증·취소 회수를 제공한다. 원문을 저장할지,
미리보기/페이지/요약 크기, 보관 기간, 허용 경로는 애플리케이션이 명시한다.
자동 요약, 자동 삭제, 숨은 출력 크기 제한을 추가하지 않는다.

## Data와 Skill의 파일 API

```python
data = project.components["data"]
snapshot = await data.asnapshot("results")
with open("/absolute/source/large.txt", "rb") as source:
    snapshot = await data.awrite_stream(
        "results", "logs/build.txt", source,
        expected_revision=snapshot["version"],
    )
page = await data.aread_text(
    "results", "logs/build.txt", expected_revision=snapshot["version"],
    offset=10000, limit=2000,
)
```

`write_stream`는 binary reader를 buffer 단위로 소비한다. reader 수명은 호출자 소유다.
`import_files`는 `{패키지 상대 경로: 로컬 source 경로}`를 받아 여러 파일과 manifest를
기존 workspace transaction으로 확정한다. 파일 쓰기와 참조 등록 실패는 함께 롤백된다.
기존 `write_file`은 이미 메모리에 있는 bytes/str 입력용으로 유지한다.

`read_file(offset, length)`는 byte 범위를 seek/read한다. `read_text(offset, limit)`는
UTF-8 문자 범위만 반환하고 앞부분을 작은 buffer로 해독한다. byte와 문자 cursor는
서로 바꾸어 쓰지 않는다. `limit=None`을 명시하면 남은 텍스트 전부를 반환한다.
Skill의 참고 자료도 같은 공개 범위 API를 사용한다.
Memory의 저장 원문 처리에 연결하는 사용자 정의 파일 구현도 공개 `read_text` 계약을
제공해야 한다. 전체 파일 읽기로 암묵적으로 대체하지 않고 capability 연결 때 검증한다.

기존 manifest/hash/CAS 검증은 유지한다. 무결성을 위해 패키지 파일을 검증하는 전체
I/O가 여전히 발생하며, 문자 offset은 해당 위치까지의 순차 해독이 필요하다. 이 기능은
메모리에 전체 원문을 올리지 않는 계약이며, 모든 조회가 페이지 길이만큼의 디스크 I/O로
끝난다는 계약은 아니다. 외부 경로 읽기 허용은 Data/Skill 내부 패키지의 symlink 금지를
완화하지 않는다.

## 파일 Tool의 범위 조회

```json
{"path":"logs/build.log","offset":10000,"max_chars":2000}
```

`file_read`는 문자 페이지의 `next_offset`을 반환한다. 마지막은 null이다.
`expected_sha256`을 다음 요청에 전달하면 바뀐 파일의 다른 부분을 이어 읽는 일을 막는다.
기존 줄 범위와 `tail_lines`도 유지하며 문자 범위와 동시에 지정할 수 없다.
전체 파일 해시와 줄 위치는 buffer/임시 파일로 계산한다. 매우 긴 한 줄에는 `max_chars`를
사용한다. `max_lines=1`만으로 한 줄의 문자 수가 제한되지는 않는다.

`file_search.max_chars`는 일치 줄과 문맥 줄의 반환 길이만 제한한다. 검색은 줄 전체를
검사한다. 명시했을 때 결과의 `line_previews=true`, `max_chars_per_line`로 잘린 표시임을
알린다. 기존 `limit`, `offset`, `context_lines`, include/exclude 계약은 유지한다.
크기 인자를 생략한 기존 전체 반환 API는 그대로 전체 결과를 반환할 수 있다.

## 작업 디렉터리와 읽기·쓰기·실행 위치

```python
await project.components["computer"].aconfigure({
    "config": {"enabled": ["file_read", "file_list", "file_search", "file_create", "shell_execute"]},
    "policy": {
        "read_access": {
            "roots": ["/work/project", "/shared/manuals"],
            "follow_symlinks": True,
        },
        "write_access": {
            "roots": ["/work/project", "/shared/results"],
            "follow_symlinks": False,
        },
        "cwd_access": {
            "roots": ["/work/project", "/shared/build"],
            "follow_symlinks": True,
        },
    },
})
```

* Toolkit의 root는 상대 경로의 기준이다. 각 권한은 독립적이며 읽기 허용만으로 쓰기나
  외부 디렉터리에서의 명령 시작을 허용하지 않는다.
* `read_access`, `write_access`, `cwd_access`는 각각 읽기, 파일 변경, 명령 시작 위치의
  허가다. 각 object에 roots와 follow_symlinks를 모두 명시해야 한다.
* 절대 경로와 상대 경로를 사용할 수 있다. 링크를 허용해도 최종 실제 대상이
  roots 안에 있어야 한다. roots는 절대 경로이며 링크의 이동 대상을 권한으로 삼지 않는다.
* roots가 빈 배열이면 그 작업을 허용하지 않는다. 별도 설정이 없으면 호출자가
  명시한 Toolkit root의 기존 링크 미추적 계약을 사용한다. 추가 외부 경로나 링크 권한을 만들거나
  이 계약을 Project config의 값으로 자동 저장하지 않는다.
* 직접 Toolkit을 연결하는 Host는 `BuiltinTools(root, read_access=..., write_access=...,
  cwd_access=...)`로 지정할 수 있다. Project의 명시적인 정책은 Component가 해석한다.
  모델 Tool 인자는 권한을 바꾸지 않는다. 기존 Host adapter의 기술적 거부를 우회하지 않는다.
* 링크 검사 후 파일을 여는 동안 경로가 바뀌어도 우회하지 못하도록 canonical 경로의 각
  segment를 descriptor와 O_NOFOLLOW로 연다. 디렉터리 순회는 inode 중복을 건너뛴다.
* 쓰기도 canonical parent descriptor로 접근한다. 링크 허용 시 patch/move/delete는
  링크 객체가 아니라 최종 정규 파일에 작용한다. 쓰기 권한에 포함된 파일의 CAS 검사에는
  내부 읽기가 필요하지만 그 원문을 file_read로 공개할 권한이 생기지는 않는다.
* 삭제 휴지통은 Toolkit root 아래에 유지한다. 다른 파일시스템 사이의 move/delete/restore는
  원자적 rename이 불가능하면 실패한다. 자동 copy/delete로 바꾸지 않는다.
* 명령 cwd도 descriptor로 확정한 디렉터리를 사용한다. 일반 셸의 파일/네트워크 접근은
  이 경로 정책으로 격리되지 않는다. OS 격리가 필요하면 test_workspace_execute를 선택한다.
* `workspace_id`로 선택한 테스트 공간에는 외부 파일/cwd 허가를 적용하지 않는다.
  그 공간의 파일, mount/network 및 실행 설정은 TestWorkspaces 계약이 소유한다.

## 명령 출력의 명시적 저장

먼저 Data 패키지를 만들고 BuiltinToolComponent에 목적지와 미리보기 크기를 지정한다.
다음 값은 예시 애플리케이션의 선택이며 Backend 기본값이 아니다.

```python
await project.components["data"].acreate({"title": "명령 결과"}, identifier="results")
await project.components["computer"].aconfigure({
    "config": {
        "enabled": ["shell_execute", "process_start", "process_output", "process_status"],
        "output": {"component": "data", "identifier": "results"},
    },
    "policy": {"output_preview_chars": 2000},
})
```

`output`과 `output_preview_chars`는 함께 명시한다. 0은 미리보기 생략이다.
shell/process/check/test/Git 명령은 stdout/stderr를 메모리 목록 대신 임시 파일로 수집한다.
선택된 저장 경로에서는 종료/취소 시 보존된 출력과 참조를 원자적으로 공개한다.
`test_workspace_execute`의 정상 완료 출력도 같은 저장 경로를 사용한다.
결과에는 작은 stdout/stderr 미리보기, 문자 수, process_id, `output_sources`가 들어간다.
미리보기가 짧아도 원문은 저장된다. 명시적인 `max_output_bytes`로 버린 출력은
`truncated=true`이며 원문이 복구된 것처럼 보고하지 않는다.

`process_output(process_id, stdout_offset, stderr_offset, max_chars)`로 원문을 나눠 읽는다.
저장된 결과는 backend/Toolkit 재시작 후에도 같은 Project에서 조회할 수 있다. 재시작은
명령을 재실행하지 않는다. 실행 중 핸들과 stdin은 복원하지 않는다. sandbox 명령이
취소/실패한 경우 성공 출력 레코드를 만들지 않으며 파일 작업 공간은 기존대로 남는다.
강제 프로세스 종료 전 아직 공개하지 않은 임시 출력은 재시작 복구 대상이 아니다.
조회 가능한 영속 결과는 파일과 참조의 트랜잭션이 완료된 결과다.

미지정 경로에는 영속 출력을 자동 생성하지 않는다. 기존 전체 반환도 유지하므로
대용량 모델 입력을 방지하려면 애플리케이션이 위 저장 설정과 페이지 크기를 선택해야 한다.

Builtin Component의 출력 레코드는 원문 참조와 프로세스 관측만 소유한다.
Run/Step/승인/operation receipt는 복제하지 않는다. Data는 바이트·manifest를 소유한다.
출력 설정 및 레코드가 참조하는 Data는 삭제 보호된다.
일반 create/save/update로 실행 관측을 위조하거나 참조를 갈아 끼울 수 없다.
출력 레코드의 명시적 삭제는
Project Session들이 비활성이고 미완료 재개 참조가 없을 때만 가능하다. 이후 저장 설정과
다른 참조까지 해제하면 Data API로 원문을 지운다. 자동 정리·보관 기한은 없다.
승인은 계속 ToolExecutor가 집행하고, 저장 실패를 명령 재실행으로 복구하지 않는다.
읽기·쓰기·cwd 권한, 저장 대상·미리보기 설정은 기존 실행 binding revision에 포함한다.

## 요약과 외부 응답

공통 `SummarySource(total_chars, identity, read_chars, read)`는 caller-owned 범위 reader다.
요약기는 명시한 크기만 요청하며 checkpoint·revision·게시 소유권은 호출자에게 남는다.
Memory는 저장된 Tool 원문을 이 경로로 요약하고 `memory_read_source`도 범위 reader를 쓴다.
현재 요청/지침, Tool 호출·결과의 짝, 승인·재개·중복 실행 방지 계약은 바꾸지 않는다.

외부 MCP/SDK가 이미 거대한 JSON/문자열을 만들어 반환하는 경우, 사후 저장만으로 그 SDK의
초기 메모리 사용을 없앨 수 없다. 해당 adapter가 별도 streaming/paging을 지원해야 한다.
Python Tool worker의 단일 JSON 응답도 완전한 JSON 해석이 필요하다. 임의 응답을 몰래
자르거나 자동으로 다른 저장 형식으로 바꾸지 않는다.

## 전체 Builtin Tool 제한 감사

| Tool 묶음 | 애플리케이션이 정하는 값 | Backend가 유지하는 계약 |
| --- | --- | --- |
| file_read/list/search | read_access, include/exclude, 페이지·문맥·바이트 크기 | UTF-8/정규 파일, 변경 감지, 관리 경로 보호 |
| file_create/patch/move/delete/restore | write_access, max_file_bytes, Project 인자 제약 | 승인, SHA256 확인, 정확한 patch 일치, no-overwrite, 원자적 파일 교체, 휴지통 identity |
| shell_execute/process_start | cwd_access, shell/argv/env, 시간·출력량 | 승인, 정확한 환경 전달, 프로세스 그룹 생성·취소 회수 |
| process_write/cancel/status/output | 공개 여부, 쓰기 내용, 페이지 크기 | 이 Toolkit이 시작한 프로세스 identity와 출력 소유자, 불확실한 입력 재실행 금지 |
| check_run/test_run | 명시한 명령 mapping, 시간·출력량 | 등록된 명령 identity와 승인 |
| git_status/diff | 공개 여부, Toolkit root, staged | 읽기 전용 관찰을 위해 외부 diff/textconv/fsmonitor 실행 금지 |
| system_inspect/process_list/process_inspect | 공개 여부, PID/검색/페이지/include_files | Linux 관찰, PID 재사용 검사, OS의 실제 접근 권한 |
| test_workspace_create/list/delete | 저장·원본 root, source_paths(명시한 `.` 포함) | Project 소유권, 자체 저장소 복사 금지, 특수 파일/링크 복사 금지, 실행 중 삭제 금지 |
| test_workspace_execute | argv/cwd/env, mount, network, 자원 값 | Bubblewrap 필수, 승인, 선택 공간의 격리 경계, 취소·재시작 시 명령 재실행 금지 |
| prompt_read | 공개 여부, 저장 Agent ID | 저장된 지침과 revision 조회; 지침 변경 권한을 만들지 않음 |
| web_search/fetch, browser_*, kernel_*, external_*_search, context_expand | 어댑터 제공·공개, URI scheme, scope, 어댑터별 허가와 제한 | 공통 입력 형태, 변경형 Tool 승인; 내부 의미는 명시한 adapter 소유 |

`enabled`, 기능 등록, Project `policies.tools.argument_constraints`의 fixed/bounded/selectable로
Tool 공개와 입력 제한을 결정한다. Backend에 임의 숫자 상한이나 고정 timeout을 추가하지 않았다.
stdin 미열기, 페이지 시작 0/첫 줄, 전체 검색 시 limit 없음은 호출 동작의 중립 상태이며
사용자 설정으로 자동 저장하지 않는다. 버퍼 단위와 파일 인코딩은 실행 한도가 아니다.
file_patch는 기존 텍스트 전체를 비교·교체하는 API다. 매우 큰 파일의 편집 알고리즘을
스트리밍 patch로 바꾸지는 않았으며, 입력/결과를 제한하지 않으면 호출자가 큰 값을 받을 수 있다.
