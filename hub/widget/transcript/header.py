"""One display-only header grammar for messages and execution observations."""

from llm.core.models import MessageRole
from ...locales import Language
from ...locales.keys import MessageKey
from ...model import ChatMessage, ReasoningMessage
from ..captions import Caption


class OutputHeader(Caption):
    """Target • status glyph and label • optional details, without empty slots.

    Language.status supplies the state part; callers never parse localized text.
    Colors, alignment, wrapping and cache lifetimes remain with each surface.
    """

    __slots__ = ()
    MAX_TOOL_NAME = 64

    @classmethod
    def message(cls, message: ChatMessage, language: Language, account_name: str):
        role = message.role
        if role == ReasoningMessage.ROLE:
            return cls(message.author, details=(language(MessageKey.REASONING),))
        if role == MessageRole.SYSTEM:
            return cls(language(MessageKey.SYSTEM), details=(message.time,))
        target = ((message.author or account_name) if role == MessageRole.USER
                  else (message.author or language(MessageKey.ASSISTANT))
                  if role == MessageRole.ASSISTANT else language(MessageKey.INFO))
        status = message.activity or language.status(message.phase or message.status)
        detail = (language.elapsed(message.elapsed_seconds)
                  if role == MessageRole.ASSISTANT else message.time)
        return cls(target, status, (detail,))

    @classmethod
    def tool(cls, name: str, status: str, language: Language, detail: str = ""):
        target = language.icons.tool + " " + " ".join(name.split())[:cls.MAX_TOOL_NAME]
        return cls(target, language.status(status), (detail,))
