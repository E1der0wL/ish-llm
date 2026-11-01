"""Audio SDK boundaries without a microphone, installed model or remote endpoint."""

import asyncio
import json
import tempfile
import unittest
from contextlib import aclosing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llm.providers.audio import AudioChunk, AudioFormat, AudioSequence, Transcript, TranscriptSequence, SpeechError
from llm.providers.calls import ProviderCalls, ProviderLimits
from llm.providers.local_speech import WhisperTranscriber, PiperSynthesizer
from llm.providers.server_speech import RealtimeTranscriber, ServerSynthesizer


class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_server_failure_is_coded_and_never_adds_a_retry(self):
        from llm.providers.requests import ProviderError
        from llm.providers.runtime import diagnostic_scope
        seen, events = [], []
        class Failure(RuntimeError):
            status_code = 429
        class Client:
            def __init__(self, **kwargs): seen.append(kwargs)
            async def __aenter__(self): raise Failure('private server message')
            async def __aexit__(self, *args): pass
        adapter = ServerSynthesizer(client_options={'base_url': 'http://speech.internal'},
                                    speech={'model': 'selected', 'voice': 'selected'})
        calls = ProviderCalls(ProviderLimits(max_active=1))
        with patch.dict('sys.modules', {'openai': SimpleNamespace(AsyncOpenAI=Client)}), calls.scope(), diagnostic_scope(events.append):
            with self.assertRaises(ProviderError) as raised:
                await anext(adapter.synthesize('private input', stream_id='u'))
        self.assertEqual(raised.exception.code, 'provider_rate_limit')
        self.assertEqual(len(seen), 1)
        self.assertEqual(calls.stats['active'], 0)
        result = next(item for item in events if item.code == 'speech_response')
        self.assertIsNone(result.details['usage'])
        self.assertEqual(result.details['status'], 'failed')
        self.assertNotIn('private input', str(result.to_dict()))

    async def test_cancel_during_local_iterator_preparation_closes_it(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        closed = []
        class Iterator:
            def __iter__(self): return self
            def __next__(self): raise AssertionError('Cancelled preparation must not start synthesis')
            def close(self): closed.append(True)
        class Voice:
            @staticmethod
            def load(*args, **kwargs):
                entered.set()
                release.wait()
                return Voice()
            def synthesize(self, *args, **kwargs): return Iterator()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'voice.onnx'
            path.touch()
            adapter = PiperSynthesizer(voice_path=path)
            with patch.dict('sys.modules', {'piper': SimpleNamespace(PiperVoice=Voice, SynthesisConfig=lambda **kwargs: kwargs)}):
                task = asyncio.create_task(anext(adapter.synthesize('text', stream_id='u')))
                try:
                    await asyncio.to_thread(entered.wait)
                    task.cancel()
                finally:
                    release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            self.assertEqual(closed, [True])

    async def test_stream_identity_order_and_final_are_required(self):
        format = AudioFormat(16000, 1)
        sequence = AudioSequence('u', format)
        for item in (AudioChunk('other', 0, format, b'', False),
                     AudioChunk('u', 1, format, b'', False)):
            with self.assertRaises(SpeechError):
                sequence.accept(item)
        sequence.accept(AudioChunk('u', 0, format, b'\x00\x00', False))
        with self.assertRaises(SpeechError):
            sequence.finish()
        sequence.accept(AudioChunk('u', 1, format, b'', True))
        sequence.finish()
        with self.assertRaises(SpeechError):
            sequence.accept(AudioChunk('u', 2, format, b'', True))
        transcript = TranscriptSequence('u')
        transcript.accept(Transcript('u', 0, 'partial', False, None))
        with self.assertRaises(SpeechError):
            transcript.finish()
        transcript.accept(Transcript('u', 1, 'final', True, None))
        transcript.finish()

    async def test_local_input_requires_installed_model_and_preserves_sdk_options(self):
        calls = []
        class Model:
            def __init__(self, path, **options):
                calls.append(('load', path, options))
            def transcribe(self, data, **options):
                calls.append(('transcribe', len(data), options))
                return iter([SimpleNamespace(text='hello')]), None
        with tempfile.TemporaryDirectory() as directory, patch.dict('sys.modules', {
            'faster_whisper': SimpleNamespace(WhisperModel=Model)
        }):
            adapter = WhisperTranscriber(model_path=directory, max_audio_frames=4, partial_frames=2,
                model_options={'compute_type': 'int8'}, transcription={'language': 'en'})
            async def chunks():
                yield AudioChunk('u', 0, adapter.input_format, b'\x00\x00' * 2, False)
                yield AudioChunk('u', 1, adapter.input_format, b'\x00\x00', True)
            stream = adapter.transcribe(chunks(), stream_id='u')
            first = await anext(stream)
            self.assertFalse(first.final)
            final = await anext(stream)
            self.assertTrue(final.final)
            await stream.aclose()
            self.assertEqual(calls[0][2], {'local_files_only': True, 'compute_type': 'int8'})
            self.assertEqual(calls[-1][2], {'language': 'en'})
            self.assertIsNone(final.usage)
            with self.assertRaises(ValueError):
                WhisperTranscriber(model_path=directory, max_audio_frames=4, partial_frames=2,
                    model_options={'local_files_only': False})

    async def test_realtime_input_streams_before_capture_finishes_and_uses_shared_admission(self):
        sent, options, inbox = [], [], asyncio.Queue()
        capture_end = asyncio.Event()
        class Connection:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def send(self, raw):
                event = json.loads(raw)
                sent.append(event)
                if event['type'] == 'input_audio_buffer.append':
                    await inbox.put({'type': 'conversation.item.input_audio_transcription.delta',
                                     'item_id': 'item-1', 'delta': 'hello'})
                if event['type'] == 'input_audio_buffer.commit':
                    await inbox.put({'type': 'conversation.item.input_audio_transcription.completed',
                                     'item_id': 'item-1', 'transcript': 'hello world'})
            async def recv(self): return json.dumps(await inbox.get())
        def connect(url, **kwargs):
            options.append((url, kwargs))
            return Connection()
        modules = {'websockets': SimpleNamespace(), 'websockets.asyncio': SimpleNamespace(),
                   'websockets.asyncio.client': SimpleNamespace(connect=connect)}
        adapter = RealtimeTranscriber(url='ws://speech.internal/transcribe',
            transcription={'model': 'internal-stt', 'language': 'ko'},
            connection_options={'open_timeout': 7})
        async def chunks():
            yield AudioChunk('u', 0, adapter.input_format, b'\x00\x00', False)
            await capture_end.wait()
            yield AudioChunk('u', 1, adapter.input_format, b'', True)
        calls = ProviderCalls(ProviderLimits(max_active=1))
        with patch.dict('sys.modules', modules), calls.scope():
            async with aclosing(adapter.transcribe(chunks(), stream_id='u')) as stream:
                partial = await anext(stream)
                self.assertEqual(partial.text, 'hello')
                self.assertFalse(partial.final)
                self.assertFalse(capture_end.is_set())
                self.assertEqual(calls.stats['active'], 1)
                capture_end.set()
                final = await anext(stream)
                self.assertEqual(final.text, 'hello world')
                self.assertTrue(final.final)
            self.assertEqual(calls.stats['active'], 0)
        self.assertEqual(options, [('ws://speech.internal/transcribe', {'open_timeout': 7})])
        self.assertEqual(sent[0]['session']['audio']['input']['transcription']['model'], 'internal-stt')
        self.assertIsNone(sent[0]['session']['audio']['input']['turn_detection'])

    async def test_server_output_preserves_settings_and_streams_aligned_pcm(self):
        captured, reads = [], []
        class Response:
            headers = {'content-type': 'audio/pcm'}
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def iter_bytes(self):
                reads.append(1)
                yield b'\x01'
                reads.append(2)
                yield b'\x02\x03\x04'
        class Client:
            def __init__(self, **kwargs):
                captured.append(kwargs)
                self.audio = SimpleNamespace(speech=SimpleNamespace(with_streaming_response=SimpleNamespace(create=self.create)))
            def create(self, **kwargs):
                captured.append(kwargs)
                return Response()
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        adapter = ServerSynthesizer(client_options={'base_url': 'http://speech.internal', 'api_key': 'fixture', 'max_retries': 0},
                                    speech={'model': 'internal-tts', 'voice': 'installed-voice'})
        with patch.dict('sys.modules', {'openai': SimpleNamespace(AsyncOpenAI=Client)}):
            async with aclosing(adapter.synthesize('text', stream_id='u')) as stream:
                first = await anext(stream)
                self.assertEqual(first.data, b'\x01\x02\x03\x04')
                self.assertTrue((await anext(stream)).final)
        self.assertEqual(captured[0]['max_retries'], 0)
        self.assertNotIn('timeout', captured[0])
        self.assertEqual(captured[1], {'model': 'internal-tts', 'voice': 'installed-voice', 'input': 'text', 'response_format': 'pcm'})

    async def test_local_output_streams_and_closes_native_iterator(self):
        closed, captured = [], []
        class Voice:
            @staticmethod
            def load(path, **options):
                captured.append(options)
                return Voice()
            def synthesize(self, text, **options):
                captured.append(options)
                try:
                    yield SimpleNamespace(sample_rate=22050, sample_channels=1, sample_width=2, audio_int16_bytes=b'\x01\x02')
                    raise AssertionError('Early close must not ask the native iterator for another chunk')
                finally:
                    closed.append(True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'voice.onnx'
            path.touch()
            adapter = PiperSynthesizer(voice_path=path, synthesis={'length_scale': 1.2})
            with patch.dict('sys.modules', {'piper': SimpleNamespace(PiperVoice=Voice, SynthesisConfig=lambda **kwargs: kwargs)}):
                async with aclosing(adapter.synthesize('text', stream_id='u')) as stream:
                    self.assertEqual((await anext(stream)).data, b'\x01\x02')
            self.assertEqual(closed, [True])
            self.assertEqual(captured, [{}, {'syn_config': {'length_scale': 1.2}}])
