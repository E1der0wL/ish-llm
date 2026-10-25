"""A shell snapshot captured before a request enters the backend queue."""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import PurePosixPath

ENVIRONMENT_OPTION = "_hub_environment"


class ShellSnapshotFormat:
    WIRE_NAME = "wire_name"
    NUL = "\0"
    ASSIGNMENT = "="
    PWD = "PWD"
    PROMPT_CONTEXT = "context"
    CONTEXT_ENVIRONMENT = "environ"
    INVALID = "Invalid Hub shell environment snapshot"
    INVALID_PWD = "ish must provide an absolute PWD before submitting a request"
    INVALID_VARIABLE = "Invalid ish environment variable"
    ABSENT_HOST_ATTRIBUTE = None
    UNAVAILABLE = "ish environment is not available yet; reopen Hub after the shell prompt is ready"


@dataclass(frozen=True, slots=True)
class ShellEnvironment:
    cwd: str
    variables: tuple[tuple[str, str], ...] = field(
        metadata={ShellSnapshotFormat.WIRE_NAME: "env"}
    )

    @classmethod
    def _wire_name(cls, attribute):
        declaration = next(
            item for item in fields(cls) if item.name == attribute.__name__
        )
        return declaration.metadata.get(ShellSnapshotFormat.WIRE_NAME, declaration.name)

    @classmethod
    def from_dict(cls, value):
        cwd_key, env_key = cls._wire_name(cls.cwd), cls._wire_name(cls.variables)
        if not isinstance(value, dict) or set(value) != {cwd_key, env_key}:
            raise ValueError(ShellSnapshotFormat.INVALID)
        cwd, env = value[cwd_key], value[env_key]
        if (
            not isinstance(cwd, str)
            or ShellSnapshotFormat.NUL in cwd
            or not PurePosixPath(cwd).is_absolute()
        ):
            raise ValueError(ShellSnapshotFormat.INVALID_PWD)
        if not isinstance(env, Mapping) or any(
            not isinstance(k, str)
            or not k
            or ShellSnapshotFormat.ASSIGNMENT in k
            or ShellSnapshotFormat.NUL in k
            or not isinstance(v, str)
            or ShellSnapshotFormat.NUL in v
            for k, v in env.items()
        ):
            raise ValueError(ShellSnapshotFormat.INVALID_VARIABLE)
        return cls(cwd, tuple(sorted(env.items())))

    @classmethod
    def from_prompt(cls, prompt):
        context = getattr(
            prompt,
            ShellSnapshotFormat.PROMPT_CONTEXT,
            ShellSnapshotFormat.ABSENT_HOST_ATTRIBUTE,
        )
        env = getattr(
            context,
            ShellSnapshotFormat.CONTEXT_ENVIRONMENT,
            ShellSnapshotFormat.ABSENT_HOST_ATTRIBUTE,
        )
        if not isinstance(env, Mapping):
            raise ValueError(ShellSnapshotFormat.UNAVAILABLE)
        return cls.from_dict(
            {
                cls._wire_name(cls.cwd): env.get(ShellSnapshotFormat.PWD),
                cls._wire_name(cls.variables): dict(env),
            }
        )

    @classmethod
    def clipboard_environment(cls, prompt):
        """Optional host environment for clipboard transport; no execution admission."""
        context = getattr(prompt, ShellSnapshotFormat.PROMPT_CONTEXT,
                          ShellSnapshotFormat.ABSENT_HOST_ATTRIBUTE)
        env = getattr(context, ShellSnapshotFormat.CONTEXT_ENVIRONMENT,
                      ShellSnapshotFormat.ABSENT_HOST_ATTRIBUTE)
        return dict(os.environ if env is ShellSnapshotFormat.ABSENT_HOST_ATTRIBUTE else env)

    def to_dict(self):
        cls = type(self)
        return {
            cls._wire_name(cls.cwd): self.cwd,
            cls._wire_name(cls.variables): dict(self.variables),
        }
