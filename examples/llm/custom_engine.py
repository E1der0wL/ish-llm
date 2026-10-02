"""API 키 없이 개발자 Engine을 실행하는 예제: python -m examples.llm.custom_engine."""

import asyncio
from tempfile import TemporaryDirectory

from llm.engines import EngineContext, BaseEngine
from llm.engines.base import EngineEventType
from llm.llm import LargeLanguageModel


class EchoEngine(BaseEngine):
    # 작업만 구현한다. Step 수명 주기/파일 저장은 BaseEngine과 서비스가 처리한다.
    async def run(self, context: EngineContext):
        yield "Echo: "
        yield context.messages[-1].content


async def main():
    # 임시 workspace를 사용하며 API 키나 네트워크가 필요하지 않다.
    with TemporaryDirectory(prefix="llm-example-") as directory:
        def display(run, event):
            if event.type == EngineEventType.TEXT_DELTA and event.delta.visibility == "user":
                print(event.delta.text, end="", flush=True)

        async with LargeLanguageModel(
            directory, engines={"echo": EchoEngine("Echo response", kind="text")},
            on_event=display,
        ) as backend:
            project = await backend.projects.acreate("Example")
            session = await project.sessions.acreate("Conversation")
            request = await session.run.submit("hello", engine="echo")
            await request.wait()
            print()


if __name__ == "__main__":
    asyncio.run(main())
