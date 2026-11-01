"""One Hub device interaction, never an additional Run or approval owner."""

import asyncio
import struct
from contextlib import aclosing
from uuid import uuid4

from llm.providers.audio import AudioChunk, TranscriptSequence
from .devices import Microphone, Speakers


class SpeechSession:
    def __init__(self, config, calls, *, transcriber=None, synthesizer=None,
                 microphone_factory=Microphone, speakers_factory=Speakers):
        if calls is None:
            raise ValueError('Speech requires the Backend shared ProviderCalls owner')
        self.config, self.calls = config, calls
        self.transcriber = transcriber if transcriber is not None else config.build_input()
        self.synthesizer = synthesizer if synthesizer is not None else config.build_output()
        self.microphone_factory, self.speakers_factory = microphone_factory, speakers_factory
        self.microphone, self.capture_task, self.playback_task = None, None, None

    async def _transcribe(self, chunks, identifier, partial, final):
        sequence = TranscriptSequence(identifier)
        completed = None
        async with aclosing(self.transcriber.transcribe(chunks, stream_id=identifier)) as stream:
            async for item in stream:
                sequence.accept(item)
                if item.final:
                    completed = item
                else:
                    partial(item)
        sequence.finish()
        if completed is not None and completed.text.strip():
            final(completed)

    @staticmethod
    def _level(data):
        # PCM normalization is a wire-format conversion, not a VAD preset.
        return max((abs(value[0]) for value in struct.iter_unpack('<h', data)), default=0) / 32768.0

    async def listen(self, *, partial, final, speaking):
        if self.transcriber is None:
            raise ValueError('Speech input is not configured')
        if self.capture_task is not None:
            raise RuntimeError('A microphone capture is already active')
        self.capture_task = asyncio.current_task()
        self.microphone = self.microphone_factory(self.transcriber.input_format, **self.config.capture)
        microphone = self.microphone
        format = self.transcriber.input_format
        behavior = self.config.interaction
        try:
            await microphone.open()
            with self.calls.scope():
                if 'speech_threshold' not in behavior:
                    identifier = uuid4().hex
                    speaking()
                    async def chunks():
                        sequence = 0
                        while (data := await microphone.read()) is not None:
                            yield AudioChunk(identifier, sequence, format, data, False)
                            sequence += 1
                        yield AudioChunk(identifier, sequence, format, b'', True)
                    async with aclosing(chunks()) as source:
                        await self._transcribe(source, identifier, partial, final)
                    return
                # Continuous VAD is enabled only by the application's explicit
                # threshold and silence length. No speech or silence preset exists.
                threshold, silence = behavior['speech_threshold'], behavior['silence_frames']
                while (first := await microphone.read()) is not None:
                    if self._level(first) < threshold:
                        continue
                    identifier = uuid4().hex
                    speaking()
                    async def utterance():
                        sequence, quiet = 0, 0
                        yield AudioChunk(identifier, sequence, format, first, False)
                        sequence += 1
                        while (data := await microphone.read()) is not None:
                            quiet = quiet + len(data) // format.frame_bytes if self._level(data) < threshold else 0
                            yield AudioChunk(identifier, sequence, format, data, False)
                            sequence += 1
                            if quiet >= silence:
                                break
                        yield AudioChunk(identifier, sequence, format, b'', True)
                    async with aclosing(utterance()) as source:
                        await self._transcribe(source, identifier, partial, final)
        finally:
            try:
                await microphone.finish()
            finally:
                self.microphone = self.capture_task = None

    async def finish_capture(self):
        """Commit captured speech; a separate cancel discards its pending turn."""
        if self.microphone is not None:
            await self.microphone.finish()

    def cancel_capture(self):
        if self.capture_task is not None:
            self.capture_task.cancel()

    def stop_playback(self):
        """Only stop synthesis/playback. Never cancel the model, Run or Tool."""
        if self.playback_task is not None:
            self.playback_task.cancel()

    async def speak(self, text, *, stream_id):
        if self.synthesizer is None:
            raise ValueError('Speech output is not configured')
        if self.playback_task is not None:
            raise RuntimeError('Speech playback is already active')
        self.playback_task = asyncio.current_task()
        try:
            speakers = self.speakers_factory(**self.config.playback)
            with self.calls.scope():
                async with aclosing(self.synthesizer.synthesize(text, stream_id=stream_id)) as stream:
                    await speakers.play(stream, stream_id=stream_id)
        finally:
            self.playback_task = None

    async def aclose(self):
        tasks = [task for task in (self.capture_task, self.playback_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
