"""Optional local speech SDKs. Models must already exist; no implicit downloads."""

from pathlib import Path
from copy import deepcopy
from threading import Lock

from .audio import AudioFormat, AudioChunk, AudioSequence, Transcript, SpeechError, SpeechStream, native_call, speech_admission


class WhisperTranscriber:
    INVALID_LIMITS = 'Local transcription requires explicit positive frame limits'
    INVALID_MODEL = 'Local transcription model_path must be an installed model directory'
    FIXED_CONFIGURATION = 'Local speech model identity and no-download behavior are adapter invariants'
    LIMIT_EXCEEDED = 'Audio exceeds the explicitly configured local utterance limit'

    input_format = AudioFormat(16000, 1)  # faster-whisper's float waveform contract.
    MINIMUM_FRAMES = 1
    LOCAL_FILES_ONLY = True  # Installed model required; never download implicitly.
    FIXED_OPTIONS = frozenset({'model_size_or_path', 'local_files_only', 'download_root'})
    PCM_DTYPE = '<i2'
    PCM_SCALE = 32768.0  # signed PCM16 -> faster-whisper's floating-point waveform.
    OPERATION = 'atranscribe'
    ADAPTER = 'faster-whisper'
    REQUIRE_INSTALLED = True

    def __init__(self, *, model_path, max_audio_frames, partial_frames, model_options=None, transcription=None):
        for value in (max_audio_frames, partial_frames):
            if type(value) is not int or value < self.MINIMUM_FRAMES:
                raise ValueError(WhisperTranscriber.INVALID_LIMITS)
        self.path = Path(model_path).resolve(strict=self.REQUIRE_INSTALLED)
        if not self.path.is_dir():
            raise ValueError(WhisperTranscriber.INVALID_MODEL)
        self.max_audio_frames, self.partial_frames = max_audio_frames, partial_frames
        self.model_options = deepcopy(model_options if model_options is not None else {})
        self.transcription = deepcopy(transcription if transcription is not None else {})
        if self.FIXED_OPTIONS & self.model_options.keys():
            raise ValueError(WhisperTranscriber.FIXED_CONFIGURATION)
        self._model = None
        self._load_lock = Lock()

    def _transcribe(self, raw):
        import numpy as np
        from faster_whisper import WhisperModel
        with self._load_lock:
            if self._model is None:
                self._model = WhisperModel(str(self.path), local_files_only=self.LOCAL_FILES_ONLY, **self.model_options)
        samples = np.frombuffer(raw, dtype=self.PCM_DTYPE).astype(np.float32) / self.PCM_SCALE
        segments, _ = self._model.transcribe(samples, **self.transcription)
        return ''.join(segment.text for segment in segments)

    async def transcribe(self, chunks, *, stream_id):
        sequence = AudioSequence(stream_id, self.input_format)
        audio, previous, transcript_sequence = bytearray(), 0, SpeechStream.FIRST_SEQUENCE
        async with speech_admission(self.OPERATION, self.ADAPTER) as observation:
            async for chunk in chunks:
                sequence.accept(chunk)
                if (len(audio) + len(chunk.data)) // self.input_format.frame_bytes > self.max_audio_frames:
                    raise SpeechError(WhisperTranscriber.LIMIT_EXCEEDED)
                audio.extend(chunk.data)
                frames = len(audio) // self.input_format.frame_bytes
                if not chunk.final and frames - previous >= self.partial_frames:
                    text = await native_call(self._transcribe, bytes(audio))
                    yield observation.record(Transcript(stream_id, transcript_sequence, text, SpeechStream.PARTIAL,
                                                        SpeechStream.UNREPORTED_USAGE))
                    transcript_sequence += SpeechStream.SEQUENCE_STEP
                    previous = frames
            sequence.finish()
            text = await native_call(self._transcribe, bytes(audio)) if audio else ''
            yield observation.record(Transcript(stream_id, transcript_sequence, text, SpeechStream.FINAL,
                                                SpeechStream.UNREPORTED_USAGE))


class PiperSynthesizer:
    INVALID_MODEL = 'Local voice_path must be an installed Piper model file'
    CHANGED_FORMAT = 'Local synthesizer changed its PCM format'
    EMPTY_OUTPUT = 'Local synthesizer returned no audio'

    OPERATION = 'aspeech'
    ADAPTER = 'piper'
    REQUIRE_INSTALLED = True
    CLOSE_METHOD = 'close'

    def __init__(self, *, voice_path, load_options=None, synthesis=None):
        self.path = Path(voice_path).resolve(strict=self.REQUIRE_INSTALLED)
        if not self.path.is_file():
            raise ValueError(PiperSynthesizer.INVALID_MODEL)
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
        async with speech_admission(self.OPERATION, self.ADAPTER) as observation:
            iterator = None
            sequence, format = SpeechStream.FIRST_SEQUENCE, None
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
                    if chunk.sample_width != AudioFormat.SAMPLE_BYTES or format is not None and current != format:
                        raise SpeechError(PiperSynthesizer.CHANGED_FORMAT)
                    format = current
                    yield observation.record(AudioChunk(stream_id, sequence, format, chunk.audio_int16_bytes, SpeechStream.PARTIAL))
                    sequence += SpeechStream.SEQUENCE_STEP
            finally:
                close = getattr(iterator, self.CLOSE_METHOD, None)
                if close is not None:
                    await native_call(close)
            if format is None:
                raise SpeechError(PiperSynthesizer.EMPTY_OUTPUT)
            yield observation.record(AudioChunk(stream_id, sequence, format, SpeechStream.NO_AUDIO, SpeechStream.FINAL))
