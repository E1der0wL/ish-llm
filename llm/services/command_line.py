"""Declared command-line inputs and demo payloads; the entry point owns execution."""

import argparse
from pathlib import Path
from llm.core.models import ConversationStorage
from llm.core.agents import AgentDefinition
from llm.core.implementation_parameters import ImplementationParameters
from llm.engines.loop.configuration import LoopParameters
from llm.providers.request_values import LiteLLMParameters
from llm.components.tools.package_values import ToolPackageSource


class CommandLine:
    PROGRAM = "llm"
    ENGINE = "--engine"
    MODEL = "--model"
    PROMPT = "--prompt"
    WORKSPACE = "--workspace"
    CONVERSATION_STORAGE = "--conversation-storage"
    API_BASE = "--api-base"
    TEMPERATURE = "--temperature"
    MAX_ITERATIONS = "--max-iterations"
    TIMEOUT = "--timeout"
    WITH_TOOLS = "--with-tools"
    REQUIRED = True
    UNSPECIFIED = None
    STORE_TRUE = "store_true"
    ENGINE_HELP = "Execution Engine (the command currently registers loop)"
    MODEL_HELP = "LiteLLM provider/model identifier"
    STORAGE_HELP = "Message storage; memory is lost when this command exits"
    ENDPOINT_HELP = "Optional OpenAI-compatible endpoint URL"
    TEMPERATURE_HELP = "Omit for model default"
    TOOLS_HELP = "Register the example add tool"
    TOOL_ID = "add"
    AGENT_ID = "demo"
    PROJECT_TITLE = "LoopEngine demo"
    SESSION_TITLE = "Streaming request"
    PROJECT_LOCATION = "Project: {path}"
    RUN_FAILURE = "Run {status}: {error}"
    INTERRUPTED_ERROR = "interrupted"
    SUCCESS = 0
    FAILURE = 1
    INTERRUPTED = 130
    STREAM_END = ""
    FLUSH_STREAM = True

    TOOL_SOURCE = "\n".join(
        [
            "from llm.components.tools import tool, ToolContract, ToolEffect, ToolIsolation",
            '@tool(ToolContract(revision="1", effect=ToolEffect.READ_ONLY, isolation=ToolIsolation.NONE, approval_required=False, operation_key_required=False, retry_fields=()))',
            "async def main(a: float, b: float) -> dict:",
            '    """Add two numbers."""',
            '    return {"result": a + b}',
            "",
        ]
    )

    @classmethod
    def parser(cls, description, *, engine, workspace):
        parser = argparse.ArgumentParser(prog=cls.PROGRAM, description=description)
        parser.add_argument(cls.ENGINE, required=cls.REQUIRED, choices=(engine,), help=cls.ENGINE_HELP)
        parser.add_argument(cls.MODEL, required=cls.REQUIRED, help=cls.MODEL_HELP)
        parser.add_argument(cls.PROMPT, required=cls.REQUIRED)
        parser.add_argument(cls.WORKSPACE, type=Path, default=Path(workspace))
        parser.add_argument(
            cls.CONVERSATION_STORAGE,
            choices=(ConversationStorage.FILE.value, ConversationStorage.MEMORY.value),
            default=ConversationStorage.FILE.value,
            help=cls.STORAGE_HELP,
        )
        parser.add_argument(cls.API_BASE, help=cls.ENDPOINT_HELP)
        parser.add_argument(cls.TEMPERATURE, type=float, default=cls.UNSPECIFIED, help=cls.TEMPERATURE_HELP)
        parser.add_argument(cls.MAX_ITERATIONS, type=int)
        parser.add_argument(cls.TIMEOUT, type=float)
        parser.add_argument(cls.WITH_TOOLS, action=cls.STORE_TRUE, help=cls.TOOLS_HELP)
        return parser

    @classmethod
    def parameters(cls, args):
        parameters = ImplementationParameters.read({}).with_policy(
            {
                key: value
                for key, value in {
                    LoopParameters.MAX_ITERATIONS: args.max_iterations,
                    LoopParameters.REQUEST_TIMEOUT: args.timeout,
                }.items()
                if value is not cls.UNSPECIFIED
            }
        )
        return parameters

    @classmethod
    def agent_definition(cls, args):
        return {
            AgentDefinition.Field.ENGINE.value: args.engine,
            AgentDefinition.Field.COMPLETION.value: {
                key: value
                for key, value in {
                    LiteLLMParameters.MODEL: args.model,
                    LiteLLMParameters.TEMPERATURE: args.temperature,
                    LiteLLMParameters.API_BASE: args.api_base,
                }.items()
                if value is not cls.UNSPECIFIED
            },
            AgentDefinition.Field.TOOLS.value: [cls.TOOL_ID] if args.with_tools else [],
        }

    @staticmethod
    def tool_definition(source):
        return {ToolPackageSource.source.__name__: source}
