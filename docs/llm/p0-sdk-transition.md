# P0 SDK/MCP 전환

사용자가 P0의 의존성 충돌을 최신 SDK로 해결한 뒤 P1·A–F로 진행하도록 승인한 작업이다.
기존 [P0 기록](p0-verification.md)의 실패와 이전 SDK 조합 통과 결과는 보존한다.
이 전환의 전체 회귀가 끝나기 전에는 P0 완료로 보고하지 않는다.

## 선언과 설치

LLM 지원 범위의 원본은 [pyproject.toml](../../pyproject.toml)이다.
LiteLLM의 일반 function Tool 요청도 MCP 모듈을 import하므로 `litellm[mcp]`의 정규
의존성을 설치한다. MCP 기능 활성화·서버 실행·승인 선택은 설치와 별개다.
OpenAI 등 하위 SDK를 독립적으로 최신 major로 강제하지 않고 선택 LiteLLM의 선언을 따른다.

ish는 import 전에 literal `PLUGIN_META`를 읽는다. [의존성 도구](../../tests/llm/dependencies.py)는
manifest의 기본 의존성·명시 feature group·import 이름 매핑으로 이 목록을 생성하고 검증한다.
플러그인 실행 시 테스트 도구나 저장소 루트 manifest를 읽지 않는다. Hub·외부 host 요구사항은
각 소유자의 선언을 읽으며 LLM에 복제하지 않는다.

[Linux lock](../../tests/llm/dependencies-linux.lock)은 Linux x86_64/Python 선언 및 고정 host의
요구사항에서 생성한 전이 의존성의 exact version·artifact hash다. 선언/host SHA가 바뀌면
fingerprint 검사에 실패한다. 검증 환경은 lock의 설치 버전 및 원본 요구 범위를 모두 검사한다.
제품 지원 범위와 검증 조합은 구별하며 모든 미래 버전의 호환성을 보장하지 않는다.

```bash
# 의존성 변경 시 명시적으로 생성하고 diff를 검토한다.
python -m tests.llm.dependencies --write-plugin
python -m tests.llm.dependencies --lock
python -m tests.llm.dependencies

# 기존 환경은 보존하고 새 경로를 지정한다. uv가 설치되어 있어야 한다.
python -m tests.llm.setup_linux --environment /tmp/ish-sdk-verification
/tmp/ish-sdk-verification/bin/python -m tests.llm.dependencies --installed
```

CI의 일반 push/PR은 `pip install --require-hashes`로 lock을 설치한다.
수동 workflow의 `test_latest=true`는 같은 선언의 최신 허용 조합을 설치하고 동일한 필수
회귀를 실행한다. 후보 실행 결과를 고정 조합 통과와 혼동하지 않는다. 업데이트를 채택할 때
lock을 다시 생성·검토·커밋하여 다음 일반 CI에서 재검증한다.

## 어댑터와 안전성

- `MCPConnector`는 MCP 2의 typed pagination과 snake_case SDK 속성을 사용한다.
  서버 선언/결과의 MCP wire 이름은 이 어댑터에서 `by_alias=True`로 직렬화한다.
- HTTP는 공개 `httpx2.AsyncClient`를 사용한다. 명시한 timeout만 적용하고 None은 추가
  deadline 없음이다. SDK private factory의 암묵적 timeout을 가져오지 않는다.
- `MCPComponent.REVISION`이 구현 revision을 소유한다. 이전 catalog/binding을 stale로
  거부하고 재준비를 요구한다. 오래된 승인·checkpoint를 변환해 실행하지 않는다.
- 실제 stdio/HTTP, 다중 페이지 발견, 인증 전달, 취소, 명시 timeout, 원격 오류를 검증한다.
  기존 ToolExecutor 승인 경로와 uncertain 효과 처리·재개 거부를 유지한다.
- 원격 서버가 실제 파일 효과를 fsync한 뒤 `os._exit`하는 시험으로 완료 응답이 없는 효과를
  검증한다. 백엔드를 재시작한 뒤 Resume가 거부되고 효과가 정확히 한 번 남아야 한다.

SDK의 SSE GET 재연결과 Tool POST 재실행은 다른 동작이다. 읽은 MCP 2.3 transport는
응답 event ID로 GET을 재연결하며 원본 Tool POST를 재호출하는 경로와 구별된다.
모든 네트워크 장애 유형을 검증했다는 뜻은 아니다.

## 실행 기록

- 변경 전 새 SDK 환경: 기존 MCP 시험 6개 중 3 pass / 3 error / 0 Skip.
  제거된 FastMCP 서버 API와 서버 종료 뒤 테스트 cleanup 오류를 확인했다.
- 초기 수정 집중 시험: 11개 중 10 pass / 1 error. 새 인증 확인용 fixture의 잘못된
  Context 속성을 MCP 2의 `headers`로 고쳤다. assertion은 유지했다.
- 이후 MCP/의존성/Provider/명시 설정 집중 시험: 79 pass, 0 failure/error/Skip, 18.293초.
  실제 crash 시험 추가 이전 집합이다.
- 최종 집중 시험: MCP 8개, 의존성 4개, 실제 host 관련 44개, R3 1개를 합해
  57 pass / 0 failure/error/Skip, 27.220초. 실제 효과 직후 crash와 재시작 뒤 재개 거부를 포함한다.
- Python 3.12.14, host `d833790959a76ac457fdeea3f21a6bea1eecfe12`,
  LiteLLM 1.106.0 / OpenAI 2.54.0 / MCP 2.3.0의 새 lock 환경에서
  `python -m tests.llm.run_regression --suite all --output <artifact>/regression.json`:
  2,197 발견·실행·통과, failure 0 / error 0 / Skip 0, 802.305초, exit 0.
  LLM·Hub·tests의 Python 파일 hash를 실행 전후 비교해 변경이 없음을 확인했다.
  Xvfb/xclip 및 OCR도 실행했으며 미실행을 통과로 합산하지 않았다.
- 의존성 원본/lock/설치 버전, `pip check`, LLM·Hub 환경 검사, 실제 SDK 오프라인 HTTP 시험
  2개, R3 제한 전달 누락 mutation 검사가 각각 exit 0이었다.
- 로컬 증거는 `/workspace/llm-clean-p0/sdk-full/`에 보존한다. 집중 시험과 전체 회귀는
  겹치는 집합이므로 합산하지 않는다. 새 커밋의 원격 CI는 별도로 검증한다.

Cloud 설치 스크립트도 lock hash별 새 환경을 생성하도록 실제 실행·재실행 검증한 뒤
설정 draft revision 7에 저장했다. 예전 가상환경과 실패 기록은 보존했다.
draft 저장은 게시 또는 새 Cloud 작업에서의 복원 검증을 뜻하지 않는다.

정적 감사의 새 위반·stale 항목은 0이지만 기존 767개 후보는 여전히 미해결이다.
이는 P0의 증가 방지 기준선이며 전체 하드코딩 제거 완료를 뜻하지 않는다.

실제 인증 모델 호출과 커널 short-write 장애 주입은 계속 미검증이다. P1 및 A–F는
이 단계의 통과를 확인한 뒤 진행한다.
