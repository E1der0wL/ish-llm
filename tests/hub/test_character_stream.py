"""Character reveal never changes authoritative text or blocks keyboard input."""

import asyncio
from dataclasses import replace
import unittest
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import Layout

from hub.model import ChatMessage, HubSnapshot, SessionSummary
from hub.ui.live import LiveHubView
from hub.widget.transcript.character_stream import CharacterStream
from tests.hub.support import SizedOutput, eventually


class CharacterStreamTests(unittest.IsolatedAsyncioTestCase):
    def test_reveal_survives_completion_and_resets_on_history_switch_or_interrupt(self):
        stream = CharacterStream()
        stream.reset()
        message = ChatMessage('assistant', '가e\u0301👩\u200d💻Z', status='streaming', id='a')
        stream.observe((message,))
        lengths = []
        while stream.advance():
            lengths.append(stream.shown)
        self.assertEqual(lengths, [1, 3, 6, 7])
        finished = replace(message, text=message.text + 'XY', status='completed')
        stream.observe((finished,))
        self.assertEqual(stream.project((finished,))[0].text, message.text)
        self.assertEqual(finished.text, message.text + 'XY')
        stream.observe((replace(finished, status='interrupted'),))
        self.assertFalse(stream.pending)
        stream.reset((message,))
        stream.observe((message,))
        self.assertFalse(stream.pending)

    async def test_each_ready_frame_reveals_one_character_and_input_remains_editable(self):
        view = LiveHubView()
        with create_pipe_input() as pipe:
            app = Application(layout=Layout(view.container, focused_element=view.composer),
                key_bindings=view.navigation_keys, input=pipe, output=SizedOutput(), full_screen=True)
            seen = []
            def rendered(_):
                count = view.transcript.control.character_stream.shown
                if not seen or seen[-1] != count:
                    seen.append(count)
            app.after_render += rendered
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                base = HubSnapshot('p', 'P', 'model', 'file', (SessionSummary('s', 'S', 'running'),), 's',
                    (ChatMessage('assistant', '', status='streaming', id='a'),))
                with set_app(app):
                    view.apply_snapshot(base)
                await asyncio.sleep(.05)
                seen.clear()
                text = 'abcdefghijklmnopqrstuvwxyz'
                with set_app(app):
                    view.apply_snapshot(replace(base, messages=(replace(base.messages[0], text=text),)))
                pipe.send_text('draft')
                await eventually(lambda: view.composer.text == 'draft')
                self.assertEqual(view.transcript.control.messages[-1].text, text)
                with set_app(app):
                    view.apply_snapshot(replace(base, messages=(replace(base.messages[0], text=text, status='completed'),)))
                await eventually(lambda: not view.transcript.control.character_stream.pending)
                await asyncio.sleep(.03)
                self.assertEqual([n for n in seen if n], list(range(1, len(text) + 1)))
            finally:
                app.exit()
                await task
                view.output_renderers.close()
                view.progress.close()
