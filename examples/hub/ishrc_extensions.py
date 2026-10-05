"""Copy into .ishrc.py to customize the builtin components and Graph handlers."""

from pathlib import Path

from llm.components.agents import AgentComponent
from llm.components.memory import MemoryComponent
from llm.components.prompts import PromptComponent
from llm.components.skills import SkillComponent
from llm.components.tools import ToolComponent
from llm.components.workflows import WorkflowComponent
from llm.engines.graph import GraphEngine
from llm.engines.graph.agent import AgentNode
from llm.engines.graph.tool import ToolNode
from llm.engines.loop import LoopEngine


def make_graph():
    return GraphEngine(handlers={
        "agent": AgentNode(engines={"loop": LoopEngine()}),
        "tool": ToolNode(),
    })


# plugin and prompt are provided by the ish .ishrc.py loader.
hub_plugin = plugin.get("hub")
if hub_plugin is not None:
    hub = hub_plugin.install(
        prompt,
        config=hub_plugin.HubConfig(
            workspace=Path.home() / ".ish/hub-workspace",
            engine="loop",
            model="gemini/gemini-3.6-flash",  # Keep your existing working model.
            language="ko",
            engine_factories={"graph": make_graph},
            component_factories=(  # Additions / overrides; other builtins stay registered.
                ToolComponent, WorkflowComponent, AgentComponent,
                MemoryComponent, PromptComponent, SkillComponent,
            ),
        ),
    )
