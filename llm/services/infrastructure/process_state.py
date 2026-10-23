"""Linux /proc process identity and state; observation alone grants no execution authority."""

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProcessStat:
    ROOT = Path("/proc")
    FILE = "stat"
    NAME_OPEN = "("
    NAME_CLOSE = ")"
    NAME_DELIMITER_SIZE = 1
    FIELDS_AFTER_NAME = 2
    POSITION = "stat_position"
    TERMINATED = frozenset(("Z", "X", "x"))

    pid: int
    name: str
    state: str = field(metadata={POSITION: 0})
    ppid: int = field(metadata={POSITION: 1})
    process_group: int = field(metadata={POSITION: 2})
    session: int = field(metadata={POSITION: 3})
    tty_number: int = field(metadata={POSITION: 4})
    foreground_process_group: int = field(metadata={POSITION: 5})
    start_ticks: int = field(metadata={POSITION: 19})

    @classmethod
    def read(cls, pid):
        raw = (cls.ROOT / str(pid) / cls.FILE).read_text()
        left, right = raw.index(cls.NAME_OPEN), raw.rindex(cls.NAME_CLOSE)
        values = raw[right + cls.FIELDS_AFTER_NAME:].split()
        return cls(pid=int(raw[:left].strip()), name=raw[left + cls.NAME_DELIMITER_SIZE:right],
                   **{item.name: item.type(values[item.metadata[cls.POSITION]])
                      for item in fields(cls) if cls.POSITION in item.metadata})

    @property
    def identity(self):
        return self.pid, self.start_ticks

    @property
    def terminated(self):
        return self.state in self.TERMINATED

    def to_dict(self):
        return asdict(self)
