# 프로그래밍·컴퓨터 진단과 Skill 탐색

실제 LoopEngine/ToolExecutor로 Skill 목록을 조회하고 필요한 지침을 읽어 작업하는 예제다.
Skill을 읽었다는 것과 실제 검증 통과는 구분한다. 작은 모델의 성공률은 모델별 평가가 필요하다.

## 실행

Linux Python 3.12.14에서 [설정 예제](developer_assistant.config.example.json)를 복사하고
실제 completion.model과 필요한 api_base/api_key를 지정한다. builtin은 호스트의 기능 제공,
project_config.parameters["components"].computer.enabled는 Project의 선택이다.
예제는 파일 변경과 셸 실행을 제공한다. 읽기 전용 작업에는 해당 이름을 enabled에서 제외한다.

```sh
python -m examples.llm.developer_assistant \
  --config /path/to/local-config.json \
  --workdir /path/to/code \
  --workspace /path/to/llm-workspace \
  --install-guides \
  '소스를 읽고 요청한 동작을 구현한 뒤 테스트 결과를 알려줘'
```

workdir은 존재해야 하고 workspace는 그 밖에 둔다. --install-guides는 code-change와
computer-diagnosis 예제 지침을 생성한다. 이미 있는 ID를 덮어쓰지 않으며 기본 실행에서
지침을 자동 설치하지 않는다. 이후 --project-id로 저장된 설정과 Skill을 재사용한다.
기존 Project에는 config의 project_config를 다시 적용하지 않는다. 호스트 카탈로그는 다시
연결하므로 기존 enabled 이름에 해당하는 기능을 계속 제공해야 한다. 각 실행은 새 Session이며,
같은 대화를 이어가려면 서비스의 Session load API를 사용한다.

## 흐름

```text
Loop completion
  → skill_list(query?, after?, limit?)
  → skill_read(identifier, expected_revision?)
  → 소스/로그 조회·프로세스 관찰
  → 명시적으로 허용된 수정/재현 실행
  → 검증 결과 확인
  → 최종 응답
```

skill_list에 인자가 없으면 전체 요약 목록을 읽는다. 설명·태그를 작업 언어로 작성하면
모델이 선택하기 쉽다. query는 의미 검색이 아니라 ID/제목/설명/태그의 단어 검색이다.
일치하지 않으면 query를 바꾸거나 전체 요약을 조회한다. 긴 본문은 선택한 Skill만 읽는다.
Graph Agent에서는 tools 허용 목록에 skill_list/skill_read를 넣어야 한다. 특정 지침의 고정
주입은 기존 resources.skills로 유지한다. 지침은 실행 정책이나 승인을 변경하지 않는다.

## 관찰 범위와 검증

process_start(stdin=true)와 process_write는 Toolkit이 만든 pipe 프로세스에만 입력한다.
기존 ish 셸을 조작하거나 PTY를 에뮬레이트하지 않는다. process_output의 next offset으로
이어 읽고 file_read(tail_lines=...)와 file_search로 기존 로그를 조회한다.
process_inspect는 PID·표준 I/O·터미널 관찰이며 prompt-toolkit 내부 포커스 정보가 아니다.
입력 라우팅의 과거 원인을 확정하려면 호스트의 이벤트 계측 기록이 필요하다.

자동 테스트는 scripted completion으로 Skill 선택을 결정하고 실제 파일 수정·Python 검증
프로세스·Run/Step 저장·재열기를 확인한다. 모델의 판단 품질을 실측한 것은 아니다.
공통 승인·취소·재개 경계를 사용하며 별도 실행 상태 저장소를 만들지 않는다.
