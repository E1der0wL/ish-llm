# hub 예제

실제 대화는 저장소 루트에서 `python -m hub --agent assistant --model openai/YOUR_MODEL`로
실행합니다. 인증/서버 설정은 [Hub 사용법](../../hub/README.md)을 참고하세요.
`ishrc.live.example.py`는 실제 ish 백엔드 연결 예제입니다.

`python -m examples.hub.preview`로 독립 PTK 미리보기를 실행합니다.
Linux Python 3.12.14, `prompt-toolkit==3.0.53`, `rich>=14,<15`가 필요합니다.
ESC로 패널에 이동한 뒤 다시 ESC로 최소화하고, 모형 셸에서 Ctrl+C로 종료합니다.
Ctrl+Q로 어느 화면에서든 최소화하고, 셸에서 다시 Hub를 열 수 있습니다.
기본 페이지 배경은 터미널 배경을 유지합니다. 모양 설정에서 팔레트 값을 직접 지정합니다.
사용자 메시지는 오른쪽 정렬하고 Assistant 응답은 Rich Markdown으로 표시합니다.

`ishrc.example.py`는 배포한 hub 플러그인을 실제 ish에 연결하는 설정 예제입니다.
이 설정은 백엔드 없이 빈 UI를 엽니다. 샘플 대화는 `sample_data.py`에 있으며
`preview.py`가 명시적으로 주입합니다. 플러그인 실행 시 예제 패키지를 import하지 않습니다.
