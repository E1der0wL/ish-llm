"""Management widget presentation and draft rules; no Backend execution policy."""

import json
from llm.components.naming import ComponentName
from llm.components.memory.records import MemoryRecord
from llm.components.prompts.records import PromptDefinition, PromptMessage
from llm.components.workflows.graph import WorkflowGraph, ControlNodeType
from llm.components.workflows.contracts import WorkflowDocument, WorkflowNode
from llm.core.agents import AgentDefinition
from llm.core.model_protocol import ModelRole
from hub.backend.execution.engine_selection import HubEngineIdentity
from hub.components import ManagedComponents
from hub.locales.keys import MessageKey


class ManagerView:
    DELETE_TEXT = MessageKey.SETTINGS_DELETE
    EMPTY = ""
    ABSENT = None
    BUSY = True
    IDLE = False
    DIRECTORIES = False
    TOGGLE = False
    OPEN_DIRECTORIES = True
    ENABLE_TOGGLE = True
    DETAIL_LINES = 1
    BUTTON_WIDTH = 12
    COMPONENT_SETTINGS = True
    ROW_SPACER = 1
    MINIMUM_LIST_HEIGHT = 2
    MAXIMUM_LIST_HEIGHT = 18
    VERTICAL_CHROME = 14
    WRAP_LINES = True
    SCROLL_ARROWS = True
    FIRST = 0
    NEXT = 1
    PREVIOUS = -1
    DATE_FORMAT = "%Y-%m-%d %H:%M"
    UNKNOWN = "—"
    ENABLED_MARK = "[x] "
    DISABLED_MARK = "[ ] "
    SEPARATOR = " • "
    NO_SETTINGS = "This domain has no Component configuration"
    HANDLED = True
    NOT_CONFIRMED = False
    CONFIRMED = True
    COMMAND_PREFIX = "/"
    SPACE = " "
    CANCEL_BUTTON = 1

    @classmethod
    def command(cls, name, action):
        return f"{cls.COMMAND_PREFIX}{name}{cls.SPACE}{action}"


class ManagerCallbacks:
    PROGRESS = "progress"
    OBSERVE = "observe"

    @classmethod
    def options(cls, observe, progress):
        return (
            {cls.PROGRESS: lambda update: observe(update, progress)} if observe else {}
        )

    @classmethod
    def observe(cls, options, callback):
        options[cls.OBSERVE] = callback


class DefinitionDraft:
    SYSTEM_TEXT = "You are a helpful assistant."
    FINAL_NODE = "done"
    EMPTY_CONTENT = ""
    MAXIMUM_TEXT = 1024 * 1024

    @classmethod
    def create(cls, name):
        return {
            ManagedComponents.PROMPTS.name: PromptDefinition.create(
                [PromptMessage(ModelRole.SYSTEM.value, cls.SYSTEM_TEXT)]
            ),
            ManagedComponents.AGENTS.name: {
                AgentDefinition.Field.ENGINE.value: HubEngineIdentity.LOOP
            },
            ManagedComponents.WORKFLOWS.name: {
                WorkflowDocument.SCHEMA_VERSION: WorkflowGraph.SCHEMA_VERSION,
                WorkflowDocument.ENTRY: cls.FINAL_NODE,
                WorkflowDocument.NODES: {
                    cls.FINAL_NODE: {WorkflowNode.TYPE: ControlNodeType.END.value}
                },
                WorkflowDocument.EDGES: [],
            },
            ManagedComponents.MEMORY.name: {
                MemoryRecord.Field.CONTENT: cls.EMPTY_CONTENT,
                MemoryRecord.Field.KIND: MemoryRecord.INITIAL_KIND,
                MemoryRecord.Field.SCOPE: MemoryRecord.PROJECT_SCOPE,
                MemoryRecord.Field.STATUS: MemoryRecord.CONFIRMED,
                MemoryRecord.Field.TAGS: [],
            },
        }[name]

    @classmethod
    def require_size(cls, text, translate):
        if len(text) > cls.MAXIMUM_TEXT:
            raise ValueError(translate(MessageKey.DEFINITION_TOO_LARGE))

    @staticmethod
    def parse_object(text, translate):
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError(translate(MessageKey.DEFINITION_OBJECT))
        return value

    @staticmethod
    def require_name(name, translate):
        if not ComponentName.matches(name):
            raise ValueError(translate(MessageKey.DEFINITION_NAME_INVALID))


class DefinitionView:
    BUTTON_WIDTH = 10
    JSON_LABEL = "JSON"
    MULTILINE = True
    SINGLE_LINE = False
    SCROLLBAR = True
    WRAP_LINES = False
    READ_ONLY = True
    IDENTIFIER_HEIGHT = 1
    PREVIEW_PADDING = 1
    MINIMUM_PREFERRED_HEIGHT = 8
    VERTICAL_MARGIN = 8
    MINIMUM_HEIGHT = 4
    PREVIEW_DELAY = 0.1
    NEWLINE = "\n"


class RAGManagerView:
    INITIAL_EPOCH = 0
    NEXT_EPOCH = 1
    POLLING = True
    IDLE = False
    BUTTON_WIDTH = 10
    DETAIL_LINES = 4
    SEPARATOR = ManagerView.SEPARATOR
    NEWLINE = "\n"
    UNKNOWN = ManagerView.UNKNOWN
    ZERO = 0
    SECONDS_PREFIX = " "
    SECONDS_SUFFIX = "s"
    POLL_SECONDS = 1
    CANCEL_NOTICE_SECONDS = 5
    PROGRESS_PREFIX = "RAG • "
    INDETERMINATE = True
    VISION_BUTTON_WIDTH = 14
    PRIMARY_BUTTON = 0
    CANCEL_BUTTON = 1
