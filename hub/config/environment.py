"""A shell snapshot captured before a request enters the backend queue."""

from dataclasses import dataclass
from pathlib import PurePosixPath
from collections.abc import Mapping

ENVIRONMENT_OPTION = "_hub_environment"


@dataclass(frozen=True, slots=True)
class ShellEnvironment:
    cwd: str
    variables: tuple[tuple[str, str], ...]

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {"cwd", "env"}:
            raise ValueError("Invalid Hub shell environment snapshot")
        cwd, env = value["cwd"], value["env"]
        if not isinstance(cwd, str) or "\0" in cwd or not PurePosixPath(cwd).is_absolute():
            raise ValueError("ish must provide an absolute PWD before submitting a request")
        if not isinstance(env, Mapping) or any(
                not isinstance(k, str) or not k or "=" in k or "\0" in k
                or not isinstance(v, str) or "\0" in v for k, v in env.items()):
            raise ValueError("Invalid ish environment variable")
        return cls(cwd, tuple(sorted(env.items())))

    @classmethod
    def from_prompt(cls, prompt):
        context = getattr(prompt, "context", None)
        env = getattr(context, "environ", None)
        if not isinstance(env, Mapping):
            raise ValueError("ish environment is not available yet; reopen Hub after the shell prompt is ready")
        return cls.from_dict({"cwd": env.get("PWD"), "env": dict(env)})

    def to_dict(self):
        return {"cwd": self.cwd, "env": dict(self.variables)}
