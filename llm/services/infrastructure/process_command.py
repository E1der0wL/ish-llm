"""Absolute argv syntax shared by isolated execution and pre-effect feedback."""

from pathlib import Path


class ProcessCommand:
    NUL = "\0"
    INVALID = "Command requires argv with an absolute executable"

    @classmethod
    def valid(cls, argv):
        return (isinstance(argv, (list, tuple)) and bool(argv) and bool(argv[0])
                and all(isinstance(arg, str) and cls.NUL not in arg for arg in argv)
                and Path(argv[0]).is_absolute())

    @classmethod
    def validate(cls, argv):
        if not cls.valid(argv):
            raise ValueError(cls.INVALID)
