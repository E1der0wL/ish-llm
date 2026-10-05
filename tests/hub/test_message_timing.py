"""Assistant timing follows its Run, including live and missing history."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from hub.ui.chat.conversation import ChatMessage, render_messages
from hub.locales import Language
from hub.backend.runtime import HubConfig, HubRuntime
from hub.config.theme import HubTheme


class MessageTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_elapsed_and_terminal_duration_exclude_queue_time(self):
        runtime = HubRuntime(HubConfig("unused", "loop", "test/model", auto_title=False))
        run = SimpleNamespace(id="run", status="running", started_at="2026-10-04T00:01:00+00:00",
                              ended_at=None, metadata={"completions": [{"reasoning_content": "Reason"}]})
        read = AsyncMock(return_value=run)
        session = SimpleNamespace(run=SimpleNamespace(aload=AsyncMock(
            return_value=SimpleNamespace(aget_data=read))))
        request = SimpleNamespace(id="user", role="user", content="Question", status="queued",
                                  created_at="2026-10-04T00:00:00+00:00", run_id=None, metadata={})
        response = SimpleNamespace(id="assistant", role="assistant", content="Answer", status="streaming",
                                   created_at=request.created_at, run_id=run.id, metadata={})
        with patch("hub.backend.runtime.datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 4, 0, 1, 2, tzinfo=timezone.utc)
            first = await runtime._messages(session, [request, response])
            self.assertEqual(first[-1].elapsed_seconds, 2)
            self.assertEqual(first[-1].time, "")
            self.assertTrue(first[0].time)
            self.assertEqual(first[1].role, "reasoning")
            clock.now.return_value = datetime(2026, 10, 4, 0, 1, 4, tzinfo=timezone.utc)
            self.assertEqual((await runtime._messages(session, [response]))[-1].elapsed_seconds, 4)
            run.status, run.ended_at = "completed", "2026-10-04T00:01:03.250000+00:00"
            response.status = "completed"
            self.assertEqual((await runtime._messages(session, [response]))[-1].elapsed_seconds, 3.25)
            reads = read.await_count
            clock.now.return_value = datetime(2026, 10, 5, tzinfo=timezone.utc)
            self.assertEqual((await runtime._messages(session, [response]))[-1].elapsed_seconds, 3.25)
            self.assertEqual(read.await_count, reads)

    async def test_terminal_states_and_missing_history_do_not_keep_counting(self):
        for status in ("interrupted", "failed", "cancelled", "paused"):
            with self.subTest(status=status):
                runtime = HubRuntime(HubConfig("unused", "loop", "test/model", auto_title=False))
                run = SimpleNamespace(status=status, started_at="2026-10-04T00:00:00+00:00",
                                      ended_at="2026-10-04T00:00:12.500000+00:00", metadata={})
                read = AsyncMock(return_value=run)
                session = SimpleNamespace(run=SimpleNamespace(aload=AsyncMock(
                    return_value=SimpleNamespace(aget_data=read))))
                self.assertEqual(await runtime._run_display(session, "run"), ("", 12.5))
                run.ended_at = None
                self.assertEqual(await runtime._run_display(session, "no-end"), ("", None))
                session.run.aload.side_effect = FileNotFoundError
                self.assertEqual(await runtime._run_display(session, "removed"), ("", None))
                message = SimpleNamespace(id="clone", role="assistant", content="Copied", status="completed",
                                          created_at="2026-10-04T00:00:00+00:00", run_id=None, metadata={})
                result = (await runtime._messages(session, [message]))[0]
                self.assertIsNone(result.elapsed_seconds)
                self.assertEqual(result.time, "")


class MessageTimingRenderTests(unittest.TestCase):
    def test_assistant_header_replaces_date_in_both_languages(self):
        for language, expected in (("ko", "소요 1분 2.5초"), ("en", "Elapsed 1m 2.5s")):
            messages = (ChatMessage("assistant", "Reply", "2026-10-04 12:00:00",
                                    status="completed", elapsed_seconds=62.5),)
            lines = render_messages(messages, 100, HubTheme(), Language(language))
            header = "".join(text for _, text in lines[0])
            self.assertIn(expected, header)
            self.assertNotIn("2026-10-04", header)
        ko = Language("ko")
        self.assertEqual(ko.elapsed(0), "소요 0.0초")
        self.assertEqual(ko.elapsed(3600), "소요 1시간 0분 0.0초")
        self.assertEqual(ko.elapsed(None), "소요 시간 알 수 없음")
        self.assertEqual(ko.status("queued"), "◷ 예약")
        self.assertEqual(ko.status("idle"), "○ 유휴")
