"""Backend reads primitive observations once; UI chooses language and glyphs."""

from llm.core.models import Message

from dataclasses import asdict, replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from hub.asset.icon import NERD, UNICODE
from hub.backend.observability.snapshot import SnapshotReader
from hub.backend.execution.recovery_model import RecoveryStepMetadata
from llm.components.memory.processing import MemorySession
from hub.model import ResponseActivity
from hub.ui.presentation import present_message
from hub.widget.transcript.thought_trace import render_trace_item
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.ui.live import LiveHubView
from hub.ui.presentation import present
from hub.widget.transcript.conversation import render_messages
from llm.core.results import CompletionResult


def headers(messages, language):
    return "".join(text for line in render_messages(messages, 120, HubTheme(), language, section="header")
                   for _, text in line)


class SnapshotProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconciled_summary_shows_activity_instead_of_auxiliary_thoughts(self):
        start = '2026-10-12T00:00:00+00:00'
        steps = (
            NS(id='analysis', kind='llm', name='Hub helper', status='completed', started_at=start,
               metadata=RecoveryStepMetadata(RecoveryStepMetadata.ANALYSIS).to_record()),
            NS(id='summary', kind='memory', name=MemorySession.ACTIVE_STEP, status='running',
               started_at=start, metadata={}))
        run = NS(id='r', engine='loop', status='running', started_at=start, ended_at=None,
            metadata={'completions': [asdict(CompletionResult(id=s.id, step_id=s.id,
                reasoning_content='Internal error recovery details')) for s in steps]})
        record = Message(id='a', role='assistant', content='Previous partial answer', status='streaming',
                         created_at=start, run_id='r', metadata={})
        reader = SnapshotReader()
        messages = await reader.messages(None, [record], latest=run, latest_steps=steps)
        self.assertEqual([m.role for m in messages], ['reasoning', 'assistant'])
        self.assertEqual(messages[0].text, '')
        self.assertEqual(messages[1].text, record.content)
        self.assertEqual(messages[0].phase, ResponseActivity.SUMMARIZING)
        for code, expected in (('ko', '컨텍스트 압축'), ('en', 'Context compaction')):
            language = Language(code)
            displayed = present_message(messages[0], language)
            text = ''.join(fragment[1] for item in displayed.trace
                for row in render_trace_item(item, 80, HubTheme(), language) for fragment in row)
            self.assertIn(expected, text)
            self.assertNotIn('Internal error', text)
        self.assertIn('Internal error', str(run.metadata))  # Preserve durable diagnostic receipts.
        steps[1].status = 'completed'
        messages = await reader.messages(None, [record], latest=run, latest_steps=steps)
        self.assertEqual([m.role for m in messages], ['assistant'])

    async def test_thoughts_and_all_tools_are_interleaved_from_durable_timestamps(self):
        start = "2026-10-12T00:00:"
        run = NS(id="r", engine="loop", status="running", started_at=start + "00+00:00", ended_at=None,
            metadata={"completions": [
                asdict(CompletionResult(id="c1", started_at=start + "01+00:00", reasoning_content="Inspect files")),
                asdict(CompletionResult(id="c2", started_at=start + "09+00:00", reasoning_content="Now verify"))]})
        record = Message(id="a", role="assistant", content="Partial", status="streaming", created_at="", run_id="r", metadata={})
        steps = tuple(NS(id=f"t{i}", name=f"tool_{i}", status="completed", kind="tool", started_at=start + f"0{i+2}+00:00") for i in range(7))
        for reader in (SnapshotReader(), SnapshotReader()):
            messages = await reader.messages(None, [record], latest=run, latest_steps=steps)
            self.assertEqual([item.text for item in messages[0].trace], ["Inspect files", *(s.name for s in steps), "Now verify"])

    async def test_reconciliation_restores_all_rounds_above_partial_answer(self):
        run = NS(id="r", engine="loop", status="running", started_at=None, ended_at=None,
                 metadata={"completions": [asdict(CompletionResult(reasoning_content="First")),
                     asdict(CompletionResult(reasoning_content="Second"))]})
        record = Message(id="a", role="assistant", content="Partial answer", status="streaming",
                    created_at="", run_id="r", metadata={})
        reader = SnapshotReader()
        steps = (NS(id="t1", name="file_read", status="completed", kind="tool"),
                 NS(id="t2", name="shell_execute", status="running", kind="tool"))
        messages = await reader.messages(None, [record], latest=run, latest_steps=steps)
        self.assertEqual([m.role for m in messages], ["reasoning", "assistant"])
        self.assertEqual(messages[0].text, "First\n\nSecond")
        self.assertEqual([(s.name, s.status) for s in messages[0].tools],
                         [("file_read", "completed"), ("shell_execute", "running")])
        record.status = "completed"
        run.status = "completed"
        self.assertEqual([m.role for m in await reader.messages(None, [record], latest=run)], ["assistant"])

    async def test_read_only_snapshot_has_no_localized_display_and_reuses_observations(self):
        config = NS(agent_id="assistant", model=None, file_root=None, user_profile=NS(display_name="Account"))
        record = NS(id="s", title="Session", agent_ids=["assistant"], config={}, metadata={"hub_auto_title": True})
        run = NS(id="r", session_id="s", status="running", engine="loop", started_at="2026-10-01T00:00:00+00:00",
                 ended_at=None, error=None, metadata={"completions": [asdict(CompletionResult(reasoning_content="Summary", model="test/model"))]})
        handle = NS(id="r", aget_data=AsyncMock(return_value=run), steps=NS(alist=AsyncMock(return_value=[NS(name="LLM", status="running", kind="llm")])))
        status = NS(status="running", queued_count=2, active_runs=[NS(run_id="r", agent_id="assistant")])
        message = Message(id="m", role="assistant", content="", status="streaming", created_at=run.started_at, run_id="r", metadata={})
        session = NS(aget_data=AsyncMock(return_value=record), aconversation=AsyncMock(return_value=[message]),
                     run=NS(astatus=AsyncMock(return_value=status), alist=AsyncMock(side_effect=lambda *, query: [] if query.status == "paused" else [handle]), aload=AsyncMock()))
        project = NS(aget_data=AsyncMock(return_value=NS(id="p", title="Project", config={}, components=["tools"], conversation_storage="file")),
                     paths=NS(root="/workspace"), agents=NS(alist=AsyncMock(return_value={"assistant": {"engine": "loop"}})))
        engines = NS(names=lambda: ("loop",), get=lambda _: NS(), resolve_agent=lambda _: NS(resolve_config=lambda *a, **kw: {"values": {"config": {"completion": {"model": "test/model"}}}}))
        reader = SnapshotReader()
        snapshot, observations = await reader.read(project, {"s": session}, "s", config=config, engines=engines, title_errors={})
        json.dumps(asdict(snapshot))  # No handles, tasks or UI controls cross threads.
        self.assertEqual(snapshot.status, "running")
        self.assertEqual(snapshot.sessions[0].queued_count, 2)
        self.assertEqual(snapshot.messages[-1].text, "")
        self.assertEqual(snapshot.messages[0].role, "reasoning")
        self.assertFalse(hasattr(snapshot, "detail"))
        self.assertFalse(hasattr(snapshot, "notice"))
        session.aget_data.assert_awaited_once()
        session.run.astatus.assert_awaited_once()
        handle.aget_data.assert_awaited_once()
        session.run.aload.assert_not_awaited()
        self.assertEqual(observations[0].record, record)
        korean = present(snapshot, Language("ko", icons=NERD))
        english = present(snapshot, Language("en", icons=UNICODE))
        korean_headers = headers(korean.messages, Language("ko", icons=NERD))
        english_headers = headers(english.messages, Language("en", icons=UNICODE))
        self.assertNotEqual(korean_headers, english_headers)
        self.assertIn(NERD.reasoning, korean_headers)
        self.assertIn(UNICODE.reasoning, english_headers)
        self.assertEqual(snapshot.status, "running")
        # Other sessions don't compete for the persistence lane on every delta.
        await reader.read(project, {"s": session}, "", config=config, engines=engines, title_errors={})
        session.aget_data.assert_awaited_once()
        reader.invalidate("s")
        await reader.read(project, {"s": session}, "", config=config, engines=engines, title_errors={})
        self.assertEqual(session.aget_data.await_count, 2)
        view = LiveHubView()
        view.apply_snapshot(snapshot)
        view.composer.text = "draft"
        view.set_theme(replace(HubTheme(), icon_style="unicode"))
        self.assertEqual(view.composer.text, "draft")
        shown = headers(view.output.transcript.control.messages, view.t)
        self.assertEqual(shown, headers(present(snapshot, view.t).messages, view.t))
        self.assertIn(UNICODE.reasoning, shown)
        view.close()
        # The selected latest Run also stops rereading settled Steps. Paused
        # interactions remain mutable and must not reuse that terminal cache.
        run.status, message.status = "completed", "completed"
        status.active_runs = ()
        await reader.read(project, {"s": session}, "s", config=config, engines=engines, title_errors={})
        scans = handle.steps.alist.await_count
        await reader.read(project, {"s": session}, "s", config=config, engines=engines, title_errors={})
        self.assertEqual(handle.steps.alist.await_count, scans)
        run.status, message.status = "paused", "paused"
        # Read the Step boundary directly: interaction UI setup is independent.
        await reader.read_steps(handle, run)
        await reader.read_steps(handle, run)
        self.assertEqual(handle.steps.alist.await_count, scans + 2)
        reader.clear()
        self.assertFalse(reader.step_observations)

    async def test_snapshot_rejects_missing_or_malformed_completion_evidence(self):
        reader = SnapshotReader()
        valid = asdict(CompletionResult(reasoning_content="visible"))
        for metadata in ({}, {"completions": [{"reasoning_content": "visible"}]},
                         {"completions": [{**valid, "reasoning_content": {"thinking": "visible"}}]}):
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                await reader.run_display(None, "r", run=NS(id="r", metadata=metadata))
