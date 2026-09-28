# 장애 복구·다중 프로세스 검사

Linux **Python 3.12.14**와 llm의 의존성이 설치된 환경에서 실행한다. `llm`의 부모가
import 경로에 있어야 한다. API 키·외부 모델·관리자 권한은 필요 없다.

```sh
python3.12 -m llm.examples.recovery_probe --output-dir ~/llm-probes
```

매번 `llm-recovery-probe-*` 디렉토리를 새로 만든다. 기존 workspace는 입력으로 받지 않으며
실제 디스크를 가득 채우지 않는다. stdout과 `report.json`에 결과를 남기고 도메인 기록,
합성 작업의 효과 기록, 자식별 로그를 보존한다. 실제 파일시스템의 잠금/저장을 확인하려면
`--output-dir`을 향후 workspace를 둘 디스크에 지정한다.

## 검사 범위

| case | 재현 방법 | 성공 조건 |
| --- | --- | --- |
| `crash` | 부분 출력과 다음 QUEUED 요청이 저장된 것을 확인한 뒤 실행 프로세스에 실제 SIGKILL | 새 프로세스가 원래 Run/Step/Assistant를 interrupted로 복구하고 부분 본문을 보존. 이전 효과를 재실행하지 않고 대기 요청만 완료 |
| `disk-full` | 별도 프로세스의 저장 함수에 ENOSPC를 한 번씩 주입: Run 시작의 Session 교체, 출력 저널 fsync, Run 완료 교체 | 시작 실패는 QUEUED와 원래 Session 상태로 rollback, 출력 실패는 미확정 델타 제거, 완료 실패는 미확정 완료 상태 rollback. 새 프로세스에서 다음 요청 처리 가능 |
| `connection` | 로컬 OpenAI 호환 HTTP/1.1 SSE 서버가 첫 토큰 전 또는 부분 토큰 후 TCP를 끊음. 실제 `litellm.completion(stream=True)` 경로 사용 | 실패 Run/Step이 남고 부분 응답이 중복 없이 보존. 다음 요청 성공, 재개방 후 실패 기록 유지. 테스트 설정상 공급자 재시도 없음 |
| `multiprocess` | 한 프로세스가 실행권을 보유한 상태에서 기본 3개 경쟁 프로세스가 같은 workspace 생성·조회 시도 | 모두 WorkspaceBusyError로 거부되고 도메인 파일이 불변. 소유자 정상 종료 후 새 프로세스가 기록 조회/실행권 확보 가능 |

`multiprocess`의 성공은 여러 프로세스가 같은 workspace를 동시에 변경한다는 의미가 아니다.
현재의 단일 소유 프로세스 계약을 검증한다. 한 소유 프로세스 안의 여러 Session 동시 실행은
`domain_performance.py`에서 별도로 검사한다.

```sh
python3.12 -m llm.examples.recovery_probe --case crash --output-dir ~/llm-probes
python3.12 -m llm.examples.recovery_probe --case disk-full --output-dir ~/llm-probes
python3.12 -m llm.examples.recovery_probe --case connection --output-dir ~/llm-probes
python3.12 -m llm.examples.recovery_probe --case multiprocess --processes 5 --timeout 90
```

`--timeout`은 자식별 제한 시간이며 기본 60초다. 느린 디스크/최초 SDK 로딩 때문에 제한을
넘으면 값을 늘려 재검사한다. 실패한 자식의 로그 경로는 보고서에 나온다. 경쟁 프로세스가
차단되는 것은 정상 성공 조건이므로 오류 문자열만으로 실패를 판정하지 않는다.
연결 검사는 먼저 로컬 준비 호출(`warmup`)을 한 번 수행한다. 최초 SDK 로딩 지연을
연결 단절로 오인하지 않도록 분리하며, 준비 호출도 보고서의 요청 목록에 포함한다.

## ish에서 실행

```python
from functools import partial

if plugin.get("llm") is not None:
    from llm.examples.recovery_probe import main as recovery_probe
    prompt.set_tool("llm-recovery-test", function=partial(
        recovery_probe, "--output-dir", "/home/user/llm-probes"))
```

```sh
llm-recovery-test
llm-recovery-test --case connection
```

호출자의 `sys.executable`이 실행 가능한 Python 3.12.14여야 한다. ish가 추가한
plugin/script·plugin/lib import 경로는 자식의 PYTHONPATH로 전달한다. 호스트가 Python 대신
별도 실행 파일을 sys.executable로 제공하면 위의 독립 Python 명령으로 실행한다.
SDK 안내/경고는 자식별 로그에 들어가고 부모는 마지막 JSON 보고서를 출력한다.

## 결과 해석과 범위

- 종료 코드: 모두 통과 `0`, 하나라도 실패 `1`, 인자/환경 오류 `2`, Ctrl+C `130`.
- `report.json`의 최종 `status` 및 각 `cases.*.status`가 `passed`인지 확인한다.
- 검사 중 예외·Ctrl+C·시간 초과가 발생하면 이 검사에서 만든 자식 프로세스를 종료·회수한다.
- `disk-full`은 지정된 저장 경계의 ENOSPC 처리와 복구 검사다. 지속적인 디스크 고갈,
  inode 부족, 복구 저널까지 쓸 수 없는 상태나 하드웨어 전원 장애를 재현하지 않는다.
- `connection`은 실제 SDK/HTTP 스트림 단절 검사다. 사내 DNS·TLS·프록시·모델 서버의
  고유 오류나 장시간 무응답은 해당 서버와 별도로 확인해야 한다.
- SIGKILL은 프로세스 장애이며 OS/디스크 전원 차단과 같지 않다. 임의 외부 Tool의
  효과가 정확히 한 번만 발생한다는 보장을 검증하는 스크립트도 아니다.

이 검사는 정상 저장 후 재개방 검사보다 강한 장애 경로를 다루지만, 사내 부하에서의
장시간 운영·대용량 RAG 성능/품질은 다른 검사와 함께 확인해야 한다.
