---
name: computer-diagnosis
description: "재현 조건, 프로세스 상태와 로그를 비교해 원인을 확인"
---
1. 기대 동작과 실제 현상, 발생 조건을 구분한다. 먼저 읽기 전용 관찰을 한다.
2. system_inspect/process_list/process_inspect로 환경과 PID의 실제 상태를 확인한다.
3. file_read의 tail_lines와 file_search로 관련 로그 및 입력/출력 경로의 소스를 읽는다.
4. process_start로 허용된 최소 재현을 실행하고 process_output의 next offset으로 이어 읽는다.
5. 가설마다 반증할 관찰을 정한다. 시각·PID·입력 분기 기록이 없으면 과거 흐름을 확정하지 않는다.
6. 근거를 확보한 뒤 필요한 변경과 검증을 수행하고, 사실/가설/추가 관찰 필요를 구분해 보고한다.
기존 ish 터미널과 이 Toolkit이 실행한 프로세스는 다르다. pipe 입력은 PTY가 아니다. process_write는 개행을 자동 추가하지 않으며 결과가 불확실한 입력을 임의 재전송하지 않는다.