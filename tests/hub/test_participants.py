"""Invitations, recipient routing, concurrent display and sidebar focus."""

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType, MouseButton
from prompt_toolkit.data_structures import Point

from hub.backend.runtime import HubConfig, HubRuntime
from hub.model import ReasoningMessage
from hub.ui.presentation import present
from hub.locales import Language
from llm.engines.base import BaseEngine
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowGraph
from tests.hub.support import SizedOutput, eventually, render_container


class ParticipantBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_invitation_persists_and_all_or_single_recipient_streams(self):
        release = asyncio.Event()
        entered = {name: asyncio.Event() for name in ('a', 'b')}
        async def action(context):
            name = context.run.agent_id
            yield name + ' first'
            entered[name].set()
            await release.wait()
            yield name + ' last'
        with tempfile.TemporaryDirectory() as root:
            config = HubConfig(root, agent_id='a', agent_definition={'engine': 'host', 'metadata': {'title': 'Coder'}},
                auto_title=False, engine_factories={'host': lambda: BaseEngine(action=action)})
            runtime = HubRuntime(config)
            try:
                await runtime.start()
                await runtime.project.agents.acreate({'engine': 'host', 'metadata': {'title': 'Reviewer'}}, identifier='b')
                sid = await runtime.new_session('new', 'Room')
                self.assertEqual((await runtime.snapshot()).participant_ids, ('a',))
                await runtime.invite_agent(runtime.project.id, sid, 'b')
                await runtime.invite_agent(runtime.project.id, sid, 'b')
                self.assertEqual((await runtime.sessions[sid].aget_data()).agent_ids, ['a', 'b'])
                result = await runtime.submit_input(sid, 'shared', ('a', 'b'))
                await asyncio.wait_for(asyncio.gather(*(v.wait() for v in entered.values())), 10)
                snapshot = await runtime.snapshot()
                replies = [m for m in snapshot.messages if m.role == 'assistant']
                self.assertEqual({m.author for m in replies}, {'Coder', 'Reviewer'})
                self.assertEqual(len({m.id for m in replies}), 2)
                self.assertTrue(all('first' in m.text for m in replies))
                await runtime.project.agents.acreate({'engine': 'host'}, identifier='c')
                with self.assertRaisesRegex(ValueError, '실행|active'):
                    await runtime.invite_agent(runtime.project.id, sid, 'c')
                self.assertEqual((await runtime.sessions[sid].aget_data()).agent_ids, ['a', 'b'])
                self.assertIsNotNone((await runtime.submission_state(sid, ('a', 'b')))['run_id'])
                release.set()
                await runtime.sessions[sid].run.wait_idle()
                await runtime.submit_input(sid, 'only b', ('b',))
                await runtime.sessions[sid].run.wait_idle()
                runs = await runtime.sessions[sid].run.alist()
                self.assertEqual(len(runs), 3)
                self.assertEqual(runs[-1].data.agent_id, 'b')
                project_id = runtime.project.id
            finally:
                release.set()
                await runtime.close()
            reopened = HubRuntime(replace(config, project_id=project_id, session_id=sid))
            try:
                await reopened.start()
                self.assertEqual((await reopened.snapshot()).participant_ids, ('a', 'b'))
            finally:
                await reopened.close()

    async def test_running_graph_is_drawn_from_recorded_definition(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def hold(node):
            entered.set()
            await release.wait()
            return {}
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False,
                agent_definition={'engine': 'host'},
                engine_factories={'host': lambda: BaseEngine(action=lambda ctx: None),
                                  'graph': lambda: GraphEngine(handlers={'hold': hold})}))
            try:
                await runtime.start()
                flow = WorkflowGraph(entry='working').node('working', 'hold').node('done', 'end').connect('working', 'done')
                await runtime.project.components.workflows.acreate(flow.to_dict(), identifier='flow')
                await runtime.project.agents.acreate({'engine': 'graph', 'engine_options': {'workflow': 'flow'}}, identifier='planner')
                sid = await runtime.new_session()
                await runtime.invite_agent(runtime.project.id, sid, 'planner')
                await runtime.submit_input(sid, 'do it', ('planner',))
                await asyncio.wait_for(entered.wait(), 10)
                snapshot = await runtime.snapshot()
                thought, = [m for m in snapshot.messages if m.role == ReasoningMessage.ROLE]
                self.assertIn('working', thought.workflow, [(s.kind, s.metadata) for s in
                    await (await runtime.sessions[sid].run.aload(snapshot.run.id)).steps.alist()])
                self.assertEqual(dict(thought.workflow_states)['working'], 'running')
                rendered = present(snapshot, Language('en'))
                graph, = [m for m in rendered.messages if m.role == ReasoningMessage.ROLE]
                self.assertIn('working', graph.trace[0].text)
                self.assertIn('done', graph.trace[0].text)
                release.set()
                await runtime.sessions[sid].run.wait_idle()
                finished = await runtime.snapshot()
                thought, = [m for m in finished.messages if m.role == ReasoningMessage.ROLE]
                self.assertEqual(dict(thought.workflow_states)['working'], 'completed')
            finally:
                release.set()
                await runtime.close()


class ParticipantUITests(unittest.IsolatedAsyncioTestCase):
    def test_session_switch_does_not_use_previous_session_participants(self):
        from hub.ui.live import LiveHubView
        from hub.model import HubSnapshot, SessionSummary
        view = LiveHubView()
        snapshot = HubSnapshot('p', 'Project', '', 'file', (
            SessionSummary('s', 'First', 'idle', participant_ids=('a',)),
            SessionSummary('t', 'Second', 'idle', participant_ids=('b',)),
        ), 's', (), participant_ids=('a',))
        try:
            view.apply_snapshot(snapshot)
            view.recipient_id = 'a'
            view.apply_snapshot(snapshot, select_id='t')
            self.assertEqual(view.recipients, ('b',))
            view.recipient_id = 'b'
            view.apply_snapshot(snapshot)  # Old selected-session observation in flight.
            self.assertEqual(view.recipients, ('b',))
            view.apply_snapshot(snapshot, select_id='s')
            self.assertEqual(view.recipient_id, 'a')
        finally:
            view.close()

    def test_unrenderable_graph_explains_limitation_without_erasing_reasoning(self):
        from hub.ui.presentation import present_message
        from hub.widget.transcript.thought_trace import render_trace_item
        from hub.config.theme import HubTheme
        message = ReasoningMessage.create('a', 'Keep this reasoning', workflow='invalid json')
        display = present_message(message, Language('en'))
        rows = render_trace_item(display.trace[0], 70, HubTheme(), Language('en'))
        self.assertIn('Cannot display', ''.join(text for row in rows for style, text in row))
        self.assertEqual(display.trace[1].text, 'Keep this reasoning')

    async def test_broadcast_followup_keeps_all_recipients_and_never_steers_one_implicitly(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from hub.model import SessionView, SubmissionResult
        from hub.ui.live import LiveController, LiveHubView
        with tempfile.TemporaryDirectory() as root:
            view = LiveHubView()
            controller = LiveController(view, HubConfig(root, auto_title=False))
            controller.app = SimpleNamespace(invalidate=Mock())
            view.project_id, view.connected, view.visible = 'p', True, True
            view.sessions = [SessionView('Room', id='s')]
            view.participant_ids = ('a', 'b')
            calls, dialogs = [], []
            def call(operation, *args, completed=None, **kwargs):
                calls.append((operation, args))
                if len(calls) == 1:
                    completed(SubmissionResult(run_id='running-a'))
            controller._call = call
            view.open_dialog = lambda *args, **kwargs: dialogs.append(args)
            try:
                view.composer.text = 'continue both'
                controller.submit('s', view.composer.text)
                choices = dialogs[0][3]
                self.assertEqual([value for value, label in choices.values], ['follow_up'])
                dialogs[0][2]()
                self.assertEqual(calls[-1][0], 'submit_input')
                self.assertEqual(calls[-1][1][2], ('a', 'b'))
                self.assertFalse(calls[-1][1][-1])
            finally:
                view.close()

    async def test_sidebar_selection_toggle_exclusivity_and_focus(self):
        from examples.hub.preview import create_preview
        with create_pipe_input() as pipe:
            app, view, _ = create_preview(input=pipe, output=SizedOutput())
            view.participant_ids = view.agents = ('a', 'b')
            view.agent_titles = {'a': 'Coder', 'b': 'Reviewer'}
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))
            try:
                await eventually(lambda: app.is_running)
                with set_app(app):
                    view.output.tags.open()
                    view.output.participants.open()
                    view.composer.text = 'keep draft'
                bar = view.output.participants
                self.assertEqual(bar.width(), view.output.tags.width())
                self.assertEqual(bar.container.content.preferred_width(100).preferred,
                                 view.output.tags.container.content.preferred_width(100).preferred)
                self.assertFalse(view.output.tags.visible)
                pipe.send_text('\x1b[B')
                await eventually(lambda: bar.selected == 'b')
                self.assertEqual(view.recipient_id, '')
                pipe.send_text(' ')
                await eventually(lambda: view._dialog is not None)
                pipe.send_text('\x1b[B\x1b[B\r')
                await eventually(lambda: view.recipient_id == 'b')
                self.assertIn('Reviewer', view.recipient_title())
                pipe.send_text('\r')
                await eventually(lambda: view._dialog is not None)
                pipe.send_text('\x1b[B\x1b[B\r')
                await eventually(lambda: view.recipient_id == '')
                self.assertEqual(view.recipients, ('a', 'b'))
                with set_app(app):
                    bar.control.mouse_handler(MouseEvent(Point(2, 25), MouseEventType.MOUSE_UP, MouseButton.LEFT, frozenset()))
                    self.assertEqual(bar.selected, 'b')
                    lines = render_container(view.output, 100, 25)
                    self.assertTrue(any(view.t('participants_add') in line for line in lines))
                    available = view.output.available_width
                    try:
                        for width in (14, 18, 24):
                            view.output.available_width = width * bar.AVAILABLE_FRACTION
                            lines = render_container(bar.container, width, 8)
                            self.assertFalse(any('Window too' in line for line in lines))
                            self.assertTrue(any(view.t('participants_title') in line for line in lines))
                    finally:
                        view.output.available_width = available
                pipe.send_text('\t')
                await eventually(lambda: app.layout.current_control is bar.add_button.control)
                pipe.send_text('\x1b')
                await eventually(lambda: not bar.visible)
                self.assertIs(app.layout.current_control, view.composer.control)
                self.assertEqual(view.composer.text, 'keep draft')
            finally:
                app.exit()
                await task
                view.close()
