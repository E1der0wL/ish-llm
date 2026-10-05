# hub 예제

실제 대화는 저장소 루트에서 `python -m hub --engine loop --model openai/YOUR_MODEL`로
실행합니다. 인증/서버 설정은 [Hub 사용법](../../hub/README.md)을 참고하세요.
`ishrc.live.example.py`는 실제 ish 백엔드 연결 예제입니다.

`python -m examples.hub.preview`로 독립 PTK 미리보기를 실행합니다.
Linux Python 3.12.14, `prompt-toolkit==3.0.53`, `rich>=14,<15`가 필요합니다.
ESC로 패널에 이동한 뒤 다시 ESC로 최소화하고, 모형 셸에서 Ctrl+C로 종료합니다.
셸에서 Hub를 다시 열 때는 Ctrl+Q를 사용합니다.
기본은 터미널 배경을 유지합니다. `--theme dark`로 어두운 테마를 비교합니다.
사용자 메시지는 오른쪽 정렬하고 Assistant 응답은 Rich Markdown으로 표시합니다.

`ishrc.example.py`는 배포한 hub 플러그인을 실제 ish에 연결하는 설정 예제입니다.
대화와 Run 상세는 샘플이며, 실제 모델이나 셸 명령은 실행하지 않습니다.
