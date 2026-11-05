"""Explicit application speech choices; no model, endpoint or device preset."""

from copy import deepcopy
from dataclasses import dataclass, fields
from typing import ClassVar
from .store import WorkspaceJSONStore
from .speech_values import AudioDevice, SpeechAdapter, SpeechBehavior, SpeechInput, SpeechOutput, SpeechSubmit


@dataclass(frozen=True, slots=True)
class SpeechConfig:
    CONFIRM: ClassVar[str] = SpeechSubmit.CONFIRM
    AUTOMATIC: ClassVar[str] = SpeechSubmit.AUTOMATIC
    REQUIRED: ClassVar[str] = 'Speech requires an explicitly selected input or output'
    DEVICE_REQUIRED: ClassVar[str] = '{name} requires device, block_frames and queue_size'
    ADAPTER_REQUIRED: ClassVar[str] = '{name} requires its speech adapter'

    input: dict | None = None
    output: dict | None = None
    capture: dict | None = None
    playback: dict | None = None
    interaction: dict | None = None

    def __post_init__(self):
        for item in fields(self):
            object.__setattr__(self, item.name, deepcopy(getattr(self, item.name)))
        for value in (self.input, self.output):
            if value is not None:
                SpeechAdapter.from_dict(value)
        if self.input is None and self.output is None:
            raise ValueError(self.REQUIRED)
        for name, selected in ((type(self).capture.__name__, self.input),
                               (type(self).playback.__name__, self.output)):
            value = getattr(self, name)
            if selected is None:
                if value is not None:
                    raise ValueError(self.ADAPTER_REQUIRED.format(name=name))
            elif value is None:
                raise ValueError(self.DEVICE_REQUIRED.format(name=name))
            else:
                AudioDevice.from_dict(value)
        self.behavior

    @property
    def behavior(self):
        return SpeechBehavior.read(self.interaction,
            input_enabled=self.input is not None, output_enabled=self.output is not None)

    def capture_device(self):
        return AudioDevice.from_dict(self.capture)

    def playback_device(self):
        return AudioDevice.from_dict(self.playback)

    def build_input(self):
        if self.input is None:
            return None
        adapter = SpeechAdapter.from_dict(self.input)
        return SpeechInput(adapter.implementation).build(adapter.config)

    def build_output(self):
        if self.output is None:
            return None
        adapter = SpeechAdapter.from_dict(self.output)
        return SpeechOutput(adapter.implementation).build(adapter.config)


class SpeechStore(WorkspaceJSONStore):
    """Application audio preferences, separate from Project execution policies."""
    FILENAME = 'speech.json'
    INVALID = 'Speech configuration must be an object or explicit null'

    def __init__(self, workspace):
        super().__init__(workspace, self.FILENAME)

    def load(self):
        value = self.get_config()
        return SpeechConfig(**value) if value is not None else None

    def get_config(self):
        value = self._read_json(None)
        if value is not None:
            if not isinstance(value, dict):
                raise ValueError(self.INVALID)
            SpeechConfig(**value)
        return value

    def configure(self, value):
        if value is not None:
            if not isinstance(value, dict):
                raise ValueError(self.INVALID)
            SpeechConfig(**value)
        self._write_json(value)
