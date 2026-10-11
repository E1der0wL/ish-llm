"""Example conversations; never imported by the deployed Hub plugin."""

from hub.model import ChatMessage, SessionView


SAMPLES = (
    SessionView(
        '프로젝트 구조 살펴보기', '완료',
        (
            ChatMessage(
                'user',
                '이 프로젝트의 실행 구조를 간단히 설명해줘.',
                '10:24',
            ),
            ChatMessage(
                'assistant',
                '### 실행 구조\n'
                '**Project**는 작업 공간이고, **Session**은 그 안에서 이어지는 대화입니다.\n'
                '\n'
                '- 메시지 하나를 보내면 하나의 **Run**이 만들어집니다.\n'
                '- 같은 Session의 요청은 순서대로 실행합니다.\n'
                '- 서로 다른 Session은 동시에 실행할 수 있습니다.\n'
                '\n'
                '```python\n'
                'request = await session.run.submit(\n'
                '    "안녕하세요", engine="loop"\n'
                ')\n'
                '```',
                elapsed_seconds=2.4,
            ),
            ChatMessage(
                'user',
                '화면을 닫아도 대화를 계속할 수 있을까?',
                '10:25',
            ),
            ChatMessage(
                'assistant',
                '네. **화면 표시와 실행 수명을 분리**하면 됩니다.\n'
                '\n'
                '| 동작 | 화면 | 실행 |\n'
                '| --- | --- | --- |\n'
                '| 패널에서 ESC | 최소화 | 유지 |\n'
                '| 명시적 중단 | 유지 | 중단 |\n'
                '\n'
                '> 현재는 UI 목업입니다. 실제 실행과 저장은 연결하지 않았습니다.',
                elapsed_seconds=1.8,
            ),
        ),
    ),
    SessionView(
        '문서 작성 계획', '예약 예시',
        (
            ChatMessage(
                'user',
                '개발 문서에 어떤 내용이 필요할까?',
                '09:42',
            ),
            ChatMessage(
                'assistant',
                '### 문서 작성 계획\n'
                '처음 사용하는 사람이 **실행까지 도달**할 수 있도록 구성해 보세요.\n'
                '\n'
                '1. 설치와 첫 실행\n'
                '2. Project와 Session 사용법\n'
                '3. 요청 대기열과 명시적 중단\n'
                '4. 재시작 후 복구\n'
                '\n'
                '설정 예시는 `ProjectConfig`를 기준으로 설명하면 됩니다.',
                elapsed_seconds=5.2,
            ),
            ChatMessage(
                'user',
                '설치 안내의 목차도 만들어줘.',
                '09:43',
                status='queued',
            ),
        ),
    ),
    SessionView(
        '새로운 아이디어', '빈 대화',
        (
            ChatMessage(
                'info',
                '무엇부터 시작할까요?\n'
                '\n'
                '코드에 대해 질문하거나, 문서를 정리하거나, 새로운 아이디어를 적어보세요.\n'
                '\n'
                '아래 입력창에서 글을 작성해 볼 수 있습니다.\n'
                '이 목업에서는 메시지를 보내거나 저장하지 않습니다.',
            ),
        ),
    ),
)
