"""Run continuation metadata declarations and detached request projections.

These links describe stored lineage. Only the runtime can admit a continuation,
using the current checkpoint, approval, execution binding and budget evidence.
"""

from copy import deepcopy


class RunLink:
    SOURCE_RUN = "run_id"

    @classmethod
    def source_id(cls, metadata):
        """Read the optional lineage projection without manufacturing a receipt."""
        return metadata.get(cls.KEY, {}).get(cls.SOURCE_RUN)

    @classmethod
    def remove(cls, metadata):
        """Used on detached clone context, never to delete execution evidence."""
        metadata.pop(cls.KEY, None)


class ResumeRecord(RunLink):
    KEY = "resume"
    CHECKPOINT = "checkpoint"
    DIGEST = "digest"
    RETRY_NODES = "retry_nodes"
    DECISIONS = "decisions"
    PARTIAL_OUTPUT = "partial_output"

    @classmethod
    def checkpoint_name(cls, metadata):
        return metadata[cls.KEY][cls.CHECKPOINT] if cls.KEY in metadata else None

    @classmethod
    def create(cls, source_id, checkpoint, digest, retry_nodes, decisions, *, partial_output=False):
        value = {cls.SOURCE_RUN: source_id, cls.CHECKPOINT: checkpoint, cls.DIGEST: digest,
                 cls.RETRY_NODES: list(retry_nodes), cls.DECISIONS: deepcopy(decisions)}
        if partial_output:
            value[cls.PARTIAL_OUTPUT] = True
        return value

    @classmethod
    def for_child(cls, descriptor, *, retry_nodes, decisions):
        """Project already-validated parent decisions into an Engine-local scope."""
        return {**deepcopy(descriptor), cls.DECISIONS: deepcopy(decisions), cls.RETRY_NODES: list(retry_nodes)}


class RestartRecord(RunLink):
    KEY = "restart"
    DIGEST = "digest"

    @classmethod
    def create(cls, source_id, digest):
        return {cls.SOURCE_RUN: source_id, cls.DIGEST: digest}
