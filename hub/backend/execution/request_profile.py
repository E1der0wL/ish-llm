"""Hub request selection, captured before durable admission; never permission."""

from dataclasses import dataclass
from enum import StrEnum
import re

from llm.core.configuration import UNSET
from .completion_review import ConversationalInput


class RequestMode(StrEnum):
    CONVERSATION = "conversation"
    LOOKUP = "lookup"
    WORK = "work"


@dataclass(frozen=True, slots=True)
class RequestProfile:
    mode: RequestMode
    KEY = "_hub_request"
    MODE = "mode"
    INVALID = "Invalid Hub request profile"
    MAX_CLASSIFY_CHARS = 4096
    # Conservative language hints select a smaller toolbox, never authorize an
    # effect. Mixed/action/large/ambiguous requests retain the Agent's toolbox.
    ACTION = re.compile(
        r"수정|생성|작성|삭제|실행|설치|적용|반영|추가|업로드|커밋|이동|저장|편집|고쳐|지워|만들|돌려|예약|보내"
        r"|\b(write|create|modify|implement|update|delete|remove|execute|run|install|build|fix|repair|apply|save|commit|push|send|deploy|move|copy|schedule)\b",
        re.IGNORECASE,
    )
    INFORMATION = re.compile(
        r"알려|설명|요약|비교|찾아|조회|보여|검토|읽어|무엇|뭐야|왜|어디|어떤|어떻게"
        r"|\b(what|why|how|where|when|who|explain|summarize|describe|compare|review|find|search|show|read|list|inspect)\b",
        re.IGNORECASE,
    )
    ACTION_TOPIC = re.compile(
        r"(?:작성|생성|수정|삭제|실행|설치|저장|편집)(?:하는|할)?\s*(?:방법|예시|예제)"
        r"|\b(?:how\s+to|example\s+of)\s+(?:write|create|modify|delete|execute|run|install|save|edit)\b",
        re.IGNORECASE,
    )

    @classmethod
    def classify(cls, text: str):
        if ConversationalInput.matches(text):
            return cls(RequestMode.CONVERSATION)
        # An action mentioned as an explanation topic is not an instruction to
        # perform it. Any separate action clause still keeps the full toolbox.
        candidate = cls.ACTION_TOPIC.sub("", text[:cls.MAX_CLASSIFY_CHARS])
        if len(text) <= cls.MAX_CLASSIFY_CHARS and not cls.ACTION.search(candidate) and cls.INFORMATION.search(text):
            return cls(RequestMode.LOOKUP)
        return cls(RequestMode.WORK)

    @classmethod
    def capture(cls, text: str, context=UNSET):
        result = {} if context is UNSET else dict(context)
        result[cls.KEY] = {cls.MODE: cls.classify(text).mode.value}
        return result

    @classmethod
    def for_engines(cls, text, context, engines):
        from .environment import EnvironmentEngine
        # Shared multi-recipient context must be understood by every Engine.
        # Custom backends may supply raw Engines without the Hub context hook.
        if engines and all(isinstance(engine, EnvironmentEngine) for engine in engines):
            return cls.capture(text, context)
        if context is UNSET or cls.KEY not in context:
            return context
        result = dict(context)
        result.pop(cls.KEY)
        return result

    @classmethod
    def take(cls, context):
        if cls.KEY not in context:
            return None  # Ordinary backend requests keep their own selection.
        value = context.pop(cls.KEY)
        if not isinstance(value, dict) or set(value) != {cls.MODE}:
            raise ValueError(cls.INVALID)
        try:
            return cls(RequestMode(value[cls.MODE]))
        except (ValueError, TypeError):
            raise ValueError(cls.INVALID) from None
