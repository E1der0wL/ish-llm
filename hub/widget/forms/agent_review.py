"""Optional review selection; schema validation and parent limits remain authoritative."""

from copy import deepcopy
from prompt_toolkit.layout.dimension import Dimension
from llm.core.agents import AgentDefinition
from llm.core.implementation_parameters import ImplementationParameters
from llm.engines.loop.configuration import LoopParameters

from hub.config.defaults import HubDefaults
from hub.locales.keys import MessageKey
from hub.widget.dropdown import DropdownBox
from .settings import MISSING, at_path, put_path


class AgentReview:
    PATH = (AgentDefinition.Field.ENGINE_OPTIONS.value, ImplementationParameters.POLICY,
            LoopParameters.FINAL_REVIEW)
    INHERIT, ENABLED, DISABLED = "inherit", "enabled", "disabled"

    def __init__(self, editor, schema, definition):
        self.editor = editor
        node = schema
        for key in self.PATH:
            node = node.get("properties", {}).get(key, {})
        self.available = bool(node)
        self.original = at_path(definition, self.PATH)
        self.initial = (self.INHERIT if self.original is MISSING else
                        self.DISABLED if self.original is None else self.ENABLED)
        self.choice = DropdownBox(lambda: [
            (self.INHERIT, editor.view.t(MessageKey.AGENT_REVIEW_INHERIT)),
            (self.ENABLED, editor.view.t(MessageKey.AGENT_REVIEW_ON)),
            (self.DISABLED, editor.view.t(MessageKey.AGENT_REVIEW_OFF)),
        ], value=self.initial, icons=lambda: editor.view.icons, width=24)
        self.choice.button.window.width = Dimension(min=1, weight=1)

    def apply(self, definition):
        if not self.available or self.choice.value == self.initial:
            return
        value = (MISSING if self.choice.value == self.INHERIT else
                 None if self.choice.value == self.DISABLED else deepcopy(HubDefaults.FINAL_REVIEW))
        put_path(definition, self.PATH, value)
