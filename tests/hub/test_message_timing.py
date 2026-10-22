"""Assistant timing follows its Run, including live and missing history."""

from datetime import datetime, timezone
from dataclasses import asdict
from llm.core.results import CompletionResult
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from hub.widget.transcript.conversation import ChatMessage, render_messages
from hub.locales import Language
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.theme import HubTheme


def admitted_user(created_at, *, content="Q", run_id=None):
    from llm.core.agents import AgentBinding
    from llm.core.models import Message, MessageRole, MessageStatus, new_id
    from llm.services.runtime.requests import RequestAdmission
    message = Message(new_id(), MessageRole.USER, content,
        MessageStatus.COMMITTED if run_id else MessageStatus.QUEUED, created_at=created_at)
    admission = RequestAdmission.create(project_id="a" * 32, session_id="b" * 32,
        message_id=message.id, bindings={"fixture": AgentBinding.create("a" * 32, "fixture", {"engine": "test"})})
    if run_id is not None:
        admission = RequestAdmission.dispatch(admission, run_ids={"fixture": run_id}, history=[message])
    message.metadata["admission"] = admission
    return message


class MessageTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_uses_run_end_without_changing_request_timestamp(self):
        runtime = HubRuntime(HubConfig("unused", auto_title=False))
        run = SimpleNamespace(id="1" * 32, status="failed", started_at="2026-10-18T00:00:02+00:00",
                              ended_at="2026-10-18T00:00:08+00:00", metadata={"completions": []})
        request = admitted_user("2026-10-18T00:00:00+00:00", run_id="1" * 32)
        response = SimpleNamespace(id="a", role="assistant", content="", status="failed", run_id="1" * 32,
                                   created_at="2026-10-18T00:00:02+00:00", metadata={})
        for _ in range(2):
            messages = await runtime.snapshot_reader.messages(None, [request, response], latest=run)
            self.assertEqual(messages[0].time, request.created_at)
            self.assertEqual(messages[-1].time, run.ended_at)

    async def test_response_duration_includes_submission_and_admission_time(self):
        runtime = HubRuntime(HubConfig("unused", auto_title=False))
        run = SimpleNamespace(id="1" * 32, status="completed", started_at="2026-10-11T00:00:04+00:00",
            ended_at="2026-10-11T00:00:06+00:00", metadata={"completions": []})
        request = admitted_user("2026-10-11T00:00:00+00:00", run_id="1" * 32)
        response = SimpleNamespace(id="a", role="assistant", content="A", status="completed", run_id="1" * 32, metadata={})
        messages = await runtime.snapshot_reader.messages(None, [request, response], latest=run)
        self.assertEqual(messages[-1].elapsed_seconds, 6)
        # Run execution timing stays independent of presentation timing.
        self.assertEqual((await runtime.snapshot_reader.run_display(None, "1" * 32, run=run))[1], 2)

    async def test_live_elapsed_and_terminal_duration_exclude_queue_time(self):
        runtime = HubRuntime(HubConfig("unused", model="test/model", auto_title=False))
        run = SimpleNamespace(id="run", status="running", started_at="2026-10-04T00:01:00+00:00",
                              ended_at=None, metadata={"completions": [asdict(CompletionResult(
                                  reasoning_content="Reason", started_at="2026-10-04T00:01:00+00:00"))]})
        read = AsyncMock(return_value=run)
        session = SimpleNamespace(run=SimpleNamespace(aload=AsyncMock(
            return_value=SimpleNamespace(aget_data=read, steps=SimpleNamespace(alist=AsyncMock(return_value=[]))))))
        request = admitted_user("2026-10-04T00:00:00+00:00", content="Question")
        response = SimpleNamespace(id="assistant", role="assistant", content="Answer", status="streaming",
                                   created_at=request.created_at, run_id=run.id, metadata={})
        with patch("hub.backend.observability.snapshot.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 4, 0, 1, 2, tzinfo=timezone.utc)
            first = await runtime.snapshot_reader.messages(session, [request, response])
            self.assertEqual(first[-1].elapsed_seconds, 2)
            self.assertEqual(first[-1].time, "")
            self.assertTrue(first[0].time)
            self.assertEqual([m.role for m in first], ["user", "reasoning", "assistant"])
            self.assertEqual(first[1].text, "Reason")
            clock.now.return_value = datetime(2026, 10, 4, 0, 1, 4, tzinfo=timezone.utc)
            self.assertEqual((await runtime.snapshot_reader.messages(session, [response]))[-1].elapsed_seconds, 4)
            run.status, run.ended_at = "completed", "2026-10-04T00:01:03.250000+00:00"
            response.status = "completed"
            self.assertEqual((await runtime.snapshot_reader.messages(session, [response]))[-1].elapsed_seconds, 3.25)
            reads = read.await_count
            clock.now.return_value = datetime(2026, 10, 5, tzinfo=timezone.utc)
            self.assertEqual((await runtime.snapshot_reader.messages(session, [response]))[-1].elapsed_seconds, 3.25)
            self.assertEqual(read.await_count, reads)

    async def test_terminal_states_and_missing_history_do_not_keep_counting(self):
        for status in ("interrupted", "failed", "cancelled", "paused"):
            with self.subTest(status=status):
                runtime = HubRuntime(HubConfig("unused", model="test/model", auto_title=False))
                run = SimpleNamespace(status=status, started_at="2026-10-04T00:00:00+00:00",
                                      ended_at="2026-10-04T00:00:12.500000+00:00", metadata={"completions": []})
                read = AsyncMock(return_value=run)
                session = SimpleNamespace(run=SimpleNamespace(aload=AsyncMock(
                    return_value=SimpleNamespace(aget_data=read))))
                self.assertEqual(await runtime.snapshot_reader.run_display(session, "run"), ("", 12.5))
                run.ended_at = None
                self.assertEqual(await runtime.snapshot_reader.run_display(session, "no-end"), ("", None))
                session.run.aload.side_effect = FileNotFoundError
                self.assertEqual(await runtime.snapshot_reader.run_display(session, "removed"), ("", None))
                message = SimpleNamespace(id="clone", role="assistant", content="Copied", status="completed",
                                          created_at="2026-10-04T00:00:00+00:00", run_id=None, metadata={})
                result = (await runtime.snapshot_reader.messages(session, [message]))[0]
                self.assertIsNone(result.elapsed_seconds)
                self.assertEqual(result.time, "")


class MessageTimingRenderTests(unittest.TestCase):
    def test_assistant_header_replaces_date_in_both_languages(self):
        for language, expected in (("ko", "소요 1분 2초"), ("en", "Elapsed 1m 2s")):
            messages = (ChatMessage("assistant", "Reply", "2026-10-04 12:00:00",
                                    status="completed", elapsed_seconds=62.5),)
            lines = render_messages(messages, 100, HubTheme(), Language(language))
            header = "".join(text for _, text in lines[0])
            self.assertIn(expected, header)
            self.assertNotIn("2026-10-04", header)
        ko = Language("ko")
        self.assertEqual(ko.elapsed(0), "소요 0초")
        self.assertEqual(ko.elapsed(3600), "소요 1시간 0분 0초")
        self.assertEqual(ko.elapsed(None), "소요 시간 알 수 없음")
        self.assertEqual(ko.status("queued"), "◷ 예약")
        self.assertEqual(ko.status("idle"), "○ 유휴")
