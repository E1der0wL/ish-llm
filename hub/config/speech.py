"""Explicit application speech choices; no model, endpoint or device preset."""

from copy import deepcopy
from dataclasses import dataclass
from typing import ClassVar
from .store import WorkspaceJSONStore


@dataclass(frozen=True, slots=True)
class SpeechConfig:
    CONFIRM: ClassVar[str] = 'confirm'
    AUTOMATIC: ClassVar[str] = 'automatic'

    input: dict | None = None
    output: dict | None = None
    capture: dict | None = None
    playback: dict | None = None
    interaction: dict | None = None

    def __post_init__(self):
        from llm.core.json_data import JsonObject

        for name in ('input', 'output', 'capture', 'playback', 'interaction'):
            value = getattr(self, name)
            if value is not None:
                JsonObject.validate(value)
                object.__setattr__(self, name, deepcopy(value))
        for name in ('input', 'output'):
            value = getattr(self, name)
            if value is not None and (set(value) != {'implementation', 'config'}
                                      or not isinstance(value['config'], dict)):
                raise ValueError(f'Speech {name} requires implementation and config')
        if self.input is None and self.output is None:
            raise ValueError('Speech requires an explicitly selected input or output')
        for name, selected in (('capture', self.input), ('playback', self.output)):
            value = getattr(self, name)
            if selected is None:
                if value is not None:
                    raise ValueError(f'{name} requires its speech adapter')
                continue
            if value is None or set(value) != {'device', 'block_frames', 'queue_size'}:
                raise ValueError(f'{name} requires device, block_frames and queue_size')
            if value['device'] is not None and type(value['device']) not in (str, int):
                raise ValueError(f'{name}.device must be a device name/index or explicit null')
            for key in ('block_frames', 'queue_size'):
                if type(value[key]) is not int or value[key] <= 0:
                    raise ValueError(f'{name}.{key} must be a positive integer')
        allowed = {'submit', 'read_responses', 'text_chunk_chars', 'speech_threshold', 'silence_frames'}
        behavior = self.interaction
        if behavior is None or set(behavior) - allowed:
            raise ValueError('Speech requires explicit interaction choices')
        if self.input is None and 'submit' in behavior:
            raise ValueError('Speech submit requires an input adapter')
        if self.output is None and {'read_responses', 'text_chunk_chars'} & behavior.keys():
            raise ValueError('Response reading requires an output adapter')
        if self.input is not None and behavior.get('submit') not in {self.CONFIRM, self.AUTOMATIC}:
            raise ValueError('Speech input requires submit=confirm or automatic')
        if self.output is not None and type(behavior.get('read_responses')) is not bool:
            raise ValueError('Speech output requires an explicit read_responses choice')
        if behavior.get('read_responses') or 'text_chunk_chars' in behavior:
            if type(behavior.get('text_chunk_chars')) is not int or behavior['text_chunk_chars'] <= 0:
                raise ValueError('Response speech requires positive text_chunk_chars')
        vad = {'speech_threshold', 'silence_frames'} & behavior.keys()
        if vad:
            from math import isfinite

            threshold = behavior.get('speech_threshold')
            frames = behavior.get('silence_frames')
            if (self.input is None or len(vad) != 2
                    or type(threshold) not in (int, float) or not isfinite(threshold)
                    or not 0 < threshold < 1 or type(frames) is not int or frames <= 0):
                raise ValueError('Continuous capture requires explicit speech_threshold (0..1) and silence_frames')

    def build_input(self):
        if self.input is None:
            return None
        from llm.providers.local_speech import WhisperTranscriber
        from llm.providers.server_speech import RealtimeTranscriber

        implementations = {'local-whisper': WhisperTranscriber, 'realtime-server': RealtimeTranscriber}
        try:
            implementation = implementations[self.input['implementation']]
        except (KeyError, TypeError) as error:
            raise ValueError('Unsupported speech input implementation') from error
        return implementation(**self.input['config'])

    def build_output(self):
        if self.output is None:
            return None
        from llm.providers.local_speech import PiperSynthesizer
        from llm.providers.server_speech import ServerSynthesizer

        implementations = {'local-piper': PiperSynthesizer, 'speech-server': ServerSynthesizer}
        try:
            implementation = implementations[self.output['implementation']]
        except (KeyError, TypeError) as error:
            raise ValueError('Unsupported speech output implementation') from error
        return implementation(**self.output['config'])


class SpeechStore(WorkspaceJSONStore):
    """Application audio preferences, separate from Project execution policies."""

    def __init__(self, workspace):
        super().__init__(workspace, 'speech.json')

    def load(self):
        value = self.get_config()
        if value is None:
            return None
        return SpeechConfig(**value)

    def get_config(self):
        value = self._read_json(None)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError('Speech configuration must be an object or explicit null')
        SpeechConfig(**value)
        return value

    def configure(self, value):
        if value is not None:
            if not isinstance(value, dict):
                raise ValueError('Speech configuration must be an object or explicit null')
            SpeechConfig(**value)
        self._write_json(value)
