# 관찰 이벤트 내보내기

기존 Observability sink는 빠른 동기 callback 계약을 유지한다. 느린 파일/네트워크 exporter를
붙일 때 Application은 `QueuedSink`를 명시적으로 선택할 수 있다. 자동 활성화되지 않는다.

```python
from llm.services.infrastructure.exporter import QueuedSink
from llm.services.infrastructure.observability import Observability

# 용량과 유실 선택은 예제 Application의 값이다.
async with QueuedSink(export_event, capacity=128, overflow="drop_oldest") as sink:
    observer = Observability(sink=sink)
    observer.record("tools", "requests")
```

기존 Backend runtime wiring의 observability_sink에도 이 callable을 전달할 수 있다.
Backend 사용이 끝난 뒤 Application이 `aclose()` 또는 async context 종료로 회수한다.
producer는 이벤트 사본을 bounded queue에 넣으며 exporter I/O를 기다리지 않는다.
`drop_oldest`는 대기 중 가장 오래된 관찰을, `drop_newest`는 새 관찰을 버린다.
실행 기록·승인·체크포인트 저장은 이 queue를 사용하지 않는다.

snapshot은 accepted/exported/failed/dropped/rejected 누적 집계만 소유한다.
drop_oldest로 버려진 항목은 앞서 accepted에도 포함되므로 accepted는 최종 전달 수가 아니다.
Run/Tool/Provider 상태나 active/waiting 값을 다시 관리하지 않는다. exporter 예외는 집계하며
Run으로 전파하거나 자동 재시도하지 않는다.

close는 새 접수를 막고 이미 받아 둔 항목을 처리한 뒤 worker를 종료한다. aclose는 event loop
밖에서 이를 기다리고, 취소를 받아도 회수 후 취소를 전파한다. 숨은 종료 timeout은 없다.
임의의 blocking callback을 강제로 죽이지 않으므로 exporter 자신이 I/O deadline을 소유해야 한다.
이 방식은 외부 로그의 무손실·durability를 보장하는 저장 시스템이 아니다.
