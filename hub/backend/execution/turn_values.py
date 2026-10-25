"""Hub edit lineage inside the existing Agent request context; no execution authorization."""

from llm.core.agents import AgentBinding


class HubTurnContext:
    KEY = "_hub_turn"
    SOURCE = "source"
    ABSENT = None
    NO_SOURCE = ""
    INVALID = "Invalid Hub turn reference"

    @classmethod
    def source(cls, message):
        context = AgentBinding.request_context(message.metadata, default={})
        value = context.get(cls.KEY, {})
        return (
            value.get(cls.SOURCE, cls.NO_SOURCE)
            if isinstance(value, dict)
            else cls.NO_SOURCE
        )

    @classmethod
    def with_source(cls, metadata, source):
        context = dict(AgentBinding.request_context(metadata, default={}))
        context[cls.KEY] = {cls.SOURCE: source}
        return context

    @classmethod
    def remove(cls, metadata):
        AgentBinding.request_context(metadata, default={}).pop(cls.KEY, cls.ABSENT)

    @classmethod
    def take(cls, options):
        turn = options.pop(cls.KEY, cls.ABSENT)
        if turn is not cls.ABSENT and (
            not isinstance(turn, dict)
            or set(turn) != {cls.SOURCE}
            or not isinstance(turn[cls.SOURCE], str)
            or not turn[cls.SOURCE]
        ):
            raise ValueError(cls.INVALID)
        return turn
