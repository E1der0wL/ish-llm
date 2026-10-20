# ish 작업 환경 연결

`config/environment.py`는 `prompt.context.environ`에서 `PWD`와 문자열 환경변수를
불변 사본으로 만듭니다. `HubInstallation`이 공급 함수를 연결하고 `LiveController`가
Enter 시점에 `_hub_environment` 요청 옵션을 구성합니다. 파일 시스템 조회나 전역
`os.chdir`/`os.environ` 변경을 UI 경로에서 수행하지 않습니다. 컨텍스트가 아직 없거나
PWD가 올바르지 않으면 초안을 보존하고 오류를 알립니다.

`backend/execution/environment.py`의 `EnvironmentEngine`은 등록 엔진에 위임하면서 요청 옵션에서
Hub 사본만 분리합니다. Loop/Graph/사용자 엔진의 고유 옵션·설정·추가 지시 계약은 그대로
위임합니다. 요청 사본은 llm의 기존 QUEUED/Run 저장 및 resume 경로로 보존됩니다.
실행과 `validate_resume` 모두 동일한 도구 바인딩을 구성하며, 환경 지문을 Tool contract
revision에 포함하여 다른 환경으로 기존 승인을 재사용하지 않습니다. 실행 준비는
백엔드 작업 스레드에서 처리합니다.

`HubBuiltinTools`는 프로젝트·cwd·env별 Toolkit을 분리하고 백엔드 종료 때 정리합니다.
`ShellProcessTools`가 ProcessTools를 상속하여 env 미지정 시의 기본값만 제공합니다.
파일/Git/셸 도구는 해당 Toolkit의 root/processes를 사용합니다. 별도로 가져온 Tool과
Agent 도구 선택 집합은 유지합니다. 테스트 공간의 격리/마운트/네트워크 정책도 유지합니다.
Hub는 shell 실행 prefix를 직접 고정해 Git prefix와 섞이지 않게 합니다.

완료 처리 프로세서는 모델에 cwd만 안내합니다. 전체 env는 모델 프롬프트나 별도 로그로
추가하지 않지만, 내구성 있는 재개를 위해 요청/Run 메타데이터에는 저장됩니다.
프로바이더 인증, 가져온 Python Tool의 전역 cwd/env, 외부 MCP 서버의 프로세스 환경은
이 바인딩의 대상이 아닙니다. 이들을 전역 변경하면 동시 세션이 서로 간섭하기 때문입니다.
명시적인 Tool `cwd`는 캡처한 root 아래 경로이며 `env`는 병합하지 않고 대체합니다.
`backend/tools/paths.py`는 파일 경로·프로세스 cwd·테스트 공간의 원본 경로가 root 내부
절대경로이면 실행 직전에 상대경로로 변환합니다. 승인에 표시하고 저장하는 원본 인자는
유지하며 경로 어댑터 revision도 승인 계약에 포함합니다. root 밖 경로, `..`, 심볼릭 링크는
기존 백엔드 검증으로 거부합니다. `workspace_id`가 있는 파일 호출은 격리 공간 기준의
기존 상대경로 규칙을 그대로 사용합니다.

main의 환경 옵션 없는 Run/직접 HubRuntime 생성에는 file_root fallback이 남아 있습니다.
[클린 리팩터링](../llm/clean-refactor.md)에서는 구 Run 복구용 fallback을 제거하고 새 요청에
실행 환경을 명시합니다. 직접 생성/독립 실행의 명시 file_root 선택은 구 저장 형식 지원과 구분합니다.
환경 Adapter의 실제 역할·승인 binding은 유지하며 다른 실행 경로로 승인 검사를 우회하지 않습니다.
데이터 저장 루트와 작업 디렉토리는 독립적입니다. 아직 제품 코드는 변경하지 않았습니다.

## 검증

2026-10-12 Linux 파일시스템 스냅샷과 Python 3.12.14에서 Hub 전체 247개가
통과했습니다 (`tests/llm/reports/ish-provider-hzfrjqst/hub.txt`). 집중 검증 47개는
`tests/llm/reports/ish-provider-fr08qpx_/focused.txt`, 전체 백엔드 1,383개는
`tests/llm/reports/ish-provider-sguxslu_/suite.txt`에서 통과했습니다.
요청 사본, 실제 ish 컨텍스트, 서로 다른 cwd/env, 명시 env 대체, Git·프로세스,
Graph 예약 요청, 절대경로 파일 생성·이동·격리 공간 복사와 재시작 후 승인 재개,
사용자 정의 Tool 보존을 확인했습니다. 승인 입력창의 작은 화면 배치, 생각 요약의
누적·재개·종료 표시, 지연 렌더링된 다중 행 입력창 위 마우스 휠도 검증했습니다.
해당 검증 시점의 Hub 코드·테스트는 스냅샷과 해시가 같으며 `llm/` 소스 변경은 없습니다.
