# ish 플러그인 작업 공간

Linux Python **3.12.14**에서 개발·검증합니다. 플러그인 실행 코드와 개발 자료를 분리합니다.

```text
ish/
├── llm/                 # AI 실행 백엔드 플러그인 — 배포 대상
├── hub/                 # Prompt-Toolkit 인터페이스 플러그인 — 배포 대상
├── dok/                 # 셸 dock·바로가기·오디오 비주얼라이저 플러그인 — 배포 대상
├── ish.platform/        # 별도 로컬 참고 소스 / 로더 계약 테스트 (Git 제외)
├── tests/
│   ├── llm/             # 자동 검사, fixture, 실행기, reports/, manual/
│   ├── hub/             # hub UI·백엔드 연결 검사
│   └── dok/             # dok UI·호스트·오디오·PTY 검사
├── examples/
│   ├── llm/             # 실행 예제, 설정 샘플, 샘플 문서
│   └── hub/             # hub 실행·미리보기 예제
├── docs/
│   ├── llm/             # 아키텍처, API, 작업 인계 및 검토 문서
│   ├── hub/             # hub 설계 문서
│   └── dok/             # dok 설치·설정·설계 문서
└── AGENTS.md            # 로컬 개발 지침 (Git 제외)
```

`llm/`은 AI 실행 백엔드이고 `hub/`는 이를 사용하는 대화·설정 UI입니다.
`dok/`는 셸 입력 화면에 바로가기와 Linux 출력 오디오 비주얼라이저를 제공하며,
llm이나 Hub 없이 독립적으로 설치할 수 있습니다.
ish에 배치할 때 `tests/`, `examples/`, `docs/`, `ish.platform/`을 플러그인 디렉토리 안에 넣지 않습니다.
컴포넌트별 사용 설명과 설정 계약은 코드 옆 Markdown으로 유지합니다.

- [llm 사용 안내](llm/README.md), [Backend API](docs/llm/backend-api.md)
- [hub 사용 안내](hub/README.md), [hub 설계](docs/hub/README.md)
- [dok 설치·사용 안내](docs/dok/README.md), [dok 설계](docs/dok/design.md)
- [개발 문서 안내](docs/README.md), [아키텍처](docs/llm/architecture.md), [최신 작업 인계](docs/llm/handoff.md)
- [명명 규칙과 API 변경표](docs/llm/naming-conventions.md)
- [테스트 실행](tests/README.md), [예제 실행](examples/README.md)

전체 llm 회귀 검사는 저장소 루트에서 실행합니다. 실행기는 소스를 Linux 파일시스템에
복사한 뒤 정확한 Python 버전으로 검사하고 `tests/llm/reports/`에 결과를 기록합니다.

```sh
.venv-linux312/bin/python tests/llm/run_linux.py --full

# Hub 연결 검사도 함께 실행
.venv-linux312/bin/python tests/llm/run_linux.py --full --hub

# 독립 dok 플러그인 검사
.venv-linux312/bin/python tests/dok/run_linux.py
```

배포는 필요한 `llm/`, `hub/`, `dok/` 폴더를 복사하는 방식입니다. 플러그인 자체의 pip 설치는 필요하지 않습니다.
개발 의존성은 `pyproject.toml`, 호스트가 설치할 의존성은 `llm/llm.py`의
`PLUGIN_META.dependencies`에 정의되어 있습니다. Hub 의존성은 `hub/hub.py`에 선언합니다.
Dok 진입점은 `dok/dok.py`이며, 오디오 기능에 필요한 Linux 도구는 dok 설치 안내를 따릅니다.
