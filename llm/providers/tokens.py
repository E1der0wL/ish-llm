"""Default provider counter registration; host counters remain explicitly selectable."""
from .model_codec import counter_input


def default_token_counters():
    from .litellm import model_token_count
    return {'model_default': model_token_count}


def count_request(counter, request):
    return counter(counter_input(counter, request))
