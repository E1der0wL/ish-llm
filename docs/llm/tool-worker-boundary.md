# Project Tool worker 시작·전송 경계

ProjectToolWorker가 child의 main 함수·모듈 identity·compile mode·표준 출력 FD와 protocol mode,
신뢰한 plugin root 및 공유 라이브러리의 argv/sys.path 위치를 선언한다. llm import 전 bootstrap
순서와 source/requirements·descriptor 재검사 시점을 유지한다. parent/child가 같은 File digest
의미를 중복 구현하지 않으며 fingerprint는 ToolPackageDigests.digest의 직접 별칭이다.

ToolWorkerWire가 응답의 UTF-8·ASCII escape·비유한 값 거부와 descriptor의 정렬된 JSON을 소유한다.
부모가 ProcessLaunch를 보내는 바깥 전송은 기존 ProcessGate의 인코딩 선언을 사용한다. 두
프로토콜의 의미를 합치지 않는다. ToolCall은 호출 출처 snapshot의 필드 선언과 to_dict/from_dict
변환을 소유하며 Project worker와 명시적 ProcessToolRunner는 이 API를 사용한다.

프로토콜 8 MiB/capture 1 MiB·읽기 크기·process group·poll 간격·단일 inspection thread는 현재
process 모듈의 선언으로 유지한다. 부모 환경을 복사하지 않고 -I·최소 locale 환경을 사용한다.
출력을 잘라 성공시키지 않으며 취소·정상 종료·실패 모두 자식 그룹과 pipe를 회수한다. 새로운
기한·재시도·격리 기본값을 추가하지 않는다. 알 수 없는 Tool 실패의 retryable 값은 기존
ToolExecutionError의 false 기본값을 사용한다.

선언 이동 전후 JSON byte/canonical text·digest 및 실패 동등성과 실제 fresh worker·출력 분리·
승인 전 spawn 금지·지문 변경 거부·취소/timeout/shutdown 회수·영수증/예산 안전성을 검증한다.
[누적 기록](whole-ownership-verification.md)에 소스별 결과와 전체 게이트를 구분한다.

## 종료 확인과 중첩 선택의 보강

부모의 wait 완료만으로 자식 종료를 확정하지 않는다. ProcessGroupExit는 SIGKILL 전후의 같은
프로세스 그룹을 관측하고, 관측한 PID/start_ticks identity가 유지되는 비종료 프로세스가 있으면
기다린다. PID 재사용과 그룹 이탈을 이전 프로세스로 해석하지 않는다. zombie/dead는 더 이상
실행하지 않는 상태다. Proc 읽기 오류를 정상 종료로 바꾸지 않고, 사전 관측 실패에도 그룹 종료
신호는 보낸다. 기존 명시적 cleanup deadline은 자식 대기에도 적용하고 생략 시 새 기한을 만들지
않는다. 0.02초는 상태 관측 간격이며 실행/정리 시간 제한이 아니다. setsid 이탈의 기존 한계는 같다.

ProcessStat가 /proc stat의 필드 위치·PID/start_ticks·상태 해석을 소유한다. 기존 진단 Tool도 같은
Reader를 사용하여 comm의 공백/괄호와 PID 재사용 해석을 중복하지 않는다.

EngineContext.read_tool_binding은 실제 Checkpoint 범위를 소유한다. 루트 저장 기록은 공통 사용량을
필수 검증하고 선택 payload로 투영한다. EngineCheckpointScope가 내놓은 중첩 snapshot은 이미
바깥 envelope와 분리된 Engine payload이므로 같은 ToolBindingRecord payload Reader로 읽는다.
바깥 서비스/Agent usage Reader의 필수 증거 검사는 그대로이며, 내부 payload에 사용량을 중복
저장하거나 누락된 바깥 증거를 기본값으로 복원하지 않는다.
