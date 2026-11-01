"""Optional local speech SDKs. Models must already exist; no implicit downloads."""

from pathlib import Path
from copy import deepcopy
from threading import Lock

from .audio import AudioFormat, AudioChunk, AudioSequence, Transcript, SpeechError, native_call, speech_admission


class WhisperTranscriber:
    input_format = AudioFormat(16000, 1)  # faster-whisper's float waveform contract.

    def __init__(self, *, model_path, max_audio_frames, partial_frames, model_options=None, transcription=None):
        for value in (max_audio_frames, partial_frames):
            if type(value) is not int or value <= 0:
                raise ValueError('Local transcription requires explicit positive frame limits')
        self.path = Path(model_path).resolve(strict=True)
        if not self.path.is_dir():
            raise ValueError('Local transcription model_path must be an installed model directory')
        self.max_audio_frames, self.partial_frames = max_audio_frames, partial_frames
        self.model_options = deepcopy(model_options if model_options is not None else {})
        self.transcription = deepcopy(transcription if transcription is not None else {})
        if {'model_size_or_path', 'local_files_only', 'download_root'} & self.model_options.keys():
            raise ValueError('Local speech model identity and no-download behavior are adapter invariants')
        self._model = None
        self._load_lock = Lock()

    def _transcribe(self, raw):
        import numpy as np
        from faster_whisper import WhisperModel
        with self._load_lock:
            if self._model is None:
                self._model = WhisperModel(str(self.path), local_files_only=True, **self.model_options)
        samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(samples, **self.transcription)
        return ''.join(segment.text for segment in segments)

    async def transcribe(self, chunks, *, stream_id):
        sequence = AudioSequence(stream_id, self.input_format)
        audio, previous, transcript_sequence = bytearray(), 0, 0
        async with speech_admission('atranscribe', 'faster-whisper') as observation:
            async for chunk in chunks:
                sequence.accept(chunk)
                if (len(audio) + len(chunk.data)) // self.input_format.frame_bytes > self.max_audio_frames:
                    raise SpeechError('Audio exceeds the explicitly configured local utterance limit')
                audio.extend(chunk.data)
                frames = len(audio) // self.input_format.frame_bytes
                if not chunk.final and frames - previous >= self.partial_frames:
                    text = await native_call(self._transcribe, bytes(audio))
                    yield observation.record(Transcript(stream_id, transcript_sequence, text, False, None))
                    transcript_sequence += 1
                    previous = frames
            sequence.finish()
            text = await native_call(self._transcribe, bytes(audio)) if audio else ''
            yield observation.record(Transcript(stream_id, transcript_sequence, text, True, None))


class PiperSynthesizer:
    def __init__(self, *, voice_path, load_options=None, synthesis=None):
        self.path = Path(voice_path).resolve(strict=True)
        if not self.path.is_file():
            raise ValueError('Local voice_path must be an installed Piper model file')
        self.load_options = deepcopy(load_options if load_options is not None else {})
        self.synthesis = deepcopy(synthesis if synthesis is not None else {})
        self._voice = None
        self._load_lock = Lock()

    def _iterator(self, text):
        from piper import PiperVoice, SynthesisConfig
        with self._load_lock:
            if self._voice is None:
                self._voice = PiperVoice.load(str(self.path), **self.load_options)
        # Omitted SDK options retain the SDK's native defaults.
        return iter(self._voice.synthesize(text, syn_config=SynthesisConfig(**self.synthesis)))

    async def synthesize(self, text, *, stream_id):
        async with speech_admission('aspeech', 'piper') as observation:
            iterator = None
            sequence, format = 0, None
            def prepare():
                nonlocal iterator
                iterator = self._iterator(text)
            try:
                await native_call(prepare)
                while True:
                    chunk = await native_call(next, iterator, None)
                    if chunk is None:
                        break
                    current = AudioFormat(chunk.sample_rate, chunk.sample_channels)
                    if chunk.sample_width != 2 or format is not None and current != format:
                        raise SpeechError('Local synthesizer changed its PCM format')
                    format = current
                    yield observation.record(AudioChunk(stream_id, sequence, format, chunk.audio_int16_bytes, False))
                    sequence += 1
            finally:
                close = getattr(iterator, 'close', None)
                if close is not None:
                    await native_call(close)
            if format is None:
                raise SpeechError('Local synthesizer returned no audio')
            yield observation.record(AudioChunk(stream_id, sequence, format, b'', True))
