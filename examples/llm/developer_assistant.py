"""소스 수정·문제 분석과 Skill 탐색을 연결하는 명시적 설정 예제. 자동 작업 성공을 보장하지 않는다."""

import argparse
import asyncio
import json
from pathlib import Path

from llm.components.skills import SkillComponent
from llm.components.tools.builtin import BuiltinTools, BuiltinToolComponent
from llm.core.models import ProjectConfig, RunStatus
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel, print_event


def starter_skills():
    """Explicit, editable standard Skill packages for this example."""
    from llm.core.skills import SkillDocument
    from llm.components.skills import SkillRecord
    return {path.name: {SkillRecord.MARKDOWN: (path / SkillDocument.FILENAME).read_text(encoding="utf-8")}
            for path in sorted(Path(__file__).with_name("skills").iterdir()) if path.is_dir()}


async def install_guides(project):
    """명시적으로 요청했을 때만 설치하며 사용자가 편집한 기존 지침은 유지한다."""
    skills = await project.components.aget("skills")
    existing = await skills.alist()
    for identifier, definition in starter_skills().items():
        if identifier not in existing:
            await skills.acreate(definition, identifier=identifier)


async def run_request(args):
    config = json.loads(args.config.read_text(encoding="utf-8"))
    work, workspace = args.workdir.resolve(), args.workspace.resolve()
    if workspace == work or work in workspace.parents:
        raise ValueError("Place the backend workspace outside the editable workdir")
    async with BuiltinTools(work, **config.get("builtin", {})) as toolkit:
        async with LargeLanguageModel(workspace,
                components=[BuiltinToolComponent(toolkit, name="computer"), SkillComponent()],
                engines={"loop": LoopEngine()}) as backend:
            backend.events.subscribe(print_event, channel="engine")
            project = (await backend.projects.aload(args.project_id) if args.project_id else
                       await backend.projects.acreate("Developer assistant", components=["computer", "skills"],
                           config=ProjectConfig(config["project_config"])))
            if args.install_guides:
                await install_guides(project)
            if not args.project_id:
                selection = await (await project.components.aget("computer")).aget_config()
                selected = selection.get("config", {}).get("enabled", [])
                definition = {"engine": "loop",
                    "completion": config["agent"]["completion"],
                    "tools": [*selected, "skill_list", "skill_read", "skill_read_resource"]}
                if "system_prompt" in config["agent"]:
                    definition["system_prompt"] = config["agent"]["system_prompt"]
                await project.agents.acreate(definition, identifier=args.agent_id)
            session = await project.sessions.acreate("Programming / diagnosis", agent_ids=[args.agent_id])
            print(f"Project: {project.id}\nSession: {session.id}")
            run = (await (await session.run.submit(args.prompt, recipients=[args.agent_id])).wait())[0]
            result = await run.aresult()
            print(f"\nRun: {run.id} / {result.status.value}")
            if result.status != RunStatus.COMPLETED:
                print(result.error_code or result.error or result.status.value)
                return 1
            return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--project-id", help="기존 설정·Skill을 사용할 Project ID. project_config는 다시 적용하지 않음")
    parser.add_argument("--install-guides", action="store_true", help="아직 없는 예제 Skill을 명시적으로 추가")
    parser.add_argument("--agent-id", default="developer", help="저장된 실행 Agent ID")
    parser.add_argument("prompt")
    return asyncio.run(run_request(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
