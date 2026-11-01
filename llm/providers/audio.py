"""Runtime-only speech stream values. These are not Run/Step records or approvals."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Protocol
from time import monotonic
from copy import deepcopy

from llm.errors import CodedError
from .calls import current_calls


class SpeechError(CodedError, ValueError):
    code = 'speech_invalid_stream'


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """The current device bridge supports signed little-endian 16-bit PCM."""
    sample_rate: int
    channels: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (self.sample_rate, self.channels)):
            raise SpeechError('Audio sample rate and channels must be positive integers')

    @property
    def frame_bytes(self):
        return self.channels * 2  # pcm_s16le protocol width, not a quality preset.


@dataclass(frozen=True, slots=True)
class AudioChunk:
    stream_id: str
    sequence: int
    format: AudioFormat
    data: bytes
    final: bool

    def __post_init__(self):
        if not isinstance(self.stream_id, str) or not self.stream_id:
            raise SpeechError('Audio requires a stream identity')
        if type(self.sequence) is not int or self.sequence < 0 or type(self.final) is not bool:
            raise SpeechError('Invalid audio sequence or termination')
        if not isinstance(self.format, AudioFormat) or not isinstance(self.data, bytes):
            raise SpeechError('Audio requires a declared PCM format and bytes')
        if len(self.data) % self.format.frame_bytes:
            raise SpeechError('Audio chunk contains an incomplete PCM frame')


@dataclass(frozen=True, slots=True)
class Transcript:
    stream_id: str
    sequence: int
    text: str
    final: bool
    # None means unreported, never zero usage. Adapters retain ownership of units.
    usage: dict | None

    def __post_init__(self):
        from llm.core.json_data import JsonObject
        if not isinstance(self.stream_id, str) or not self.stream_id or not isinstance(self.text, str):
            raise SpeechError('Transcript requires its stream identity and text')
        if type(self.sequence) is not int or self.sequence < 0 or type(self.final) is not bool:
            raise SpeechError('Invalid transcript sequence or termination')
        if self.usage is not None:
            JsonObject.validate(self.usage)


class AudioSequence:
    def __init__(self, stream_id, format):
        self.stream_id, self.format = stream_id, format
        self.next_sequence, self.ended = 0, False

    def accept(self, chunk):
        if not isinstance(chunk, AudioChunk) or self.ended:
            raise SpeechError('Audio arrived after termination or has an invalid type')
        if (chunk.stream_id, chunk.sequence, chunk.format) != (self.stream_id, self.next_sequence, self.format):
            raise SpeechError('Audio stream identity, order or format changed')
        self.next_sequence += 1
        self.ended = chunk.final

    def finish(self):
        if not self.ended:
            raise SpeechError('Audio input disconnected before its final chunk')


class Transcriber(Protocol):
    input_format: AudioFormat
    def transcribe(self, chunks: AsyncIterator[AudioChunk], *, stream_id: str) -> AsyncIterator[Transcript]: ...


class SpeechSynthesizer(Protocol):
    def synthesize(self, text: str, *, stream_id: str) -> AsyncIterator[AudioChunk]: ...


class TranscriptSequence:
    def __init__(self, stream_id):
        self.stream_id, self.next_sequence, self.ended = stream_id, 0, False

    def accept(self, item):
        if (not isinstance(item, Transcript) or self.ended
                or (item.stream_id, item.sequence) != (self.stream_id, self.next_sequence)):
            raise SpeechError('Transcript stream identity, order or termination changed')
        self.next_sequence += 1
        self.ended = item.final

    def finish(self):
        if not self.ended:
            raise SpeechError('Transcription ended without a final result')


class SpeechObservation:
    """Output observations only; no request bodies, audio retention or usage guesses."""
    CLOSED = 'closed'
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'
    FAILED = 'failed'

    def __init__(self):
        self.started = monotonic()
        self.first_output_seconds, self.usage = None, None
        self.output_chunks = 0

    def record(self, item):
        if self.first_output_seconds is None:
            self.first_output_seconds = monotonic() - self.started
        self.output_chunks += 1
        if isinstance(item, Transcript) and item.usage is not None:
            self.usage = deepcopy(item.usage)
        return item


@asynccontextmanager
async def speech_admission(operation, adapter):
    """Reuse the Host's existing admission owner; never create another call limit."""
    calls = current_calls()
    from .runtime import diagnostic, diagnostic_failure
    if calls is not None:
        await calls.acquire()
    observation, status = SpeechObservation(), SpeechObservation.CLOSED
    try:
        diagnostic('provider_request', operation=operation, stage='audio', adapter=adapter)
        yield observation
        status = SpeechObservation.COMPLETED
    except asyncio.CancelledError:
        status = SpeechObservation.CANCELLED
        raise
    except Exception as error:
        status = SpeechObservation.FAILED
        report = diagnostic_failure(error, operation=operation, stage='audio', adapter=adapter)
        if isinstance(error, CodedError):
            raise
        from .requests import ProviderError, error_code
        raise ProviderError(error_code(error), diagnostic=report) from error
    finally:
        diagnostic('speech_response', operation=operation, stage='audio', adapter=adapter,
                   status=status, seconds=monotonic() - observation.started,
                   first_output_seconds=observation.first_output_seconds,
                   output_chunks=observation.output_chunks, usage=observation.usage)
        if calls is not None:
            calls.release()


async def native_call(function, *args):
    """Do not abandon a synchronous native iterator while it still owns resources.

    Cancellation stops publication immediately; closing waits for an in-progress
    native call. There is no promise that a third-party CPU kernel is preemptible.
    """
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        task.exception()
        raise asyncio.CancelledError
    return task.result()
