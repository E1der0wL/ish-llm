"""Editable starter Workflow data, built using the public graph definition API."""

from pathlib import Path

from llm.components.workflows import WorkflowGraph
from llm.core.agents import AgentDefinition


class CodingWorkflow:
    DIRECTORY = Path(__file__).parent / "prompts" / "coding-workflow"
    STAGES = ("inspect", "implement", "verify", "report")

    @classmethod
    def definition(cls, agent_id: str) -> dict:
        common = (cls.DIRECTORY / "common.md").read_text(encoding="utf-8")
        instructions = {stage: common + "\n\n" + (cls.DIRECTORY / f"{stage}.md").read_text(encoding="utf-8")
                        for stage in cls.STAGES}
        graph = WorkflowGraph(entry=cls.STAGES[0], inputs={"request": "/prompt"},
                              initial_state={"instructions": instructions},
                              outputs={"report": "/report"})
        for index, stage in enumerate(cls.STAGES):
            graph.node(stage, "agent", agent=agent_id,
                       output_format=AgentDefinition.OutputFormat.TEXT.value,
                       emit_text=stage == cls.STAGES[-1],
                       inputs={"request": "/request", "instructions": f"/instructions/{stage}",
                               **{previous: f"/{previous}" for previous in cls.STAGES[:index]}},
                       outputs={stage: "/text"})
            if index:
                graph.connect(cls.STAGES[index - 1], stage)
        return graph.node("end", "end").connect(cls.STAGES[-1], "end").to_dict()
