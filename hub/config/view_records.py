"""Hub reading-state records. Tolerant UI observations never authorize execution."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ReadingPosition:
    MINIMUM_OFFSET = 0
    MINIMUM_LINE = 0
    INVALID_OBSERVATION = None

    message_id: str = ""
    offset: int = MINIMUM_OFFSET
    line: int = MINIMUM_LINE

    @classmethod
    def observe(cls, value):
        if (
            isinstance(value, dict)
            and isinstance(value.get(cls.message_id.__name__), str)
            and type(value.get(cls.offset.__name__)) is int
            and value[cls.offset.__name__] >= cls.MINIMUM_OFFSET
            and type(value.get(cls.line.__name__)) is int
            and value[cls.line.__name__] >= cls.MINIMUM_LINE
        ):
            return cls(
                value[cls.message_id.__name__],
                value[cls.offset.__name__],
                value[cls.line.__name__],
            )
        return cls.INVALID_OBSERVATION


class ProjectReadingState:
    SELECTED = "selected"
    POSITIONS = "positions"
    UNSELECTED = ""
    INVALID = "Invalid Hub project view state"

    def __init__(self, value):
        self._value = value

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or not isinstance(
            value.get(cls.POSITIONS, {}), dict
        ):
            raise ValueError(cls.INVALID)
        return cls(value)

    @property
    def selected(self):
        return self._value.get(self.SELECTED, self.UNSELECTED)

    def positions(self):
        result = {}
        for session_id, value in self._value.get(self.POSITIONS, {}).items():
            position = ReadingPosition.observe(value)
            if position is not ReadingPosition.INVALID_OBSERVATION:
                result[session_id] = position
        return result

    @classmethod
    def update_record(cls, value, selected, positions):
        # The Writer preserves unknown stored observations and the established
        # mutation/error order; read-time filtering does not repair the file.
        value[cls.SELECTED] = selected
        value.setdefault(cls.POSITIONS, {}).update(
            {key: asdict(position) for key, position in positions.items()}
        )


class ViewStateRecord:
    VERSION = 1
    VERSION_FIELD = "version"
    PROJECTS = "projects"
    LAST_PROJECT = "last_project"
    UNSELECTED = ProjectReadingState.UNSELECTED
    INVALID = "Invalid Hub view state"

    def __init__(self, value):
        self._value = value

    @classmethod
    def empty(cls, version):
        return {cls.VERSION_FIELD: version, cls.PROJECTS: {}}

    @classmethod
    def read(cls, value, version):
        if (
            not isinstance(value, dict)
            or type(value.get(cls.VERSION_FIELD)) is not int
            or value[cls.VERSION_FIELD] != version
            or not isinstance(value.get(cls.PROJECTS), dict)
        ):
            raise ValueError(cls.INVALID)
        return cls(value)

    @property
    def last_project(self):
        value = self._value.get(self.LAST_PROJECT, self.UNSELECTED)
        return value if isinstance(value, str) else self.UNSELECTED

    def project(self, project_id):
        return ProjectReadingState.read(self._value[self.PROJECTS].get(project_id, {}))

    def save_positions(self, project_id, selected, positions):
        projects = self._value[self.PROJECTS]
        self._value[self.LAST_PROJECT] = project_id
        project = projects.setdefault(project_id, {})
        ProjectReadingState.update_record(project, selected, positions)

    def to_dict(self):
        # Only the owning store serializes this freshly read, unshared record.
        return self._value
