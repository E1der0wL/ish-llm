# 개인 수동 검사

기존 `gemini_test.py`와 `session_test.py`를 보관합니다. 이 파일들은 개인 설정을 포함할 수
있으므로 자동 테스트·소스 snapshot·플러그인 배포에 포함하지 않으며 Git에서도 제외합니다.
실행이 필요하면 저장소 루트에서 내용을 검토한 뒤 `python -m tests.llm.manual.gemini_test`
처럼 명시적으로 호출합니다. 이 디렉토리를 자동 discovery 대상으로 삼지 않습니다.
