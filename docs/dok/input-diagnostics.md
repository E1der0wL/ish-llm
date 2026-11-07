# 간헐적인 `[` 입력 조사

2026-11-07 사용자 보고: ish를 켠 뒤 불특정하게 `[[[[[`가 커서에 입력되고 수초 동안 조작이 어려우며, 회복 후에도 문자가 버퍼에 남는다. 이 보고를 단순 화면 잔상이나 이전 Windows Terminal 선택 정지 현상으로 간주하지 않는다.

확인 당시 실제 WSL 터미널은 66×287, raw 입력 모드(`-icanon -echo`), 소프트웨어 흐름 제어 비활성(`-ixon`)이었다. 이것은 확인 순간의 상태이며 장애 순간의 상태를 증명하지 않는다. dok의 정기 시스템 갱신은 프롬프트 버퍼에 문자를 쓰지 않는다.

격리한 실제 ish 1.2.1 프로세스에서 하단 바를 갱신하는 동안 빈 프롬프트를 유지했고, 정상 CPR 응답과 SGR 마우스 클릭도 입력을 남기지 않았다. 반면 포커스 보고 `ESC[I`·`ESC[O`를 합성 입력하면 현재 입력 파서는 이를 Escape와 일반 문자로 나누어 프롬프트에 `[I[O`를 남겼다. [합성 입력 재현](../../tests/dok/reports/dok-status-language-c4m2g9ur/protocol-probe.json), [제어 신호 기록](../../tests/dok/reports/dok-status-language-c4m2g9ur/protocol-probe.trace.json). 이 결과만으로 사용자의 `[[[[[` 원인이 포커스 보고라고 확정하지 않는다.

## 실제 터미널에서 기록

WSL의 문제가 발생하는 Windows Terminal 탭에서 다음을 실행한다.

```sh
/home/user/.local/bin/ish-1.2.1/bin/python/bin/python3 /mnt/d/WorkSpace/ish/tests/dok/manual/trace_input.py
```

실제 설치된 ish와 dok을 별도 임시 홈으로 시작한다. 하단 바·키보드·마우스를 사용해 재현한 뒤 `ish_exit`로 종료한다. 종료 시 `/home/user/.cache/dok-input-trace-*/control-events.json` 경로를 출력한다. 기존 설정·일정·ish 소스를 변경하지 않으며 다른 플러그인을 로드하지 않는다. 날씨 요청·오디오·MPRIS 연결을 끈 상태이므로 개인 세션과 완전히 같은 구성은 아니다.

기록은 최근 256개의 CSI 제어 시퀀스, 입력 파서 flush, 일반 `[`로 분류된 횟수, 렌더 간격과 당시 마우스/대기 설정뿐이다. 일반 명령 문자열·붙여넣기 내용·OSC 제목·프롬프트 전체는 저장하지 않는다. 정상 입력을 차단하거나 수정하지 않는 진단 도구이며, 프로덕션 플러그인에 상시 추적 코드를 추가하지 않는다. 이 기록으로 실제 유입 신호가 확인된 뒤 입력 처리 책임과 수정 범위를 결정한다.
