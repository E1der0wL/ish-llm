"""Prompt definition fields and message projections; execution belongs to consumers."""

from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum

from llm.core.configuration import UNSET
from llm.core.model_protocol import ModelMessage, ModelRole
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean, metadata_schema


@dataclass(frozen=True, slots=True)
class PromptMessage:
    """A projection of already selected message data, not a second stored record."""

    MINIMUM_CONTENT = 1
    STORED_ROLES = (ModelRole.SYSTEM.value, ModelRole.USER.value, ModelRole.ASSISTANT.value)

    role: str
    content: str

    @classmethod
    def schema(cls):
        return {
            SchemaKeyword.TYPE: SchemaType.OBJECT,
            SchemaKeyword.REQUIRED: [cls.role.__name__, cls.content.__name__],
            SchemaKeyword.PROPERTIES: {
                cls.role.__name__: {SchemaKeyword.ENUM: list(cls.STORED_ROLES)},
                cls.content.__name__: {SchemaKeyword.TYPE: SchemaType.STRING,
                                       SchemaKeyword.MIN_LENGTH: cls.MINIMUM_CONTENT},
            },
            SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
        }

    @classmethod
    def read(cls, value):
        return cls(value[cls.role.__name__], value[cls.content.__name__])

    def to_dict(self):
        return {type(self).role.__name__: self.role, type(self).content.__name__: self.content}

class PromptDefinition:
    CAPABILITY = "prompts"

    class Field(StrEnum):
        MESSAGES = "messages"
        DESCRIPTION = "description"
        METADATA = "metadata"

    MINIMUM_MESSAGES = 1
    SCHEMA = {
        SchemaKeyword.TYPE: SchemaType.OBJECT,
        SchemaKeyword.REQUIRED: [Field.MESSAGES.value],
        SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT,
        SchemaKeyword.PROPERTIES: {
            Field.DESCRIPTION.value: {SchemaKeyword.TYPE: SchemaType.STRING},
            Field.METADATA.value: metadata_schema(),
            Field.MESSAGES.value: {
                SchemaKeyword.TYPE: SchemaType.ARRAY, SchemaKeyword.MIN_ITEMS: MINIMUM_MESSAGES,
                SchemaKeyword.ITEMS: PromptMessage.schema(),
            },
        },
    }

    @classmethod
    def message_values(cls, value):
        """Required field access; an explicit runtime empty list remains empty."""
        return (PromptMessage.read(item) for item in value[cls.Field.MESSAGES.value])

    @classmethod
    def model_messages(cls, value):
        """Normalize a runtime selection at the existing model-message boundary."""
        return (ModelMessage(ModelRole(item[PromptMessage.role.__name__]), item[PromptMessage.content.__name__])
                for item in value[cls.Field.MESSAGES.value])

    @classmethod
    def create(cls, messages, *, description=UNSET, metadata=UNSET):
        """Explicit construction only; stored definitions still validate the full Schema."""
        value = {}
        if description is not UNSET:
            value[cls.Field.DESCRIPTION.value] = deepcopy(description)
        if metadata is not UNSET:
            value[cls.Field.METADATA.value] = deepcopy(metadata)
        value[cls.Field.MESSAGES.value] = [message.to_dict() for message in messages]
        return value
