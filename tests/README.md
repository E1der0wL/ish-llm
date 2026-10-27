# 플러그인별 테스트

`llm/`, `hub/` 하위에서 각 플러그인의 테스트를 관리합니다. 제품 패키지를 테스트
디렉토리가 가리지 않도록 저장소 루트에서 모듈로 실행하고 discovery에 `-t .`를 줍니다.

```sh
# Linux Python 3.12.14: Linux 스냅샷에서 집중 + 전체 llm 검사
.venv-linux312/bin/python tests/llm/run_linux.py --full

# llm 설정 계약과 연결된 hub UI까지 함께 검사
.venv-linux312/bin/python tests/llm/run_linux.py --full --hub

# 이미 소스와 환경이 Linux 파일시스템에 있을 때
.venv-linux312/bin/python -m unittest discover -s tests/llm -t . -v

# 선택한 검사만 실행
.venv-linux312/bin/python tests/llm/run_linux.py --modules tests.llm.test_plugin
```

- `llm/support/`, `llm/configuration_fixtures.py`: 공유 fixture와 subprocess 검사 도우미
- `llm/reports/<snapshot-name>/`: 실행별 로그와 소스 해시 manifest. 이전 결과를 덮어쓰지 않습니다.
- `llm/reports/latest.json`: 가장 최근에 끝난 검사의 보고서 디렉토리
- `llm/reports/history/`: 기존 검사 로그·스냅샷·임시 검사 스크립트의 원본 보관
- `llm/reports/history/workspaces/`: 과거 데모 결과의 원본 보관. 현재 실행 workspace가 아닙니다.
- `llm/reports/runs/`: 예제가 별도 출력 경로 없이 생성하는 지속 실행 자료
- `llm/manual/`: 개인 설정을 사용하는 수동 테스트; 자동 discovery·snapshot·배포에서 제외

과거 보고서와 snapshot 내부 경로는 당시 실행 기록으로 보존합니다. 과거 스크립트는 현재
실행기가 아닙니다. 이들 기록을 현재 소스 검증 결과로 사용하지 않습니다.
`ish.platform/`은 로더 계약 검사에 참고하며 이 작업 공간에서 본체를 배포하지 않습니다.
이 저장소에는 해당 호스트 소스를 포함하지 않습니다. 전체 검증에는 [CI 정의](../.github/workflows/tests.yml)의
reference host revision과 OS 의존성을 준비합니다. 환경 누락으로 발생한 skip을 전체 통과로 보고하지 않습니다.
`tests.llm.run_regression`의 엄격 실행은 skip·누락·중복도 실패로 취급합니다.
`run_linux.py`도 동일한 실행기를 사용하며 결과 JSON과 테스트별 시작·종료 JSONL을 남깁니다.
한 테스트가 120초 이상 끝나지 않으면 스레드 스택을 기록하고 검사는 계속 실행합니다.
검증용 복사본에는 두 플러그인, 공통 문서, 의존성 lock, 참조 호스트 설정과 CI 자료가 포함됩니다.
`--hub`는 복사 범위가 아닌 Hub 전체 검사 실행 여부를 지정합니다.
텍스트 파일은 CRLF만 LF로 정규화하며 manifest에는 원본 바이트 해시와 복사본 해시를 함께
기록합니다. 소유권·아키텍처 감사도 같은 LF 기준을 사용하므로 코드 변경은 검출하면서
Git checkout의 줄바꿈 차이는 검토 불일치로 취급하지 않습니다.
복사본의 Git index는 별도로 생성하며 실제 작업 저장소의 index나 파일은 변경하지 않습니다.
의존성 입력은 상위 저장소 HEAD가 아닌 참조 호스트의 설정·소스 내용 해시를 사용합니다.
CI의 명시적인 외부 호스트 revision 확인은 별도로 유지됩니다.
보고서와 개인 수동 스크립트는 Git 제외 대상입니다. 자동 테스트는 실제 모델 API를 호출하지 않습니다.
ish 설정 검사는 공개 `examples/llm/ishrc.example.py`를 사용합니다. 개인 `.ishrc.py`는
테스트 입력이나 소스 snapshot에 포함하지 않습니다.

Hub 공통 PTK 출력·비동기 입력 도우미는 `hub/support.py`, 독립 미리보기 검사는
`hub/test_preview.py`에 있습니다. 이 도우미들은 개별 테스트 모듈에 의존하지 않습니다.
