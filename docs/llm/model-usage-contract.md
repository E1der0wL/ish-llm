# 모델 사용량의 선언·예약·영수증

[기존 소유권 계약](contract-ownership.md)과 [사용량 전환 기록](whole-ownership-verification.md)의
현재 구현 연결을 보강한다. 저장 형식·한도·기간·삭제 보호와 실제 효과 검증 기준은 유지한다.

`UsagePolicy`는 원본 설정을 변경하지 않는 실행 관측이며 `UsageLimit`이 한도 부재의 의미를
소유한다. 소유 Run이 없는 Component 호출의 `without_run_limits()`는 기존과 같이 Run 한도만
제외한 사본을 반환한다. Project 한도·기간·counter는 그대로다. Run에 속한 호출은 저장된
Run 정책을 사용하며 Resume·삭제·재시작을 이유로 이 경로를 적용하거나 한도를 초기화하지 않는다.

`TokenUsage.valid_count`가 정규 토큰 수와 예약 수의 정확한 nonnegative int 판별을 소유한다.
bool·float·문자열·음수는 허용하지 않는다. `TokenReservation.count`는 호출별 선택한 counter를
기존 thread 경계에서 한 번 실행하고 잘못된 결과를 `usage_configuration`으로 거절한다.
Run의 UsageScope와 Component 관찰자는 같은 검증을 사용한다. 예약이 필요하지 않은 호출에
계수 작업을 추가하지 않는다. 누락 사용량은 기존 예약을 유지하며 unknown을 0으로 복원하지 않는다.

`ModelUsageRecord`의 필드 선언에는 예약 이후 변경할 수 없는 필드를 함께 표시한다. Reader의
필수 필드 집합과 시작→종료 변경 검증이 같은 필드 선언을 사용한다. identity·모델·operation·
원래 시각·예약량은 종료 시 바뀌지 않는다. 기존 독립 기대 필드 목록과 손상·누락·전이 시험을
유지하여 잘못된 metadata 선언을 시험도 함께 수용하지 않게 한다.

`ModelUsageRecord.filename`이 해당 저장 형식의 파일명을 소유한다. 기본 Component 저장과
manifest의 예약 이름 검사는 이 API를 사용한다. `ModelUsageManifest`는 필수 inventory·현재
버전·소유자·revision을 검증하고 records 사본을 읽기 전용으로 보관한다. 필수 기록·루트·manifest
누락을 초기화하거나 빈 목록으로 복구하지 않는다. 정의·운영 기록·Run 실행 기록은 중복 저장하지 않는다.

사용량 집계는 삭제된 Session의 증거도 포함한다. runtime의 `INCLUDE_DELETED_EVIDENCE`를
Run 예약과 Project 사용량 보호 경로가 공유한다. Component 및 Repository의 선택적 조회
hook은 해당 Owner 메서드 선언을 참조한다. 주입 저장소의 원래 projection 경로와 검증된 Run
영수증 경로를 유지하며 원본 Reader를 건너뛰지 않는다.

예약 검사와 저장의 기존 workspace 잠금·트랜잭션 순서는 유지한다. 실제 호출 전에 계수 실패가
발생하면 효과가 없고 영수증 파일은 바뀌지 않는다. 실행 이후 실패·취소·영수증 손실 시 기존
예약·호출 횟수·불확실성은 그대로 보호한다. [검증 정책](verification-policy.md)의 집중/통합
안전성, R3 mutation, 전체 게이트와 정확한 SHA CI를 따른다.
