# 간헐적인 `[` 입력 조사

2026-11-07 사용자 보고: ish를 켠 뒤 불특정하게 `[[[[[`가 커서에 입력되고 수초 동안 조작이 어려우며, 회복 후에도 문자가 버퍼에 남는다. 이 보고를 단순 화면 잔상이나 이전 Windows Terminal 선택 정지 현상으로 간주하지 않는다.

2026-11-08 개인 환경 진단에서는 마우스 조작만으로 `ESC[C` 방향키 신호가
대량 유입되는 실제 장애를 기록했다. 활성 Windows Terminal 프로필의
`experimental.repositionCursorWithMouse: true`가 확인됐고, 해당 기능을
우회하는 Shift 클릭·드래그 비교에서는 증상과 새 방향키 CSI·`[` 입력이
사라졌다. [상세 관측과 한계](popup-lifecycle-review-20261108.md)를 참고한다.
Windows Terminal 설정이나 제품 코드는 아직 바꾸지 않았다.

확인 당시 실제 WSL 터미널은 66×287, raw 입력 모드(`-icanon -echo`), 소프트웨어 흐름 제어 비활성(`-ixon`)이었다. 이것은 확인 순간의 상태이며 장애 순간의 상태를 증명하지 않는다. dok의 정기 시스템 갱신은 프롬프트 버퍼에 문자를 쓰지 않는다.

격리한 실제 ish 1.2.1 프로세스에서 하단 바를 갱신하는 동안 빈 프롬프트를 유지했고, 정상 CPR 응답과 SGR 마우스 클릭도 입력을 남기지 않았다. 반면 포커스 보고 `ESC[I`·`ESC[O`를 합성 입력하면 현재 입력 파서는 이를 Escape와 일반 문자로 나누어 프롬프트에 `[I[O`를 남겼다. [합성 입력 재현](../../tests/dok/reports/dok-status-language-c4m2g9ur/protocol-probe.json), [제어 신호 기록](../../tests/dok/reports/dok-status-language-c4m2g9ur/protocol-probe.trace.json). 이 결과만으로 사용자의 `[[[[[` 원인이 포커스 보고라고 확정하지 않는다.

## 실제 터미널에서 기록

### 개인 설정·플러그인을 그대로 사용하는 진단

2026-11-08 추가한 `tests/dok/manual/trace_current_input.py`는 설치된 ish와 실제
`~/ish/.ishrc.py`, `~/ish/dok/config.jsonc`, Hub·dok 및 오디오·날씨 설정을 그대로
사용한다. 아래의 기존 격리 실행기와 다르다. 제품 소스나 설정을 수정하지 않고
새 프로세스 안에서 입력·출력·상태 관측 함수만 연결한다. 일반적인 ish 시작에
따른 캐시 갱신, 설정된 날씨 요청, 알림 확인 등은 그대로 동작한다.

정확한 터미널 연결을 비교하려면 현재 ish에서 `ish_exit`로 기본 WSL 셸로 돌아온
뒤, 같은 Windows Terminal 탭에서 실행한다. 실행 중인 다른 ish 안에 중첩하면
바깥 ish의 PTY 전달 계층이 추가되므로 그 차이를 기록해야 한다.

```sh
/home/user/.local/bin/ish-1.2.1/bin/python/bin/python3 -I /mnt/d/WorkSpace/ish/tests/dok/manual/trace_current_input.py
```

캘린더·날씨를 열고 ESC로 닫은 뒤 마우스를 움직이거나 드래그한다. 화면만
갱신되고 입력이 막히더라도 다른 창에서
`~/.cache/dok-current-input-*/control-events.json`을 읽을 수 있다. 정상 상태에서
`ish_exit`로 종료하면 마지막 기록도 저장한다. 기록은 백그라운드 스레드에서
1초마다 교체하며 최근 512개 이벤트와 누적 횟수만 유지한다.

기록 대상은 CSI 종류·마우스 모드 출력, 입력 큐 길이, 포커스 종류, 팝업 상태,
버퍼 길이, `[` 개수, 자동완성/선택 활성 여부, 버퍼 변경 호출 함수 이름이다.
명령 문자열·버퍼 본문·일정 내용·클립보드·붙여넣기·OSC/DCS 문자열은 저장하지
않는다. 진단 오버헤드가 있으므로 이 기록만으로 원래 지연 시간을 정밀하게
측정했다고 주장하지 않는다.

### 격리 설정으로 비교하는 진단

WSL의 문제가 발생하는 Windows Terminal 탭에서 다음을 실행한다.

```sh
/home/user/.local/bin/ish-1.2.1/bin/python/bin/python3 /mnt/d/WorkSpace/ish/tests/dok/manual/trace_input.py
```

실제 설치된 ish와 dok을 별도 임시 홈으로 시작한다. 하단 바·키보드·마우스를 사용해 재현한 뒤 `ish_exit`로 종료한다. 종료 시 `/home/user/.cache/dok-input-trace-*/control-events.json` 경로를 출력한다. 기존 설정·일정·ish 소스를 변경하지 않으며 다른 플러그인을 로드하지 않는다. 날씨 요청·오디오·MPRIS 연결을 끈 상태이므로 개인 세션과 완전히 같은 구성은 아니다.

기록은 최근 256개의 CSI 제어 시퀀스, 입력 파서 flush, 일반 `[`로 분류된 횟수, 렌더 간격과 당시 마우스/대기 설정뿐이다. 일반 명령 문자열·붙여넣기 내용·OSC 제목·프롬프트 전체는 저장하지 않는다. 정상 입력을 차단하거나 수정하지 않는 진단 도구이며, 프로덕션 플러그인에 상시 추적 코드를 추가하지 않는다. 이 기록으로 실제 유입 신호가 확인된 뒤 입력 처리 책임과 수정 범위를 결정한다.
