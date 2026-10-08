"""Backend reads primitive observations once; UI chooses language and glyphs."""

from dataclasses import asdict, replace
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock

from hub.asset.icon import NERD, UNICODE
from hub.backend.snapshot import SnapshotReader
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.ui.live import LiveHubView
from hub.ui.presentation import present


class SnapshotProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_only_snapshot_has_no_localized_display_and_reuses_observations(self):
        config = NS(engine="loop", model=None, file_root=None, user_profile=NS(display_name="Account"))
        record = NS(id="s", title="Session", config={}, metadata={"hub_auto_title": True})
        run = NS(id="r", session_id="s", status="running", engine="loop", started_at="2026-10-01T00:00:00+00:00",
                 ended_at=None, error=None, metadata={"completions": [{"reasoning_content": "Summary", "model": "test/model"}]})
        handle = NS(aget_data=AsyncMock(return_value=run), steps=NS(alist=AsyncMock(return_value=[NS(name="LLM", status="running")])))
        status = NS(status="running", queued_count=2, active_run_id="r")
        message = NS(id="m", role="assistant", content="", status="streaming", created_at=run.started_at, run_id="r", metadata={})
        session = NS(aget_data=AsyncMock(return_value=record), aconversation=AsyncMock(return_value=[message]),
                     run=NS(astatus=AsyncMock(return_value=status), alist=AsyncMock(return_value=[handle]), aload=AsyncMock()))
        project = NS(aget_data=AsyncMock(return_value=NS(id="p", title="Project", config={}, components=["tools"], conversation_storage="file")),
                     paths=NS(root="/workspace"))
        engines = NS(names=lambda: ("loop",), get=lambda _: NS(resolve_config=lambda *a, **kw: {"values": {"config": {"completion": {"model": "test/model"}}}}))
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
        self.assertNotEqual(korean.activity, english.activity)
        self.assertIn(NERD.statuses["running"], korean.activity)
        self.assertIn(UNICODE.statuses["running"], english.activity)
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
        self.assertNotIn(NERD.statuses["running"], view.activity)
        self.assertIn(UNICODE.statuses["running"], view.activity)
