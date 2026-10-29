"""Stateless next-call exposure within the native Agent Tool ceiling."""

import json

from llm.components.tools import ToolExposure
from llm.core.model_protocol import ModelRole
from llm.engines.loop.review import LoopReview
from llm.services.infrastructure.storage import revision_token

from .request_profile import RequestMode, RequestProfile
from .request_tools import RequestTools
from .tool_catalog import ToolCatalog
from .tool_lookup import ToolLookup
from ..tools.application import HubApplicationTools


class InvocationMode:
    def __init__(self, initial):
        self.initial = initial

    def classify(self, text):
        mode = RequestProfile.classify(text).mode
        # A polite follow-up does not abandon an ongoing investigation/work.
        return self.initial if mode == RequestMode.CONVERSATION else mode

    def messages(self, messages):
        latest = next((m for m in reversed(messages) if self.user_instruction(m)), None)
        return self.initial if latest is None else self.classify(latest.text)

    @staticmethod
    def user_instruction(message):
        # Native review feedback is a reference USER message, not steering.
        return message.role == ModelRole.USER and not message.text.startswith(LoopReview.FEEDBACK_PREFIX)

    def request(self, request, request_id):
        latest = next((m for m in reversed(request.messages) if self.user_instruction(m.message)), None)
        # Earlier processors may append reference text to the original request.
        return (self.initial if latest is None or latest.source_id == request_id
                else self.classify(latest.message.text))


class HubToolSelector:
    VERSION = "hub.tool_exposure.v1"

    def __init__(self, tools, mode, *, groups=None, discovery=False, constraints=None):
        self.catalog = ToolCatalog.from_registry(tools, extra_groups=groups)
        self.mode = InvocationMode(mode)
        self.lookup = ToolLookup(tools, groups=groups, constraints=constraints) if discovery else None
        self.revision = revision_token({
            'version': self.VERSION, 'mode': mode,
            'catalog': [(entry.name, sorted(entry.tags)) for entry in self.catalog.entries],
            'discovery': self.lookup.rows if self.lookup is not None else None,
        })

    def discovered(self, messages):
        """Only completed, bound hub_help searches in this instruction segment count.

        Recompute metadata from our immutable catalog; never trust arbitrary
        text, another Tool's result, a fabricated name, or an unresolved call.
        """
        if self.lookup is None:
            return set()
        calls, names = {}, set()
        for message in messages:
            if InvocationMode.user_instruction(message):
                calls.clear()
                names.clear()
            elif message.role == ModelRole.ASSISTANT:
                calls.update((call.id, call) for call in message.calls
                             if call.name == HubApplicationTools.HELP)
            elif message.role == ModelRole.TOOL and message.result is not None:
                result = message.result
                call = calls.pop(result.call_id, None)
                if call is None or result.name != HubApplicationTools.HELP:
                    continue
                arguments = call.arguments
                if not isinstance(arguments, dict) or ToolLookup.QUERY not in arguments:
                    continue
                try:
                    expected = self.lookup.search(arguments[ToolLookup.QUERY], arguments.get(ToolLookup.OFFSET, 0))
                    observed = json.loads(result.content)
                except (ValueError, TypeError):
                    continue
                if observed == expected:
                    names.update(row['name'] for row in expected['tools'])
        return names

    async def select_tools(self, request: ToolExposure):
        mode = self.mode.messages(request.messages)
        selected = set(RequestTools.names(self.catalog, mode))
        if mode != RequestMode.CONVERSATION:
            selected.update(self.discovered(request.messages))
        # Preserve native ordering. No registry reconstruction, handlers, or IO.
        return tuple(name for name in request.available if name in selected)
