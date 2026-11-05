"""Hub speech choices. Adapter options remain owned by the selected provider."""

from dataclasses import dataclass, fields
from enum import StrEnum
from math import isfinite

from llm.core.contracts import JsonValue


class SpeechInput(StrEnum):
    WHISPER = 'local-whisper'
    REALTIME = 'realtime-server'

    def build(self, config):
        from llm.providers.local_speech import WhisperTranscriber
        from llm.providers.server_speech import RealtimeTranscriber
        if self == self.WHISPER:
            return WhisperTranscriber(**config)
        return RealtimeTranscriber(**config)


class SpeechOutput(StrEnum):
    PIPER = 'local-piper'
    SERVER = 'speech-server'

    def build(self, config):
        from llm.providers.local_speech import PiperSynthesizer
        from llm.providers.server_speech import ServerSynthesizer
        if self == self.PIPER:
            return PiperSynthesizer(**config)
        return ServerSynthesizer(**config)


@dataclass(frozen=True, slots=True)
class SpeechAdapter(JsonValue):
    implementation: str
    config: dict


@dataclass(frozen=True, slots=True)
class AudioDevice(JsonValue):
    device: str | int | None
    block_frames: int
    queue_size: int

    INVALID_SIZE = 'Audio block and queue sizes must be explicitly positive'

    def __post_init__(self):
        JsonValue.__post_init__(self)
        if self.block_frames <= 0 or self.queue_size <= 0:
            raise ValueError(self.INVALID_SIZE)


class SpeechSubmit(StrEnum):
    CONFIRM = 'confirm'
    AUTOMATIC = 'automatic'


@dataclass(frozen=True, slots=True)
class SpeechBehavior(JsonValue):
    submit: SpeechSubmit | None = None
    read_responses: bool | None = None
    text_chunk_chars: int | None = None
    speech_threshold: float | None = None
    silence_frames: int | None = None

    REQUIRED = 'Speech requires explicit interaction choices'
    INPUT_REQUIRED = 'Speech input requires submit=confirm or automatic'
    OUTPUT_REQUIRED = 'Speech output requires an explicit read_responses choice'
    NO_INPUT = 'Speech submit requires an input adapter'
    NO_OUTPUT = 'Response reading requires an output adapter'
    INVALID_CHUNK = 'Response speech requires positive text_chunk_chars'
    INVALID_VAD = 'Continuous capture requires explicit speech_threshold (0..1) and silence_frames'

    @classmethod
    def read(cls, value, *, input_enabled, output_enabled):
        if not isinstance(value, dict):
            raise ValueError(cls.REQUIRED)
        # Public missing/null distinctions are validated before optional values
        # become an observation. No choice or device is supplied by this reader.
        selected = cls.from_dict(value)
        names = {item.name for item in fields(cls) if item.name in value}
        if not input_enabled and cls.submit.__name__ in names:
            raise ValueError(cls.NO_INPUT)
        if not output_enabled and names & {cls.read_responses.__name__, cls.text_chunk_chars.__name__}:
            raise ValueError(cls.NO_OUTPUT)
        if input_enabled and selected.submit is None:
            raise ValueError(cls.INPUT_REQUIRED)
        if output_enabled and selected.read_responses is None:
            raise ValueError(cls.OUTPUT_REQUIRED)
        if selected.read_responses or cls.text_chunk_chars.__name__ in names:
            if selected.text_chunk_chars is None or selected.text_chunk_chars <= 0:
                raise ValueError(cls.INVALID_CHUNK)
        vad = names & {cls.speech_threshold.__name__, cls.silence_frames.__name__}
        if vad and (not input_enabled or len(vad) != 2 or selected.speech_threshold is None
                    or not isfinite(selected.speech_threshold) or not 0 < selected.speech_threshold < 1
                    or selected.silence_frames is None or selected.silence_frames <= 0):
            raise ValueError(cls.INVALID_VAD)
        return selected
