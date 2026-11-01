"""Hub speech routing and devices with no external service or physical hardware."""

import asyncio
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llm.providers.audio import AudioChunk, AudioFormat, Transcript, SpeechError
from llm.providers.calls import ProviderCalls
from hub.config.speech import SpeechConfig, SpeechStore
from hub.ui.speech.devices import CaptureBuffer
from hub.ui.speech.session import SpeechSession
from hub.ui.speech.controller import SpeechController, SpokenOutput
from hub.model import ChatMessage, AnswerDraft


def configuration(**interaction):
    return SpeechConfig(input={'implementation': 'realtime-server', 'config': {
        'url': 'ws://speech.internal', 'transcription': {'model': 'test'}}},
        output={'implementation': 'speech-server', 'config': {
            'client_options': {'base_url': 'http://speech.internal', 'api_key': 'fixture'},
            'speech': {'model': 'test', 'voice': 'voice'}}},
        capture={'device': None, 'block_frames': 4, 'queue_size': 2},
        playback={'device': None, 'block_frames': 4, 'queue_size': 2},
        interaction={'submit': 'confirm', 'read_responses': True, 'text_chunk_chars': 4, **interaction})


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_configuration_is_optional_and_buffers_models_are_explicit(self):
        with tempfile.TemporaryDirectory() as workspace:
            store = SpeechStore(workspace)
            self.assertIsNone(store.load())
            self.assertFalse(store.path.exists())
            with self.assertRaises(ValueError):
                store.configure({})
            self.assertFalse(store.path.exists())
        with self.assertRaises(ValueError):
            replace(configuration(), capture={'device': None, 'block_frames': 4, 'queue_size': 0})
        with self.assertRaises(ValueError):
            replace(configuration(), interaction={'submit': 'automatic', 'read_responses': True})
        with self.assertRaises(ValueError):
            replace(configuration(), output=None, playback=None)
        with self.assertRaises(ValueError):
            replace(configuration(), interaction={'submit': 'confirm', 'read_responses': False, 'text_chunk_chars': -1})
        # Local input and server output are independent choices, including mixed setups.
        with tempfile.TemporaryDirectory() as model:
            config = replace(configuration(), input={'implementation': 'local-whisper', 'config': {
                'model_path': model, 'max_audio_frames': 8, 'partial_frames': 4}})
            self.assertEqual(config.build_input().path.name, __import__('pathlib').Path(model).name)
            self.assertEqual(config.build_output().speech['voice'], 'voice')

    async def test_microphone_callback_queue_is_bounded_and_never_silently_drops(self):
        queue = CaptureBuffer(2, asyncio.get_running_loop())
        self.assertTrue(queue.put(b'first'))
        self.assertTrue(queue.put(b'second'))
        self.assertFalse(queue.put(b'overflow'))
        self.assertEqual(len(queue.values), 2)
        with self.assertRaises(SpeechError):
            await queue.get()
        queue = CaptureBuffer(1, asyncio.get_running_loop())
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        await asyncio.to_thread(queue.put, b'audio')
        self.assertEqual(await waiter, b'audio')
        queue.finish()
        self.assertIsNone(await queue.get())

    async def test_partial_is_not_submitted_and_one_final_reuses_utterance_identity(self):
        queue, partials, finals, speaking = asyncio.Queue(), [], [], []
        partial_ready = asyncio.Event()
        class Microphone:
            async def open(self): pass
            async def read(self): return await queue.get()
            async def finish(self): await queue.put(None)
        class Transcriber:
            input_format = AudioFormat(16000, 1)
            async def transcribe(self, chunks, *, stream_id):
                sequence = 0
                async for chunk in chunks:
                    yield Transcript(stream_id, sequence, 'recognized', chunk.final, None)
                    sequence += 1
        session = SpeechSession(configuration(), ProviderCalls(), transcriber=Transcriber(),
            microphone_factory=lambda *args, **kwargs: Microphone())
        def partial(item):
            partials.append(item)
            partial_ready.set()
        task = asyncio.create_task(session.listen(partial=partial, final=finals.append,
                                                  speaking=lambda: speaking.append(True)))
        await queue.put(b'\x01\x00')
        await partial_ready.wait()
        self.assertEqual(finals, [])
        await session.finish_capture()
        await task
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0].stream_id, partials[0].stream_id)
        self.assertEqual(speaking, [True])
        self.assertIsNone(session.microphone)

    async def test_capture_cancellation_never_submits_a_final(self):
        finals = []
        waiting, finished = asyncio.Event(), asyncio.Event()
        class WaitingTranscriber:
            input_format = AudioFormat(16000, 1)
            async def transcribe(self, chunks, *, stream_id):
                yield Transcript(stream_id, 0, 'partial', False, None)
                waiting.set()
                await asyncio.Event().wait()
        class ClosingMicrophone:
            async def open(self): pass
            async def finish(self): finished.set()
        session = SpeechSession(configuration(), ProviderCalls(), transcriber=WaitingTranscriber(),
            microphone_factory=lambda *args, **kwargs: ClosingMicrophone())
        task = asyncio.create_task(session.listen(partial=lambda value: None,
            final=finals.append, speaking=lambda: None))
        await waiting.wait()
        session.cancel_capture()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())
        self.assertEqual(finals, [])

    def controller(self):
        view = SimpleNamespace(visible=True, project_id='project', selected=0, connected=True,
            sessions=[SimpleNamespace(id='session')], notice='', composer=SimpleNamespace(text=''),
            _drafts={}, _dialog=None, question=SimpleNamespace(item=None), settings_open=False,
            t=lambda key, **values: str(key))
        controller = SimpleNamespace(view=view, config=SimpleNamespace(
            _workspace_anchor=self.enterContext(tempfile.TemporaryDirectory())),
            app=SimpleNamespace(create_background_task=asyncio.create_task, invalidate=Mock()),
            _error=Mock(), submit=Mock(), interrupt=Mock())
        speech = SpeechController(controller)
        speech.config, speech.scope = configuration(), ('project', 'session')
        return speech

    async def test_cancelled_response_cannot_publish_late_audio(self):
        speech = self.controller()
        entered, released = asyncio.Event(), asyncio.Event()
        spoken = []
        async def speak(text, *, stream_id):
            spoken.append(text)
            entered.set()
            await released.wait()
        speech.session = SimpleNamespace(speak=speak, stop_playback=Mock())
        message = ChatMessage(role='assistant', text='safe', id='answer', status='streaming', run_id='run')
        snapshot = SimpleNamespace(project_id='project', selected_id='session', messages=[message])
        speech.observe(snapshot)
        async with asyncio.timeout(5):
            await entered.wait()
        snapshot.messages = [replace(message, text='safe late text', status='interrupted')]
        speech.observe(snapshot)
        await asyncio.gather(*tuple(speech.tasks), return_exceptions=True)
        speech.observe(snapshot)
        self.assertEqual(spoken, ['safe'])
        self.assertEqual(speech.outputs, {})
        self.assertIsNone(speech.read_task)
        speech.session.stop_playback.assert_called_once()
        speech.controller.interrupt.assert_not_called()

    async def test_voice_does_not_auto_answer_an_existing_approval(self):
        speech = self.controller()
        speech.config = configuration(submit='automatic')
        speech.view.question.item = SimpleNamespace(id='approval', revision='current')
        async def listen(**callbacks):
            callbacks['partial'](Transcript('utterance', 0, 'ap', False, None))
            self.assertEqual(speech.view.composer.text, '')
            callbacks['final'](Transcript('utterance', 1, 'approve', True, None))
        speech.session = SimpleNamespace(listen=listen)
        await speech._listen()
        self.assertEqual(speech.view.composer.text, 'approve')
        self.assertEqual(speech.request_key(('project', 'session'), 'approve'), 'utterance')
        speech.controller.submit.assert_not_called()

    async def test_playback_is_bounded_and_cancel_releases_device(self):
        import threading
        from hub.ui.speech.devices import Speakers
        entered, release = threading.Event(), threading.Event()
        full = asyncio.Event()
        output = Mock()
        def write(data):
            entered.set()
            release.wait()
        output.write.side_effect = write
        produced = []
        async def chunks():
            for ordinal in range(10):
                produced.append(ordinal)
                if len(produced) == 4:
                    full.set()
                yield AudioChunk('audio', ordinal, AudioFormat(16000, 1), b'\x00\x00', False)
            yield AudioChunk('audio', 10, AudioFormat(16000, 1), b'', True)
        with patch.dict('sys.modules', {'sounddevice': SimpleNamespace(RawOutputStream=Mock(return_value=output))}):
            task = asyncio.create_task(Speakers(device=None, block_frames=1, queue_size=2).play(chunks(), stream_id='audio'))
            try:
                await asyncio.to_thread(entered.wait)
                await full.wait()
                self.assertEqual(len(produced), 4)  # device + two queued blocks + one blocked producer
                task.cancel()
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        output.abort.assert_called_once()
        output.close.assert_called_once()

    async def test_duplicate_final_and_cancel_never_submit_a_turn(self):
        class Microphone:
            async def open(self): pass
            async def read(self): return None
            async def finish(self): pass
        class Transcriber:
            input_format = AudioFormat(16000, 1)
            async def transcribe(self, chunks, *, stream_id):
                yield Transcript(stream_id, 0, 'first', True, None)
                yield Transcript(stream_id, 1, 'duplicate', True, None)
        finals = []
        session = SpeechSession(configuration(), ProviderCalls(), transcriber=Transcriber(),
            microphone_factory=lambda *args, **kwargs: Microphone())
        with self.assertRaises(SpeechError):
            await session.listen(partial=lambda value: None, final=finals.append, speaking=lambda: None)
        self.assertEqual(finals, [])

    async def test_reading_uses_published_text_and_stop_never_calls_run_interrupt(self):
        speech = SpeechController.__new__(SpeechController)
        speech.config = configuration()
        speech.diagnostics = None
        speech.outputs, speech.read_task = {}, None
        spoken = []
        async def speak(text, *, stream_id): spoken.append((text, stream_id))
        speech.session = SimpleNamespace(speak=speak, stop_playback=Mock())
        speech.controller = SimpleNamespace(interrupt=Mock(), config=SimpleNamespace(
            _workspace_anchor=self.enterContext(tempfile.TemporaryDirectory())))
        speech.scope = ('project', 'session')
        message = ChatMessage('assistant', '', id='a', run_id='r', status='streaming',
                              draft=AnswerDraft('unreviewed candidate'))
        speech.outputs['a'] = SpokenOutput(message)
        await speech._read()
        self.assertEqual(spoken, [])
        speech.outputs['a'].message = replace(message, text='approved answer', status='completed', draft=None)
        await speech._read()
        self.assertEqual(''.join(text for text, _ in spoken), 'approved answer')
        speech.stop_playback()
        speech.session.stop_playback.assert_called_once()
        speech.controller.interrupt.assert_not_called()

    async def test_audio_reuses_host_admission_and_explicit_diagnostics(self):
        from llm.providers.runtime import ProviderDiagnostics, diagnostic_failure
        from hub.backend.runtime import HubRuntime

        speech = self.controller()
        calls = ProviderCalls()
        diagnostics = ProviderDiagnostics(error_details=True, max_detail_chars=17)
        runtime = HubRuntime.__new__(HubRuntime)
        runtime.backend = SimpleNamespace(provider_calls=calls,
            services=SimpleNamespace(provider_diagnostics=diagnostics))
        resources = await runtime.get_speech_resources()
        def receive(operation, *, completed):
            self.assertEqual(operation, 'get_speech_resources')
            completed(resources)
        speech.controller._call = receive
        with patch.object(speech.store, 'load', return_value=configuration()):
            session = await speech._connect()
        self.assertIs(session.calls, calls)
        self.assertIs(speech.diagnostics, diagnostics)
        reports = []
        class Rejected(RuntimeError):
            status_code = 400
            body = {'error': {'message': 'explicit speech detail', 'param': 'voice'}}
        async def speak(text, *, stream_id):
            reports.append(diagnostic_failure(Rejected('opaque'), operation='aspeech', stage='audio'))
        speech.session = SimpleNamespace(speak=speak)
        message = ChatMessage(role='assistant', text='public answer', id='answer', status='completed', run_id='run')
        speech.outputs[message.id] = SpokenOutput(message)
        await speech._read()
        self.assertTrue(reports)
        self.assertIn('voice', str(reports[0].to_dict()))
        self.assertNotIn('explicit speech detail', str(reports[0].to_dict()))

    async def test_vad_and_barge_in_use_only_explicit_thresholds(self):
        chunks = iter([b'\x00\x00' * 4, b'\xff\x7f' * 4, b'\x00\x00' * 4,
                       b'\xff\x7f' * 4, b'\x00\x00' * 4, None])
        class Microphone:
            async def open(self): pass
            async def read(self): return next(chunks, None)
            async def finish(self): pass
        class Transcriber:
            input_format = AudioFormat(16000, 1)
            async def transcribe(self, chunks, *, stream_id):
                received = [chunk async for chunk in chunks]
                self_test.assertTrue(received[-1].final)
                yield Transcript(stream_id, 0, 'turn', True, None)
        self_test = self
        finals, begins = [], []
        session = SpeechSession(configuration(speech_threshold=.2, silence_frames=4), ProviderCalls(),
            transcriber=Transcriber(), microphone_factory=lambda *args, **kwargs: Microphone())
        await session.listen(partial=lambda item: None, final=finals.append, speaking=lambda: begins.append(True))
        self.assertEqual(len(finals), 2)
        self.assertNotEqual(finals[0].stream_id, finals[1].stream_id)
        self.assertEqual(begins, [True, True])
