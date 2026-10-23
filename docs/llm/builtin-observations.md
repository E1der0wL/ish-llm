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

파일/프로세스/테스트 공간과 다른 등록 경로의 남은 전환은 [전수 기록](whole-ownership-verification.md)에서
계속 추적한다. 진단 Tool 전환을 내장 Tool 전체 완료로 해석하지 않는다.
