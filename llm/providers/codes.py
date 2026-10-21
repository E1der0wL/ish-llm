"""Stable Provider failure vocabulary; no SDK, runtime or execution dependencies."""

from enum import StrEnum


class ProviderErrorCode(StrEnum):
    """Normalized transport/response observations; codes grant no retry authority."""

    CANCELLED = "provider_cancelled"
    AUTHENTICATION = "provider_authentication"
    RATE_LIMIT = "provider_rate_limit"
    TIMEOUT = "provider_timeout"
    INVALID_RESPONSE = "provider_invalid_response"
    UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "provider_invalid_request"
    CONNECTION = "provider_connection"
    FAILED = "provider_failed"
    EMPTY_RESPONSE = "provider_empty_response"
