"""Component reference response projection; the selected repositories validate records."""

from copy import deepcopy
from .models import Run, Step
from .step_event_metadata import StepEventMetadata
from .step_tool_metadata import ToolStepMetadata


class ComponentReference:
    class Field:
        SESSION = "session"
        RUN = "run"
        STEP = "step"

    def __init__(self, value):
        self._value = value

    @classmethod
    def for_session(cls, record):
        return cls({cls.Field.SESSION: record})

    def include_run(self, record):
        self._value[self.Field.RUN] = record

    def include_step(self, record):
        self._value[self.Field.STEP] = record

    @property
    def tool_call_id(self):
        return ToolStepMetadata.call_id(self._value.get(self.Field.STEP, {}).get(Step.metadata.__name__, {}))

    @property
    def error_code(self):
        owner = self._value.get(self.Field.STEP, self._value[self.Field.RUN])
        return owner.get(Run.error_code.__name__) or owner.get(Run.metadata.__name__, {}).get(
            StepEventMetadata.Field.ERROR_CODE
        )

    def to_dict(self):
        return deepcopy(self._value)
