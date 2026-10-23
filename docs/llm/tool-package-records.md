# Python Tool 패키지 정의와 manifest의 단일 Owner

`llm/components/tools/package_values.py`의 ToolPackageSource가 source/requirements의 현행
정의를 소유한다. 원본 Python과 선택적인 requirements.txt가 그대로 정규 파일이다. 내부 JSON
정의 파일을 추가하지 않는다. 공개 CRUD의 JSON 값은 이 Owner의 Reader/Writer를 거친다.
Worker·dependency 준비·Hub 설명은 속성으로 소비하고 필드를 다시 해석하지 않는다.

Source Reader는 source 필수·비어 있지 않은 UTF-8 문자열, 선택 requirements 문자열,
미지 필드 거부를 유지한다. requirements 생략과 빈 문자열은 다르며, 명시적 null은 계속
거부한다. 내부의 None은 파일 부재만 나타내고 Writer는 해당 필드를 생략한다. 원본 줄바꿈·
주석·Unicode·명시적인 빈 파일을 정규화하거나 덮어쓰지 않는다. ToolRequirements가 기존
PEP 508 파싱과 옵션/URL 거부를 소유하며 설치·실행 정책을 만들지 않는다.

ToolPackageDigests는 원본 내용의 checksum 선언을 소유한다. checksum 계산은 정의가
유효하다는 증거가 아니다. 장애 주입에서 잘못된 requirements에 일치하는 manifest를 구성한
뒤에도 실제 Source Reader가 실패해야 한다. fingerprints API에 Source 검증을 잘못 결합해
이 재현을 막았던 변경은 되돌려 책임을 분리했다. 기존 실패 Assertion은 유지한다.

ToolPackageManifest가 필수 header·정확한 정수 version·Project/component identity·digest
필드를 검증한다. ToolPaths는 선언된 파일과 실제 항목, 링크, 파일 종류, 원본 checksum을
대조하고 Source 의미를 검증한다. manifest나 파일이 없다고 빈 저장소를 만들지 않는다.
manifest version과 ToolPaths의 공개 version 참조는 같은 선언을 사용한다.

Repository의 inventory는 typed manifest와 정의를 내부에 유지하고 공개 Component CRUD에서
JSON으로 내보낸다. 저장 순서·원자적 대체·공통 Component 트랜잭션·CAS·삭제 보호는 바꾸지
않는다. 소스/requirements 파일명은 ToolPaths를 사용하며 dependency installer와 worker의
실행 권한은 기존 Owner에 남는다. 과거 형식 Reader나 별도 호환 실행 경로를 추가하지 않는다.
