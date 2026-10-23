"""Complete independent wire fixtures; corruption tests mutate them explicitly."""

from copy import deepcopy


def resume_record(**values):
    return deepcopy({"run_id": "fixture-source", "checkpoint": "fixture-checkpoint",
                     "digest": "fixture-digest", "retry_nodes": [], "decisions": {}, **values})
