"""The existing Bubblewrap boundary: declared argv and protected mount rules."""

from pathlib import Path
import shutil

from llm.core.models import RunErrorCode
from llm.policies import ExecutionLimitError


class LinuxSandbox:
    PROGRAM = "bwrap"
    REQUIRED = "Linux Bubblewrap is required; no unsandboxed fallback is allowed"
    PREFIX = ("--unshare-all", "--unshare-user", "--die-with-parent", "--new-session",
              "--cap-drop", "ALL", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp")
    SHARE_NETWORK = "--share-net"
    READ_ONLY_BIND = "--ro-bind"
    RUNTIME_PATHS = ("/usr", "/bin", "/lib", "/lib64")
    HOST_ROOT = Path("/")
    SPECIAL_PATHS = (Path("/proc"), Path("/dev"), Path("/sys"))
    FORBIDDEN_WORKDIRS = ("/", "/usr", "/bin", "/lib", "/lib64", "/proc", "/dev", "/tmp",
                         "/var", "/run", "/home", "/etc", "/sys")
    EXPOSED_HOST = "Cannot expose a whole host or special filesystem"
    DEDICATED_WORKDIR = "Sandbox workdir must be a dedicated directory"
    EXPOSED_RUNTIME = "Sandbox workdir cannot contain the trusted runner or Python runtime"
    CLEAR_ENVIRONMENT = "--clearenv"
    SET_ENVIRONMENT = "--setenv"
    WRITE_BIND = "--bind"
    WORKDIR = "--chdir"
    COMMAND = "--"

    @classmethod
    def locate(cls):
        return shutil.which(cls.PROGRAM)

    @classmethod
    def command(cls, command, *, cwd, python, read_only_paths, env, allow_network, trusted_runner):
        bwrap = cls.locate()
        if not bwrap:
            raise ExecutionLimitError(RunErrorCode.SANDBOX_UNAVAILABLE, cls.REQUIRED)
        args = [bwrap, *cls.PREFIX]
        if allow_network:
            args += [cls.SHARE_NETWORK]
        for path in cls.RUNTIME_PATHS:
            if Path(path).exists():
                args += [cls.READ_ONLY_BIND, path, path]
        for path in read_only_paths:
            if path == cls.HOST_ROOT or any(root in (path, *path.parents) for root in cls.SPECIAL_PATHS):
                raise ValueError(cls.EXPOSED_HOST)
            args += [cls.READ_ONLY_BIND, str(path), str(path)]
        if str(cwd) in cls.FORBIDDEN_WORKDIRS:
            raise ValueError(cls.DEDICATED_WORKDIR)
        for protected in (Path(python), trusted_runner):
            if cwd in (protected, *protected.parents):
                raise ValueError(cls.EXPOSED_RUNTIME)
        # Only the isolated target receives the explicitly selected environment.
        args += [cls.CLEAR_ENVIRONMENT]
        for key, value in env.items():
            args += [cls.SET_ENVIRONMENT, key, value]
        args += [cls.WRITE_BIND, str(cwd), str(cwd), cls.WORKDIR, str(cwd), cls.COMMAND, *command]
        return args
