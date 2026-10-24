"""Transient retry observation identities; no execution or retry authorization."""


class RetryIdentity:
    RUN_POSITION = 0

    @classmethod
    def create(cls, run_id, member):
        return run_id, member

    @classmethod
    def run_id(cls, value):
        return value[cls.RUN_POSITION]
