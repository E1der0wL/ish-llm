# 응답 안정성과 마우스 스크롤

## 2026-10-12 WSL 진단

최근 공급자 로그는 Gemini의 HTTP 503과 429를 기록했습니다. 두 경우 모두 해당
Completion의 `chunks_received=0`으로, 응답 수신 전 실패였습니다. 사용 중인 프로젝트에는
`parameters.engines.loop.policy.provider`가 없어서 첫 오류가 Run 실패로 이어졌습니다.
429 상세에는 쿼터 종류나 Retry-After가 없어 단기 요청 제한과 장기 쿼터 소진을 구분할 수 없습니다.

현재 활성 WSL 프로젝트는 원본 `project.json`을 `.hub/backups/reliability-*`에 보관한 뒤
공개 `project.asave()` API와 config version 검증으로 다음 정책만 추가했습니다.
설정 창에서 `config.parameters.engines.loop.policy.provider`를 검색해 조절할 수 있습니다.

```json
{"max_attempts": 4, "delay_seconds": 10, "max_delay_seconds": 60}
```

첫 호출을 포함해 최대 4회, 재시도 전 10·20·40초를 기다립니다. 새 Hub 프로젝트에는
Loop와 제목 생성에 각각 이 정책을 저장합니다. 기존 프로젝트를 열 때 자동으로 덮어쓰지
않으며, 생성 시 명시한 provider 정책도 보존합니다. Graph의 Agent가 Loop를 사용하면
해당 Loop 설정을 따릅니다. 다른 엔진·RAG 모델 호출에는 이 정책을 이식하지 않습니다.

재시도 자체는 llm의 기존 공개 기능을 사용합니다. 응답을 받기 전의 일시적 오류만 대상이며,
본문·생각·도구 호출 델타를 받은 뒤에는 자동 재생하지 않습니다. 인증·잘못된 요청·사용자
취소도 재시도하지 않습니다. 이전에 완료한 Tool 결과는 유지합니다. 명시한 SDK 재시도가
있으면 llm이 중복 재시도를 방지하므로 두 계층의 횟수가 곱해지지 않습니다.
반복되는 쿼터 소진이나 공급자 장애까지 성공을 보장하지는 않습니다.

실제 Gemini에 짧은 응답 요청을 한 번 보냈을 때 첫 시도 실패 후 두 번째에서 `OK`를 받았고,
전체 19.82초가 걸렸습니다. 이는 한 번의 연결 확인이며 장기 성공률 측정은 아닙니다.
기존 대화·도구 작업은 재실행하지 않았습니다.

## 재시도·승인 UI

화면 전용 상태는 표시 모델이 선언한다. `ChatMessage.PREPARING`,
`StepSummary.WAITING_APPROVAL/RETRYING`, `SessionSummary.WAITING_INPUT/WAITING_APPROVAL`은
기존 화면 문자열이며 영속 상태·승인·재실행 권한을 생성하지 않는다. Run·Message·Step·Session의
영속 상태를 표시할 때는 해당 backend 상태 선언을 참조한다. Tool countdown은 정책의 명시된
대기를 보존하고 None에만 추가 대기 없음을 적용하며, 실행 횟수·대기·기한 판단은 llm이 소유한다.

기본 `HubLoopEngine`은 `RetryObservationMixin`으로 기존 Loop를 관찰합니다.
`BackendServices.observability_sink`의 확정된 `providers/retries` 사실만 사용하므로
인증 오류·부분 출력·최종 실패를 재시도 대기로 잘못 표시하지 않습니다. 재시도 실행·상한·
SDK 위임 판단은 그대로 llm이 소유합니다. Graph의 기본 Loop Agent에도 적용됩니다.
사용자 지정 엔진은 이 mixin을 명시적으로 조합해야 관찰할 수 있으며, SDK 내부의 숨은
재시도는 횟수나 시간을 추측하지 않습니다.

입력창 위 한 상태 줄에서 `재시도 대기 중 · 1/4회 시도 완료 · 2번째 시도까지 10초`,
`재시도 중 · 2/4번째 시도`를 표시합니다. countdown은 monotonic 시각으로 UI에서 계산하므로
초마다 파일을 다시 읽을 필요가 없습니다. 호출 종료·취소 시 관찰값을 버립니다.

승인 요청은 기본 출력에 도구·위험도·대상·인자 크기만 요약합니다. `Ctrl+O`는 원본 요청 전체를
별도 읽기 팝업으로 열며 기존 이동키·Enter/ESC 닫기를 사용합니다. 원본 인자와 영속 승인은
바꾸지 않습니다. 승인 Enter는 즉시 입력을 소비하고 `답변 전달 중 · 작업 제목`을 표시합니다.
Hub의 화면 조회 lock을 기다리지 않고 공개 응답 API를 호출하며, 중복 `resume_plan()` 미리보기는
제거했습니다. 실제 `resume()`의 체크포인트·승인·중복 실행 검증과 durable queue는 유지합니다.

## 잘못된 샌드박스 명령

별도 실패는 `test_workspace_execute`에 절대 경로가 아닌 실행 파일을 전달한 경우였습니다.
Hub의 `HubTestWorkspaces`는 명령 실행 전에 이 인자 오류만 확인하고 `executed: false`와
수정 방법을 모델에 반환합니다. 모델이 인자를 수정하면 다시 승인 절차를 거쳐 실행됩니다.
실행 파일을 임의로 바꾸거나 호스트 셸로 우회하지 않습니다. 실제 샌드박스·실행·파일 오류는
기존대로 전달하며, 권한·Bubblewrap 격리·중단 처리는 llm 구현을 그대로 사용합니다.

## 마우스

ish에 설치한 Hub가 표시되는 동안 PTK 마우스 입력을 활성화합니다. 최소화하면 셸의 원래
설정을 사용하고 플러그인을 해제하면 원래 filter를 복원합니다. `WheelRoutingContainer`는
대화 페이지의 휠 입력을 출력 영역으로 전달합니다. 키보드 포커스와 초안은 유지하며,
설정·태그바·모달·자동완성 팝업은 자체 입력 처리를 유지합니다.

## 검증

- Linux Python 3.12.14: 집중 12개, Hub 전체 215개 통과.
  `tests/llm/reports/ish-provider-rs6w06fc/{focused,hub}.txt`.
- llm 전체 1,374개 통과: `tests/llm/reports/ish-provider-otek1adf/suite.txt`.
  해당 실행의 이전 Hub 검사 사본은 중단하고 위 최신 검사로 대체했습니다.
- 503·429 후 정상 완료, 완료 Tool 미재실행, 재시도 상한, 부분 출력·인증 오류 미재시도,
  대기 중 취소, 실제 Bubblewrap에서 잘못된 명령 수정 후 작업 완료를 검증했습니다.
- 실제 PTK SGR 마우스 입력으로 출력·입력창 위 휠 동작과 초안·포커스·셸 모드 복원을 검증했습니다.

### 2026-10-12 재시도·승인 UI 검증

- Linux Python 3.12.14: 집중 31개, Hub 전체 224개 통과.
  `tests/llm/reports/ish-provider-kjca30dv/{focused,hub}.txt`.
- llm 전체 1,374개 통과: `tests/llm/reports/ish-provider-kqalhgpj/suite.txt`.
  이 사본의 집중 검사는 이전 상태 문구 기대값 때문에 실패했으며 최신 집중 검사로 대체했습니다.
  같은 사본에서 시작한 Hub 전체 검사는 중단하고 위 최신 코드 검사로 대체했습니다.
- 실제 재시도 사실, 정책의 횟수·대기 상한, 로컬 countdown, SDK 위임·부분 출력·인증·기한
  오류의 잘못된 재시도 표시 방지, 병렬 호출·취소 시 관찰값 정리를 확인했습니다.
- 실제 PTK Ctrl+O/ESC로 승인 상세를 열고 닫은 뒤 원래 입력창에서 승인하여 같은 대화를
  이어가는 경로, 즉시 입력 소비·전달 중 표시·중복 Enter 방지와 대기 UI lock 우회를 확인했습니다.
- Python 소스 334개의 최종 검사 사본 해시를 확인하고 WSL Hub 파일 14개를 반영했습니다.
  설치 경로 import 확인 완료. 원본 백업은
  `~/.ish/hub-workspace/.hub/plugin-backups/20261011T215354Z-retry-ui/`입니다.
  실행 중인 ish는 재시작하지 않았으며 새 코드는 다음 시작부터 적용됩니다. llm/ 변경은 없습니다.
