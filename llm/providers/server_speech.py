"""Explicit server adapters: OpenAI-compatible realtime STT and PCM speech output.

The selected server must actually implement these protocols. Chat-completions
compatibility alone does not imply audio support. No server/model/voice is chosen.
"""

import asyncio
import base64
import json
from copy import deepcopy
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from importlib import import_module

from .audio import AudioFormat, AudioChunk, AudioSequence, Transcript, SpeechError, speech_admission, native_call


@asynccontextmanager
async def _connection(url, options):
    sdk = await native_call(import_module, 'websockets.asyncio.client')
    async with sdk.connect(url, **options) as connection:
        yield connection


@asynccontextmanager
async def _client(options):
    sdk = await native_call(import_module, 'openai')
    async with sdk.AsyncOpenAI(**options) as client:
        yield client


class _ServerError(RuntimeError):
    def __init__(self, error):
        # Details are available only through the existing opt-in diagnostic
        # policy. The exception message never echoes a server payload.
        self.body = {'error': error}
        super().__init__('Speech server rejected the audio operation')


class RealtimeTranscriber:
    input_format = AudioFormat(24000, 1)  # Realtime pcm_s16le wire protocol.

    def __init__(self, *, url, transcription, connection_options=None):
        if not isinstance(url, str) or urlsplit(url).scheme not in {'ws', 'wss'}:
            raise ValueError('Realtime transcription requires an explicit WebSocket URL')
        if not isinstance(transcription, dict) or not isinstance(transcription.get('model'), str) or not transcription['model']:
            raise ValueError('Realtime transcription requires an explicit model')
        self.url, self.transcription = url, deepcopy(transcription)
        self.connection_options = deepcopy(connection_options if connection_options is not None else {})

    async def transcribe(self, chunks, *, stream_id):
        sequence = AudioSequence(stream_id, self.input_format)
        committed = asyncio.Event()
        async with speech_admission('atranscribe', 'realtime') as observation, _connection(self.url, self.connection_options) as connection:
            # Client-selected utterance boundaries preserve the caller's stable ID.
            # Server VAD must not silently split one committed input into new requests.
            await connection.send(json.dumps({'type': 'session.update', 'session': {
                'type': 'transcription', 'audio': {'input': {
                    'format': {'type': 'audio/pcm', 'rate': self.input_format.sample_rate},
                    'transcription': self.transcription, 'turn_detection': None,
                }}}}))

            async def send():
                async for chunk in chunks:
                    sequence.accept(chunk)
                    if chunk.data:
                        await connection.send(json.dumps({'type': 'input_audio_buffer.append',
                            'audio': base64.b64encode(chunk.data).decode('ascii')}))
                sequence.finish()
                committed.set()
                await connection.send(json.dumps({'type': 'input_audio_buffer.commit'}))

            text, item_id, revision = '', None, 0
            sender, receiver = asyncio.create_task(send()), None
            try:
                while True:
                    receiver = asyncio.create_task(connection.recv())
                    done, _ = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
                    if sender in done:
                        sender.result()
                    raw = await receiver
                    event = json.loads(raw)
                    kind = event.get('type')
                    if kind == 'error' or kind == 'conversation.item.input_audio_transcription.failed':
                        raise _ServerError(event.get('error'))
                    if kind not in {'conversation.item.input_audio_transcription.delta',
                                    'conversation.item.input_audio_transcription.completed'}:
                        continue
                    identifier = event.get('item_id')
                    if not isinstance(identifier, str) or not identifier or item_id is not None and identifier != item_id:
                        raise SpeechError('Server transcript belongs to a different audio item')
                    item_id = identifier
                    final = kind.endswith('.completed')
                    if final:
                        if not committed.is_set():
                            raise SpeechError('Server finalized an audio turn before the client committed it')
                        await sender
                        text = event['transcript']
                    else:
                        delta = event['delta']
                        if not isinstance(delta, str):
                            raise SpeechError('Server transcript delta must be text')
                        text += delta
                    yield observation.record(Transcript(stream_id, revision, text, final, event.get('usage')))
                    revision += 1
                    if final:
                        return
            finally:
                pending = [task for task in (sender, receiver) if task is not None]
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)


class ServerSynthesizer:
    output_format = AudioFormat(24000, 1)  # OpenAI-compatible PCM response contract.

    def __init__(self, *, client_options, speech):
        if not isinstance(client_options, dict) or not client_options.get('base_url'):
            raise ValueError('Speech output requires an explicit server base_url')
        if not isinstance(speech, dict) or any(not isinstance(speech.get(key), str) or not speech[key] for key in ('model', 'voice')):
            raise ValueError('Speech output requires an explicit model and voice')
        if 'input' in speech or speech.get('response_format', 'pcm') != 'pcm':
            raise ValueError('Streaming speech input and PCM format are runtime protocol fields')
        self.client_options, self.speech = deepcopy(client_options), deepcopy(speech)

    async def synthesize(self, text, *, stream_id):
        async with speech_admission('aspeech', 'openai-compatible') as observation, _client(self.client_options) as client:
            async with client.audio.speech.with_streaming_response.create(
                **{**self.speech, 'input': text, 'response_format': 'pcm'}
            ) as response:
                content_type = response.headers.get('content-type', '').split(';')[0].strip()
                if content_type not in {'audio/pcm', 'application/octet-stream'}:
                    raise SpeechError('Speech server returned a non-PCM response')
                carry, sequence = b'', 0
                async for data in response.iter_bytes():
                    carry += data
                    size = len(carry) - len(carry) % self.output_format.frame_bytes
                    if size:
                        yield observation.record(AudioChunk(stream_id, sequence, self.output_format, carry[:size], False))
                        carry = carry[size:]
                        sequence += 1
                if carry or not sequence:
                    raise SpeechError('Speech server returned incomplete or empty PCM audio')
                yield observation.record(AudioChunk(stream_id, sequence, self.output_format, b'', True))
