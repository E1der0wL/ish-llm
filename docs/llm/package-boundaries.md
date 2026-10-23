# Tool 패키지 저장·공용 dependency 경계

ToolPaths가 패키지 경로·파일 모드·manifest·정규 파일/허용 파일 집합·진단을 선언한다.
`checked`는 기존 부모 경로 거부와 reject_links를 그대로 호출한다. inventory는 계속 manifest와
실제 파일 집합·digest·정의 의미를 검증한 뒤 typed ToolPackageSource를 반환한다. 파일 저장의
prepare_replace → 임시 bytes 쓰기 → flush/fsync → replace → 디렉터리 sync와 manifest 커밋
순서는 유지한다. 삭제/중단 원본 보호와 실패 재현 주입점을 바꾸지 않는다.

ToolDependencies가 설치된 distribution 조회·PEP 508 dependency 탐색·이미 로드된 Host 경로와
명시적 Host 경로 해석을 소유한다. packaging의 marker wire 필드 extra는 RequirementEnvironment의
선언에서 직렬화한다. distribution의 Name과 Host 모듈/설정 필드는 해당 외부 경계 안에서만
해석한다. extras·marker·정규 이름·version 충돌 판단은 기존 packaging 구현을 재사용한다.

load_tool은 현재 패키지를 읽고 dependency 가용성을 확인한 다음 worker 검사를 조율한다.
생략된 prepare는 기존대로 false이며 자동 설치하지 않는다. 명시적 prepare만 ish installer를
호출하고 기존 설치 전체를 pin한 constraints·같은 PLUGIN_LIB_DIR·설치 후 재검증을 유지한다.
기존 _installed/_missing/_host_target/_checked 이름은 단일 구현의 직접 별칭이며, 별도 Reader나
레거시 실행 경로가 아니다. 기존 장애 주입과 dependency 검사 시험을 계속 사용할 수 있다.

실행 문맥 안에서 prepare를 금지하는 계약은 ToolCallContext/UsageScope가 소유한다. 그 금지
시험은 이전 조회 함수의 mock 대신 실제 ToolCallContext.scope로 실행 중 상태를 만들고 같은
RuntimeError Assertion을 검증한다. 실제 소스 import 파일 효과가 0인지 확인하는 시험도 유지한다.

경로·dependency 선언의 정리는 저장 형식·SDK 재시도·권한·승인·예산·Tool 효과·재개 계약을
변경하지 않는다. [검증 기록](whole-ownership-verification.md)에 기존 실패와 수정 후 실제
결과를 분리하여 보존한다.
