"""User-facing language guidance; no translation calls or stored prompt edits."""

from llm.components.processing import CompletionSession
from .input_context import HubInputContext


class LanguageSession(CompletionSession):
    def __init__(self, language):
        self.language = language

    async def prepare(self, request):
        HubInputContext.insert(request,
            f"Use the user's preferred language ({self.language}) for answers, questions, "
            "progress descriptions and any reasoning summaries you expose, unless the user asks "
            "otherwise. Preserve code, identifiers and paths. Do not translate tool payloads.")
        if False:
            yield


class LanguageProcessor:
    name = 'hub.language'
    priority = 1000
    close_timeout = None

    def __init__(self, language):
        self.language = language

    def session(self, context):
        return LanguageSession(self.language)
