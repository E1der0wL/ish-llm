"""명시적으로 선택한 Skill·Data·Tool 참조 예제. 모델/네트워크/Tool 실행은 없다.

저장소 루트에서 Linux Python 3.12.14로 실행:
    python -m examples.llm.skill_data --workspace /tmp/skill-data-example
매 실행마다 새 Project를 생성하며 기존 Project/파일을 덮어쓰지 않는다.
"""

import argparse
import asyncio
from pathlib import Path

from llm.llm import LargeLanguageModel


async def create_example(app):
    project = await app.projects.acreate("Skill and Data", components=["skills", "data", "tools"])
    skills = await project.components.aget("skills")
    data = await project.components.aget("data")
    tools = await project.components.aget("tools")
    await data.acreate({"title": "공유 참고 자료"}, identifier="manual")
    manual = await data.asnapshot("manual")
    manual = await data.awrite_file("manual", "reference.md", "# Reference\nUse explicit input values.\n",
                                    expected_revision=manual["version"])
    await tools.acreate({"source": 'from llm.components.tools import tool\n@tool()\nasync def main(text: str):\n    """Return supplied text."""\n    return text\n'}, identifier="echo")
    tool = await tools.asnapshot("echo")
    await skills.acreate({"instructions": "Read the reference and validate the result."}, identifier="guide")
    guide = await skills.asnapshot("guide")
    guide = await skills.awrite_file("guide", "examples/input.txt", "sample input",
                                     expected_revision=guide["version"])
    await skills.aupdate("guide", {"resources": [
        {"path": "examples/input.txt"},
        {"component": "data", "identifier": "manual", "path": "reference.md", "revision": manual["version"]},
        {"component": "tools", "identifier": "echo", "revision": tool["version"]},
    ]}, expected_version=guide["version"])
    guide = await skills.asnapshot("guide")
    reference = await skills.aread_resource("guide", 1, expected_revision=guide["version"])
    # Tool는 여전히 미활성이다. 실제 사용에는 기존 준비·선택·승인 API를 사용한다.
    return project, reference


async def main(workspace):
    async with LargeLanguageModel(workspace) as app:
        project, reference = await create_example(app)
        print(f"Project: {project.id}")
        print(reference["text"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    asyncio.run(main(parser.parse_args().workspace))
