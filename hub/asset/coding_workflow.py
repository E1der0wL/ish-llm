"""Editable starter Workflow data, built using the public graph definition API."""

from pathlib import Path

from llm.components.workflows import WorkflowGraph
from llm.core.agents import AgentDefinition


class CodingWorkflow:
    DIRECTORY = Path(__file__).parent / "prompts" / "coding-workflow"
    STAGES = ("inspect", "implement", "verify", "report")
    COMMON = 'common.md'
    ENCODING = 'utf-8'
    STAGE_FILE = '{stage}.md'
    PARAGRAPH = '\n\n'
    REQUEST = 'request'
    INSTRUCTIONS = 'instructions'
    REPORT = 'report'
    PROMPT_PATH = '/prompt'
    REPORT_PATH = '/report'
    REQUEST_PATH = '/request'
    INSTRUCTION_PATH = '/instructions/{stage}'
    STAGE_PATH = '/{stage}'
    TEXT_PATH = '/text'
    AGENT_HANDLER = 'agent'
    END = 'end'

    @classmethod
    def definition(cls, agent_id: str) -> dict:
        common = (cls.DIRECTORY / cls.COMMON).read_text(encoding=cls.ENCODING)
        instructions = {stage: common + cls.PARAGRAPH + (cls.DIRECTORY / cls.STAGE_FILE.format(stage=stage)).read_text(encoding=cls.ENCODING)
                        for stage in cls.STAGES}
        graph = WorkflowGraph(entry=cls.STAGES[0], inputs={cls.REQUEST: cls.PROMPT_PATH},
                              initial_state={cls.INSTRUCTIONS: instructions},
                              outputs={cls.REPORT: cls.REPORT_PATH})
        for index, stage in enumerate(cls.STAGES):
            graph.node(stage, cls.AGENT_HANDLER, agent=agent_id,
                       output_format=AgentDefinition.OutputFormat.TEXT.value,
                       emit_text=stage == cls.STAGES[-1],
                       inputs={cls.REQUEST: cls.REQUEST_PATH, cls.INSTRUCTIONS: cls.INSTRUCTION_PATH.format(stage=stage),
                               **{previous: cls.STAGE_PATH.format(stage=previous) for previous in cls.STAGES[:index]}},
                       outputs={stage: cls.TEXT_PATH})
            if index:
                graph.connect(cls.STAGES[index - 1], stage)
        return graph.node(cls.END, cls.END).connect(cls.STAGES[-1], cls.END).to_dict()
