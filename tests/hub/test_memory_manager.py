"""Memory manager uses scoped, revision-checked CRUD without internal file access."""

import json
import tempfile
import unittest

from hub.backend.runtime import HubConfig, HubRuntime
from hub.widget.forms.definition_manager import initial_definition
from llm.components.memory.component import MemoryConflictError


class MemoryManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_project_and_session_records_create_edit_conflict_and_soft_delete(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = HubRuntime(HubConfig(root, auto_title=False))
            await runtime.start()
            try:
                pid = runtime.project.id
                sid = await runtime.new_session()
                data = await runtime.project.components.aget("memory")
                async def manage(action, argument="", version=None):
                    return await runtime.manage_records(pid, "memory", action, argument, version)
                values = {**initial_definition("memory"), "content": "Project fact"}
                entries = await manage("create", {"name": "manual", "text": json.dumps(values)})
                self.assertEqual([item["name"] for item in entries], ["manual"])
                await data.acreate({"content": "Session fact", "scope": "session", "session_id": sid,
                                    "status": "candidate"}, identifier="session-note")
                self.assertEqual({item["name"] for item in await manage("list")}, {"manual", "session-note"})
                record = await manage("read", "session-note")
                edited = json.loads(record["text"])
                self.assertNotIn("revision", edited)
                self.assertNotIn("source", edited)
                edited["content"] = "Edited session fact"
                await manage("save", {"name": "session-note", "text": json.dumps(edited)}, record["version"])
                with self.assertRaises(MemoryConflictError):
                    await manage("save", {"name": "session-note", "text": json.dumps(edited)}, record["version"])
                current = await manage("read", "session-note")
                self.assertEqual(json.loads(current["text"])["content"], "Edited session fact")
                await manage("delete", "session-note", current["version"])
                self.assertEqual([item["name"] for item in await manage("list")], ["manual"])
                self.assertTrue((await data.aload("session-note", include_deleted=True, session_id=sid))["deleted"])
            finally:
                await runtime.close()
