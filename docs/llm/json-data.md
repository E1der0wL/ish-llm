# JSON 객체 검증의 책임

`llm/core/json_data.py`의 `JsonObject.validate`가 열린 JSON 객체의 문법 검증을 소유한다.
최상위 dict, 문자열 key, 재귀적인 JSON 값, 유한 숫자를 검사한다. 입력을 변경하거나 숫자·key를
변환하지 않는다. 알 수 없는 필드를 버리거나 생략한 필드의 값을 만들지 않는다.

ProjectConfig는 Project의 설정 구조와 정책을 계속 검증한다. 공개
`ProjectConfig.validate_json`은 같은 JsonObject 검증기로 위임한다. 다른 도메인은 JSON 문법
검사를 위해 ProjectConfig에 의존하지 않고 JsonObject를 직접 사용한다. 두 구현이나 fallback
Reader는 없다. 값의 형태가 JSON이라는 사실은 승인·상태·버전·identity·revision·예산·효과의
유효성을 증명하지 않는다. 각 Record와 실행 경계의 기존 의미 검증을 별도로 유지한다.

격리 Tool worker는 자신의 import 경로와 protocol FD를 설정한 뒤 공통 검증기를 로드한다.
기존 지연 import 위치를 유지하며 문법 검증 이동으로 worker 실행 환경이나 저장 형식을 바꾸지 않는다.
이 변경의 AST 대조·실제 worker·실패 주입·전체 게이트 증거는
[전수 소유권 검증 기록](whole-ownership-verification.md)에 남긴다.
