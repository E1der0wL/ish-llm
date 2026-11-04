"""Nonblocking question provenance; answers are input, never execution authority."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from llm.core.contracts import JsonValue
from llm.core.interactions import InteractionRequest, InteractionOption, InteractionView


class QuestionMode(StrEnum):
    BLOCKING = 'blocking'
    NONBLOCKING = 'nonblocking'


def validate_question(request):
    if not isinstance(request, InteractionRequest):
        raise TypeError('Question requires an InteractionRequest')
    if request.kind not in (InteractionRequest.KIND_INPUT, InteractionRequest.KIND_CHOICE):
        raise ValueError('Questions cannot request execution approval')
    if any(option.effect != InteractionOption.EFFECT_SELECT for option in request.options):
        raise ValueError('Question options cannot approve or deny execution')


@dataclass(frozen=True, slots=True)
class QuestionPublication(JsonValue):
    METADATA: ClassVar[str] = 'question_ids'
    run_id: str
    step_id: str
    request: dict

    def __post_init__(self):
        super(QuestionPublication, self).__post_init__()
        if any(not isinstance(value, str) or not value for value in (self.run_id, self.step_id)):
            raise ValueError('Question publication requires Run and Step provenance')
        validate_question(self.parsed_request)
        if self.parsed_request.binding:
            raise ValueError('Nonblocking questions cannot bind resume or approval decisions')

    @property
    def parsed_request(self):
        return InteractionRequest.from_dict(self.request)

    @property
    def delivery_key(self):
        request = self.parsed_request
        # One logical delivery per question revision, reusing the intake owner's
        # idempotency receipt rather than creating another execution ledger.
        return f'question:{self.run_id}:{request.id}:{request.revision}'

    @classmethod
    def identifiers(cls, metadata):
        if cls.METADATA not in metadata:
            return ()
        identifiers = metadata[cls.METADATA]
        if not isinstance(identifiers, list) or len(set(identifiers)) != len(identifiers):
            raise ValueError('Invalid question publication manifest')
        for identifier in identifiers:
            InteractionRequest.validate_identity(identifier)
        return tuple(identifiers)


@dataclass(frozen=True, slots=True, kw_only=True)
class QuestionView(InteractionView):
    source_run_id: str
    source_step_id: str

    def to_dict(self):
        return {**super(QuestionView, self).to_dict(),
                'source_run_id': self.source_run_id, 'source_step_id': self.source_step_id}
