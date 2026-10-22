# 표준 Agent Skills와 Skills 운영 기록

이 문서는 SKILL.md 단일 원본 전환의 현행 계약이다. 이전 skill.json 저장 계약을 대체한다.
다른 Component의 운영 저장 구조는 변경하지 않는다. 구현·검증 상태는
[전환 검증 기록](agent-skills-verification.md)에서 별도로 관리한다.

기준은 [Agent Skills Specification](https://agentskills.io/specification)과
[공식 원문 69ef37e](https://github.com/agentskills/agentskills/blob/69ef37e9424c0a7ea9dd2293b559e43ec8176379/docs/specification.mdx)다.

## 단일 정의와 Owner

`skills/<name>/SKILL.md`의 YAML frontmatter와 Markdown 본문이 유일한 정의 원본이다.
name/description은 필수이며 name과 부모 디렉터리가 일치한다. license/compatibility/metadata/
allowed-tools는 표준의 선택 필드다. metadata는 문자열 키와 문자열 값만 허용한다.
본문의 길이 권고를 실행 제한으로 추가하지 않는다. 빈 본문도 표준상 유효하다.
YAML의 중복 키·잘못된 타입·미지원 필드는 원본을 변경하지 않고 거부한다.

공개 CRUD 입력은 `markdown`에 SKILL.md 원문을 전달한다. 조회는 이 원문과 운영 정보의
메모리 Projection을 반환한다. 파싱된 정의를 JSON 파일로 재저장하지 않는다.
instructions/title/tags 형태의 구 입력은 지원하지 않는다. 표준 밖 필드를 metadata 문자열에
자동 인코딩하거나 name/description의 생략을 추측하여 보완하지 않는다.
SkillDocument/SkillName이 표준 해석을, SkillRecord가 운영 정보와 Projection을,
SkillStorage가 디렉터리·필수 목록·바이트 저장을 소유한다. 소비자는 Owner API를 사용한다.

## 디렉터리와 운영 증거

```text
skills/
  <name>/SKILL.md
  <name>/scripts/...
  <name>/references/...
  <name>/assets/...
  _state/manifest.json
  _state/records/<name>.json
  _state/usage/...
```

표준 패키지에는 다른 자료 파일·디렉터리도 허용한다. `_state`는 Skill 탐색 대상이 아니다.
운영 기록은 Skill별로 나누며 정의 본문을 포함하지 않는다. 원문 digest, 파일 inventory와
권한·디렉터리 목록, Data/Tool 고정 참조, 불변 fork 계보와 명시적 출처를 기록한다.
목록과 각 기록은 Project·Component identity와 digest로 연결하며 필수 기록 소실은 빈 값으로
복구하지 않는다. Component 사용량 영수증은 기존 ModelUsageRecord 계약을 유지한다.

자료는 package root 기준 상대 경로다. generic file API로 SKILL.md를 덮어쓰거나 지울 수 없다.
링크·특수 파일·경로 이탈을 거부하고 원본 파일 바이트와 일반 권한을 보존한다.
외부 편집으로 관리 증거와 달라진 저장소는 차단한다. 등록된 원본을 자동 재스캔해 채택하지 않는다.
Markdown 링크 자체를 중복 관계 원장으로 저장하지 않는다. 명시적 resources는 기존의 로컬
파일·URI·고정 버전 Data/Tool 참조이며 Tool 권한을 생성하지 않는다.

## 수명과 소비

import/export는 표준 디렉터리를 그대로 다루고 운영 기록을 Skill 패키지에 섞지 않는다.
표준 import는 Project별 연결·계보를 자동 승계하지 않는다. Project backup은 운영 기록까지
보존하며 clone/fork는 기존 Owner의 참조·부모 버전·파일 보존 계약을 따른다.
fork의 새 name은 대상 디렉터리와 일치해야 한다. lineage는 생성 후 변경하지 않는다.

정의·파일·운영 기록 변경은 기존 workspace transaction으로 묶는다. snapshot revision은 원문과
실행 관련 운영 정보를 포함하며 파일 변경도 CAS·실행 binding에 반영한다. 사용량 영수증의
추가는 Skill 정의 revision을 바꾸지 않는다. Agent 참조는 Agent 정의만 원본으로 유지한다.
선택 해제된 Component의 참조·사용량·부모 삭제 보호도 유지한다.

목록은 name/description 요약, 선택한 Skill 읽기는 원문, 자료는 필요할 때 조회한다.
Agent의 명시적 resources.skills는 선택 본문만 주입한다. allowed-tools와 scripts는 저장만으로
승인·허용 목록·실행 권한을 추가하지 않는다. 원격 URI도 자동 접근하지 않는다.
Hub는 표준 디렉터리 import 및 Markdown 편집에 Skill 공개 API를 사용한다.
Refinement의 승인 대상과 rollback 증거에는 변경된 원문·관련 운영 정보의 버전을 사용한다.
Run/Step/Checkpoint/승인/효과 원장은 기존 Owner에 유지한다. 필수 과거 snapshot은 현재 정의의
편집 가능한 중복 원본이 아니라 당시 승인·실행·복구의 증거다.

기존 skill.json과 이전 records 구조는 읽기·자동 변환·자동 삭제하지 않는다. 기존 버전에서
원본을 내보내고 표준 파일을 명시적으로 작성해 새 API로 등록한다. 기존 실행을 새 형식의
binding으로 재해석하거나 필수 증거를 재발급하지 않는다.

## 검증 게이트

[Level 1–3 정책](verification-policy.md)을 적용한다. AS1은 표준 Reader/Writer·실제 디렉터리·
CAS·손상/장애 주입, AS2는 Agent/Refinement/Hub와 참조·삭제·재개 소비자,
AS3는 현재 통합 소스의 전체 LLM/Hub·S1–S7·R3 mutation·문서·실행 환경 검증이다.
전체 성공 후 작업 브랜치에 Push하고 정확한 SHA의 필수 원격 CI를 확인한다.
테스트 누락·중복·Skip·expected failure·unexpected success를 성공으로 처리하지 않는다.
