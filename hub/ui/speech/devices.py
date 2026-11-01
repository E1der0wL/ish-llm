"""Hub-computer PortAudio devices. No microphone or sound API enters llm."""

import asyncio
from collections import deque
from threading import Lock

from llm.providers.audio import AudioChunk, AudioSequence, SpeechError, native_call


class CaptureBuffer:
    """A bounded callback-thread bridge with at most one queued wake-up.

    Overflow is reported and capture stops; audio is never silently discarded.
    No task or unbounded call_soon queue is created for every microphone block.
    """

    def __init__(self, capacity, loop):
        if type(capacity) is not int or capacity <= 0:
            raise ValueError('Microphone buffer capacity must be explicitly positive')
        self.capacity, self.loop = capacity, loop
        self.values, self.lock, self.ready = deque(), Lock(), asyncio.Event()
        self.closed, self.notified, self.error = False, False, None

    def _wake(self):
        if not self.notified:
            self.notified = True
            self.loop.call_soon_threadsafe(self.ready.set)

    def put(self, data):
        with self.lock:
            if self.closed:
                return False
            if len(self.values) >= self.capacity:
                self.error = SpeechError('Microphone queue exceeded its configured capacity')
                self.closed = True
            else:
                self.values.append(data)
            self._wake()
            return not self.closed

    def finish(self, error=None):
        with self.lock:
            self.closed, self.error = True, error or self.error
            self._wake()

    async def get(self):
        while True:
            with self.lock:
                if self.error is not None:
                    raise self.error
                if self.values:
                    return self.values.popleft()
                if self.closed:
                    return None
                self.ready.clear()
                self.notified = False
            await self.ready.wait()


class Microphone:
    def __init__(self, format, *, device, block_frames, queue_size):
        if any(type(value) is not int or value <= 0 for value in (block_frames, queue_size)):
            raise ValueError('Capture block and queue sizes must be explicitly positive')
        self.format, self.device, self.block_frames = format, device, block_frames
        self.queue_size, self.stream, self.buffer = queue_size, None, None

    async def open(self):
        from importlib import import_module
        sd = await native_call(import_module, 'sounddevice')

        self.buffer = CaptureBuffer(self.queue_size, asyncio.get_running_loop())

        def receive(data, frames, time, status):
            if status or frames > self.block_frames:
                self.buffer.finish(SpeechError('Microphone capture overrun or invalid block size'))
                raise sd.CallbackAbort
            if not self.buffer.put(bytes(data)):
                raise sd.CallbackAbort

        # Missing SDK options are omitted. The device format comes from the
        # selected STT adapter, while buffer values belong to Hub configuration.
        def start():
            stream = sd.RawInputStream(device=self.device, samplerate=self.format.sample_rate,
                channels=self.format.channels, dtype='int16', blocksize=self.block_frames,
                callback=receive)
            self.stream = stream
            try:
                stream.start()
            except BaseException:
                stream.close()
                raise
            return stream
        try:
            await native_call(start)
        except BaseException:
            await self.finish()
            raise

    async def read(self):
        if self.buffer is None:
            raise RuntimeError('Microphone is not open')
        return await self.buffer.get()

    async def finish(self):
        stream, self.stream = self.stream, None
        try:
            if stream is not None:
                await native_call(stream.close)
        finally:
            if self.buffer is not None:
                self.buffer.finish()


class Speakers:
    def __init__(self, *, device, block_frames, queue_size):
        if any(type(value) is not int or value <= 0 for value in (block_frames, queue_size)):
            raise ValueError('Playback block and queue sizes must be explicitly positive')
        self.device, self.block_frames, self.queue_size = device, block_frames, queue_size

    async def play(self, chunks, *, stream_id):
        from importlib import import_module
        sd = await native_call(import_module, 'sounddevice')
        from time import monotonic
        from llm.providers.runtime import diagnostic

        started, first = monotonic(), True

        # The bounded producer/consumer queue applies backpressure to the SDK.
        queue = asyncio.Queue(maxsize=self.queue_size)
        stream, producer, format, completed = None, None, None, False
        async def send():
            validator = None
            async for chunk in chunks:
                if validator is None:
                    validator = AudioSequence(stream_id, chunk.format)
                validator.accept(chunk)
                width = self.block_frames * chunk.format.frame_bytes
                for start in range(0, len(chunk.data), width):
                    await queue.put((chunk.format, chunk.data[start:start + width]))
            if validator is None:
                raise SpeechError('Speech output contained no audio stream')
            validator.finish()
            await queue.put(None)

        producer = asyncio.create_task(send())
        reader = None
        try:
            while True:
                reader = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait((producer, reader), return_when=asyncio.FIRST_COMPLETED)
                if producer in done:
                    producer.result()
                item = await reader
                if item is None:
                    completed = True
                    break
                current_format, data = item
                if stream is None:
                    format = current_format
                    def start():
                        nonlocal stream
                        output = sd.RawOutputStream(device=self.device,
                            samplerate=format.sample_rate, channels=format.channels,
                            dtype='int16', blocksize=self.block_frames)
                        stream = output
                        try:
                            output.start()
                        except BaseException:
                            output.close()
                            raise
                        return output
                    await native_call(start)
                await native_call(stream.write, data)
                if first:
                    first = False
                    diagnostic('speech_playback_started', stream_id=stream_id,
                               first_write_seconds=monotonic() - started)
        finally:
            for task in (producer, reader):
                if task is not None:
                    task.cancel()
            try:
                if stream is not None:
                    try:
                        await native_call(stream.stop if completed else stream.abort)
                    finally:
                        await native_call(stream.close)
            finally:
                await asyncio.gather(*(task for task in (producer, reader) if task is not None), return_exceptions=True)
