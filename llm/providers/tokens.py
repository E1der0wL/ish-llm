"""Default provider counter registration; host counters remain explicitly selectable."""
from llm.core.model_protocol import ModelRequest


def default_token_counters():
    from .litellm import model_token_count
    return {'model_default': model_token_count}


def count_request(counter, request):
    if not isinstance(request, ModelRequest):
        raise TypeError("Token counter requires ModelRequest")
    return counter(request)
