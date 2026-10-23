# 내장 Tool의 입력·조회 계약

BuiltinArguments는 각 내장 Tool 타입의 필드와 metadata에서 공개 Schema를 파생하고 호출
경계에서 알려진 필드만 읽는다. 입력을 수정하거나 생략된 값을 저장하지 않는다. 승인 전
ToolRegistry 검증과 실제 작업 경계의 기존 검증은 유지한다. 다른 경계의 필드를 새 정책으로
해석하지 않는다. 이 Reader는 영속 기록의 누락 복원이나 범용 SDK 변환에 사용하지 않는다.

DiagnosticTools의 세 진단 Tool은 이름·설명·인자·제약을 diagnostic_values의 타입에서 등록한다.
내부에서는 ProcessStat와 시스템·페이지·상세 관측 타입을 사용하고 공개 응답 경계에서 기존
딕셔너리 모양으로 직렬화한다. /proc 경로·단위·encoding·FD 대응은 진단 Owner가 선언한다.
ProcessStat의 커널 필드 및 PID/start identity 의미를 다시 선언하거나 부분 해석하지 않는다.

실패하거나 요청하지 않은 선택적 관측은 생략하며 실패 이유는 unavailable에 기록한다.
관측한 빈 argv/files와 읽지 못한 필드를 구분한다. 목록의 페이지 종료는 ProcessPage의 기본
next_pid=None이다. 명시 expected_start_ticks=null은 생략으로 바꾸지 않고 기존대로 거부한다.
관측 전후 PID identity가 바뀌면 결과를 반환하지 않는다. 관측 결과는 실행 권한이나 과거 사건의
원본이 아니다. 입력 원본·Schema 사본·독립 wire·페이지·읽기 실패·PID 경합을 검증한다.

## 파일 Tool

FileLimits·FilePathArguments·FileTraversalArguments가 공통 byte 한도·필수 경로·탐색 필드를
선언한다. 여덟 작업별 입력 타입에서 이름·설명·추가 필드·필수 여부를 소유하며 같은 선언으로
카탈로그 Schema와 내부 입력 조회를 연결한다. 검색의 required 배열 순서도 기존대로 보존한다.
FileReadArguments는 줄 시작·개수·tail·기대 digest의 생략과 명시값을 구분한다. 입력 원본에
기본값을 쓰지 않고 파일을 읽은 후 기존 범위 충돌과 버전 검사를 수행한다.

FileTools는 실제 경로·파일 한도·잠금·읽기/쓰기·fsync/원자적 replace·휴지통 이동을 소유한다.
휴지통 식별자는 FileTrashIdentifier의 같은 pattern으로 Schema와 실제 복원을 검사한다.
등록의 file_read 재시도 필드는 FileReadArguments.path에서 파생한다. 읽기/목록/검색/변경 결과는
file_values의 타입으로 조립하고 공개 경계에서 직렬화한다. 사본은 권한이나 저장 원본이 아니다.

파일 경로/링크/예약 경로 보호·정확한 최신 hash·단일 치환·덮어쓰기 금지·byte 한도·취소 drain과
원본 또는 완전한 새 상태 계약을 유지한다. 실제 원자적 교체 실패에서도 원본 bytes와 mode를
보존하고 임시 파일을 회수한다. Workspace 선택은 기존 TestWorkspaces 경계가 소유한다.

각 소단계의 상태와 미전환 소비자는 [전수 기록](whole-ownership-verification.md)에서 추적한다.
개별 입력·파일 전환을 전체 저장소 완료로 해석하지 않는다.

## 프로세스 Tool

ProcessLimits·ProcessInvocation·작업별 입력 타입이 argv/환경/한도·조회·입력·shell/check 인자를
선언한다. 공개 Schema와 typed 조회를 같은 필드에서 파생한다. shell/check의 내부 argv 요청은
명시된 override만 전달하고 원본 요청·승인 binding을 수정하지 않는다. timeout/output 한도의
생략과 명시 null은 기존 실행 의미를 유지하며 공개 Schema는 그대로다.

ProcessEnvironment는 자식 환경의 검증·사본·공개 Schema를 소유한다. 생략은 각 실행 경계의
기존 환경 선택, 명시 {}는 빈 환경이다. 부모와 병합하거나 key/value를 문자열로 변환하지 않는다.
기존 copy_env 공개 함수 경로는 같은 Owner 메서드의 직접 참조다. 격리 실행기의 나머지 코드와
spawn/승인/namespace 설정 순서는 변경하지 않는다.

ProcessSession은 해당 Toolkit 수명 동안의 실제 process·task·stdout/stderr 버퍼·시작/종료·상태와
입력 잠금을 소유한다. status는 process_values.ProcessStatus의 값이며 새로운 저장 기록을 만들지
않는다. 모니터의 상태 변경, spawn 중 취소, monitor 시작 전 종료, stdin drain/close, 자식 그룹 회수,
stdout/stderr 합산 byte 상한과 명시 timeout의 기존 순서를 유지한다.

execute는 기존 공개 start/cancel 재정의 경로를 사용한다. Hub가 저장한 셸 환경의 주입을
내부 메서드 직접 호출로 건너뛰지 않는다. ProcessSnapshot이 start 응답의 identity 투영을 소유한다.
ProcessSnapshot·ProcessOutput·ProcessWritten이 공개 결과를 직렬화한다. OutputPage는 완성된
Unicode 문자 단위의 cursor를 계산한다. 미완성 UTF-8은 실행 중에는 소비하지 않고 종료/잘림
확정 시 기존 replacement 규칙을 쓴다. stdout/stderr는 각각의 offset/remaining을 가지며 전역 사건
순서로 합치지 않는다. 인자 null/0/bool은 기존 규칙대로 거부하고 새로운 기본 한도는 만들지 않는다.

테스트 내부의 process/task/buffer 접근은 타입 속성으로 갱신하되 독립 wire·실제 입력/출력·취소·
효과 Assertion은 유지한다. 환경 값/오류와 전체 등록 정의/계약/binding의 이전·현재 동등성,
실제 파일/격리/취소·필수 안전성 및 Hub 소비자 검증을 거친다. 공유 환경 Owner 변경은 다음
현재 통합 Level 3의 필수 대상이며 소단계 결과를 원격 CI 통과로 승계하지 않는다.

## 테스트 공간과 등록

WorkspaceSelection과 작업별 입력 타입이 선택 ID·생성 파일·명령·명시 환경의 단일 선언이다.
파일 Schema의 선택 공간 필드도 같은 선언에서 파생한다. WorkspaceSettings의 Host 값은
WorkspaceRuntime이 기존 ProcessToolRunner 검증과 실행에 전달하고 WorkspaceBinding은
분리된 사본으로 승인 지문에 투영한다. 외부 실행 설정 조회는 기존 JSON 모양의 사본이다.
원본 입력·저장 설정에 생략된 env나 기본 제한을 추가하지 않으며 env 지정 시 전체 대체한다.

ProcessCommand가 격리 명령의 절대 executable·문자열 argv·NUL 검증을 소유한다. Hub의
실행 전 피드백도 이 판별을 사용한다. Hub의 status/executed/error/guidance는 별도 UI 응답
선언이며 재시도·승인 권한이 아니다. 실행기는 동일한 명령 검증을 반드시 다시 통과한다.
ProcessResult는 일반 명령의 종료 코드·stdout/stderr decoding과 공개 결과를 소유하고
WorkspaceExecutionResult가 공간 identity를 함께 직렬화한다. JSON Tool 출력은 별도 계약이다.

BuiltinTools는 해당 입력 Owner의 이름·설명·Schema를 조립하며 GitCommands와 Host 셸 prefix를
분리한다. Git 등록은 선택한 shell argv를 바꾸지 못한다. checks는 ConfiguredChecks가 기존
명령 검증을 수행한다. 외부 Host 어댑터의 요청 Schema·효과 종류는 HostAdapters가 선언하고
실제 payload 의미와 구현은 주입된 Host가 소유한다. Backend는 opaque options를 해석하거나
생략값을 채우지 않는다. 로컬 RAG 이름의 거절과 명시적 외부 이름은 기존대로 유지한다.

BuiltinExecutionBinding은 root/shell/checks/선택 공간의 사본을 소유한다. Component는
bound revision API를 사용하고 환경 필드를 다시 조립하지 않는다. 승인 대상 효과·격리·
재시도 가능 필드·원래 revision은 유지하며 환경/계약이 바뀌면 기존 승인을 재사용하지 않는다.
독립 wire·이전/현재 정의/계약/지문 비교와 실제 셸/Git 동시 등록·환경·승인/재개 검증을 수행한다.
