# ish에서 명시적으로 Project를 선택하여 LoopEngine 테스트

1. 최신 `llm/` 폴더를 ish의 plugin/script/llm에 복사한다.
   예제는 플러그인 배포에 포함하지 않는다. pip 설치는 필요하지 않다.
   Python 의존성은 기존 플러그인 로더가 PLUGIN_META를 통해 확인한다.
   이 개발용 검사를 연결하려면 별도 checkout의 루트가 ish와 Tool worker 모두의
   Python import 경로에 있어야 한다. 예제 import는 `examples.llm.ish_loop`다.
2. `examples/llm/ishrc.example.py` 내용을 실제 ish의 `config.RC_FILE`에 추가한다.
   현재 호스트의 기본 경로는 `~/ish/.ishrc.py`이며, ISH_HOME 설정을 바꾸었다면 그 경로를 따른다.
   기존 설정은 유지하고 `model`과 `api_base`를 사내 서버에 맞게 수정한다.
   OpenAI 호환 서버는 `openai/<서버에서 제공하는 모델명>`을 사용한다.
   다른 provider는 해당 LiteLLM 모델명을 사용하고 불필요한 api_base를 삭제한다.
3. ish를 다시 시작한 뒤 실행한다.

```sh
export ISH_LLM_API_KEY='사내에서 발급받은 키'
llm-test "한국어로 짧게 인사하고 Python 코드 리뷰 시 확인할 항목 세 가지를 알려줘"
llm-test --project-id 출력된ProjectID --session-id 출력된SessionID "방금 제시한 첫 번째 항목을 더 자세히 설명해줘"
```

`llm-test --help`로 명령 인자를 확인한다. API 키 환경 변수는 명령 실행 시점에
읽는다. 이미 provider 전용 인증 환경 변수를 쓰면 `api_key_env=None`으로 지정한다.
환경 변수 대신 코드에서 넘기려면 completion에 `"api_key": "발급받은 키"`를
추가하고 `api_key_env=None`으로 지정할 수 있다.

실제 처리 코드는 `examples/llm/ish_loop.py`의 `run_request()`다.
ish worker → asyncio.run → LargeLanguageModel → projects.acreate/aload →
sessions.acreate/aload → session.run.submit(engine="loop") → request.wait → aresult 순서다.
응답은 TEXT_DELTA 이벤트로 즉시 출력하고, 완료 후 Run ID·상태·종료 이유·사용 토큰을
표시한다. 모델이 사용량을 주지 않으면 토큰은 None이다.

Project ID를 생략하면 새 Project를 생성한다. `--project-id`를 지정하면 저장된 Project를
선택한다. 이 예제는 Component를 선택하지 않으며 대화 저장은 file로 명시한다.
Session ID를 생략하면 새 대화이며, 지정하려면 Project ID도 함께 전달해야 한다.
기록은 출력된 Project 경로에 저장된다. completion과 반복/시간 제한은 예제의 명시적
호스트 설정이며 기존 ProjectConfig를 덮어쓰지 않는다. UI의 자동 선택/템플릿은 hub가 소유한다.

승인 등이 필요한 Run은 PAUSED 상태를 출력한다. 이 예제는 한 요청과 스트리밍을
테스트하며 승인 선택/명시적 재개 UI까지 구현하지 않는다. 오류/미완료는 종료 코드 1,
Ctrl+C는 130, 성공은 0이다. Ctrl+C 후 이미 수행된 외부 작업의 취소를 보장하지 않는다.

ish의 Tool worker는 함수를 pickle로 전달하므로 `.ishrc.py`에 정의한 lambda나
지역 async 함수를 직접 등록하지 않는다. import 가능한 main에 partial로 설정값만
묶으며, 백엔드는 worker 안에서 만들고 async with로 정리한다.
