"""Explicit Provider retry/deadline configuration; RetryPolicy alone calculates attempts."""

import math

from llm.core.schema import (SchemaType, SchemaKeyword, SchemaExtension, SchemaNarrowing,
                             SchemaBoolean, object_schema, field)
from llm.policies.retry import RetryPolicy


class ProviderOptions:
    class Field:
        MAX_ATTEMPTS = "max_attempts"
        WALL_TIMEOUT = "wall_timeout"
        DELAY_SECONDS = "delay_seconds"
        MAX_DELAY_SECONDS = "max_delay_seconds"
        RESPECT_RETRY_AFTER = "respect_retry_after"
        JITTER = "jitter"

    UNSPECIFIED = None
    SELECTED = True
    MINIMUM_SECONDS = 0
    INVALID_OBJECT = "Provider options must be an object"
    INVALID_ATTEMPTS = "Provider max_attempts requires an integer"
    INVALID_TIMING = "Provider timing configuration must be finite"
    RETRY_AFTER_DESCRIPTION = "서버 Retry-After를 최소 대기로 존중. 대기 상한을 넘으면 재시도 중단"
    JITTER_DESCRIPTION = "명시된 백오프 범위에서 대기를 분산. Retry-After 최소 대기는 유지"
    TIMING_FIELDS = (Field.WALL_TIMEOUT, Field.DELAY_SECONDS, Field.MAX_DELAY_SECONDS)

    def __init__(self, value):
        # Read an already selected mapping without revalidating or materializing defaults.
        self._value = value

    @classmethod
    def describe_config(cls):
        return object_schema({
            cls.Field.MAX_ATTEMPTS: field(SchemaType.INTEGER, **{
                SchemaKeyword.MINIMUM: RetryPolicy.INITIAL_ATTEMPTS,
                SchemaExtension.NARROWING: SchemaNarrowing.MAXIMUM}),
            cls.Field.WALL_TIMEOUT: field([SchemaType.NUMBER, SchemaType.NULL], **{
                SchemaKeyword.EXCLUSIVE_MINIMUM: cls.MINIMUM_SECONDS,
                SchemaExtension.NARROWING: SchemaNarrowing.MAXIMUM}),
            cls.Field.DELAY_SECONDS: field(SchemaType.NUMBER, **{SchemaKeyword.MINIMUM: cls.MINIMUM_SECONDS}),
            cls.Field.MAX_DELAY_SECONDS: field(SchemaType.NUMBER, **{SchemaKeyword.MINIMUM: cls.MINIMUM_SECONDS}),
            cls.Field.RESPECT_RETRY_AFTER: field(SchemaType.BOOLEAN, description=cls.RETRY_AFTER_DESCRIPTION),
            cls.Field.JITTER: field(SchemaType.BOOLEAN, description=cls.JITTER_DESCRIPTION),
        }, **{SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT})

    @classmethod
    def resolve(cls, options):
        from jsonschema import Draft202012Validator
        if not isinstance(options, dict):
            raise TypeError(cls.INVALID_OBJECT)
        result = dict(options)
        Draft202012Validator(cls.describe_config()).validate(result)
        if cls.Field.MAX_ATTEMPTS in result and type(result[cls.Field.MAX_ATTEMPTS]) is not int:
            raise ValueError(cls.INVALID_ATTEMPTS)
        if any(result.get(key) is not cls.UNSPECIFIED and not math.isfinite(result[key]) for key in cls.TIMING_FIELDS):
            raise ValueError(cls.INVALID_TIMING)
        return result

    @property
    def max_attempts(self):
        return self._value.get(self.Field.MAX_ATTEMPTS)

    @property
    def wall_timeout(self):
        return self._value.get(self.Field.WALL_TIMEOUT)

    @property
    def delay_seconds(self):
        return self._value.get(self.Field.DELAY_SECONDS)

    @property
    def max_delay_seconds(self):
        return self._value.get(self.Field.MAX_DELAY_SECONDS)

    @property
    def respect_retry_after(self):
        return self._value.get(self.Field.RESPECT_RETRY_AFTER) is self.SELECTED

    @property
    def jitter(self):
        return self._value.get(self.Field.JITTER) is self.SELECTED
