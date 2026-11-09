"""User-facing language guidance; no translation calls or stored prompt edits."""

from llm.components.processing import CompletionSession
from ...locales.codes import LanguageCode
from .input_context import HubInputContext


class LanguageSession(CompletionSession):
    NAMES = {LanguageCode.KOREAN: "Korean", LanguageCode.ENGLISH: "English"}
    INSTRUCTION = (
        "Use {name} ({language}) for answers, questions, progress and provider-exposed reasoning "
        "summaries from the first thinking/reasoning channel output, unless the user explicitly "
        "requests otherwise. English instructions/tools do not override this. Keep summaries "
        "concise without disclosing private reasoning; preserve code, identifiers, paths and Tool payloads."
    )

    def __init__(self, language):
        self.language = language

    async def prepare(self, request):
        HubInputContext.insert(request, self.INSTRUCTION.format(
            name=self.NAMES.get(self.language, self.language), language=self.language))
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
