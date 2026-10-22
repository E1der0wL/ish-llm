"""Application rotation preserves domain data and the Backend's safe file contract."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hub.backend.observability.logging import RotatingLogSink
from hub.backend.runtime import HubConfig, create_backend
from llm.services.infrastructure.logging import DomainLogger, LogConfig


class LogRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.logs = self.root / "logs"

    def test_explicit_rotation_preserves_original_bounded_file_and_handler_assertions(self):
        root_handlers = list(logging.getLogger().handlers)
        logger = DomainLogger(sink=RotatingLogSink(max_bytes=220, backup_count=3))
        marker = self.root / "original.json"
        marker.write_text('"original domain source"')
        for _ in range(12):
            logger.write(self.logs, "session.loaded", entity_id="a" * 32)
        files = list(self.logs.glob("service.log*"))
        self.assertEqual(len(files), 4)
        self.assertEqual(logging.getLogger().handlers, root_handlers)
        self.assertTrue(all(json.loads(line)["event"] == "session.loaded"
                            for path in files for line in path.read_text().splitlines()))
        self.assertEqual(marker.read_text(), '"original domain source"')

    def test_rotation_reuses_allowlist_and_preserves_read_switches(self):
        logger = DomainLogger(LogConfig(log_reads=False), sink=RotatingLogSink(220, 3))
        logger.write(self.logs, "session.loaded", entity_id="a" * 32)
        self.assertFalse(self.logs.exists())
        for _ in range(12):
            logger.write(self.logs, "session.saved", entity_id="PRIVATE", related_id="b" * 32,
                         status="PRIVATE", count=-1, permanent="PRIVATE")
        contents = [path.read_text() for path in self.logs.glob("service.log*")]
        self.assertTrue(contents)
        self.assertNotIn("PRIVATE", "".join(contents))
        for content in contents:
            for line in content.splitlines():
                self.assertEqual(set(json.loads(line)), {"time", "event", "related_id"})
        disabled = DomainLogger(LogConfig(enabled=False), sink=RotatingLogSink(220, 3))
        before = {p.name: p.read_bytes() for p in self.logs.iterdir()}
        disabled.write(self.logs, "session.saved")
        self.assertEqual({p.name: p.read_bytes() for p in self.logs.iterdir()}, before)

    def test_linked_backup_is_rejected_before_any_rotation_or_external_write(self):
        self.logs.mkdir()
        primary = self.logs / "service.log"
        primary.write_text("original\n")
        external = self.root / "external"
        external.write_text("external original\n")
        (self.logs / "service.log.2").symlink_to(external)
        logger = DomainLogger(sink=RotatingLogSink(1, 3))
        with self.assertWarnsRegex(RuntimeWarning, "could not write an operational log"):
            logger.write(self.logs, "session.saved")
        self.assertEqual(primary.read_text(), "original\n")
        self.assertEqual(external.read_text(), "external original\n")
        self.assertEqual(sorted(p.name for p in self.logs.iterdir()), ["service.log", "service.log.2"])

    def test_deleted_domain_is_not_recreated(self):
        DomainLogger(sink=RotatingLogSink(220, 3)).write(self.root / "absent" / "logs", "session.saved")
        self.assertFalse((self.root / "absent").exists())

    def test_parallel_calls_share_one_sink_without_duplicate_or_lost_records(self):
        logger = DomainLogger(sink=RotatingLogSink(1_000_000, 3))
        with ThreadPoolExecutor(max_workers=4) as workers:
            list(workers.map(lambda index: logger.write(self.logs, "session.saved", count=index), range(40)))
        rows = [json.loads(line) for line in (self.logs / "service.log").read_text().splitlines()]
        self.assertEqual(sorted(row["count"] for row in rows), list(range(40)))

    def test_invalid_or_missing_policy_is_rejected_without_touching_files(self):
        for value in (None, False, True, 0, -1, 1.5, "3"):
            for values in ((value, 3), (220, value)):
                with self.assertRaises(ValueError):
                    RotatingLogSink(*values)
        with self.assertRaises(TypeError):
            RotatingLogSink()
        with self.assertRaises(TypeError):
            HubConfig(self.root, log_sink=False)
        self.assertFalse(self.logs.exists())


class HubLogRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_injects_explicit_sink_and_default_remains_append_only(self):
        with tempfile.TemporaryDirectory() as root:
            for name, sink, expected in (("default", None, 1), ("rotation", RotatingLogSink(220, 3), 4)):
                async with create_backend(HubConfig(Path(root) / name, auto_title=False, log_sink=sink)) as app:
                    project = await app.projects.acreate("original project")
                    for index in range(12):
                        await project.sessions.acreate(str(index), agent_ids=sorted(await project.agents.alist()))
                    self.assertEqual(len(list(project.paths.logs.glob("service.log*"))), expected)
                    self.assertEqual(len(await project.sessions.alist()), 12)
                    self.assertEqual((await project.aget_data()).title, "original project")

    async def test_rotation_failure_after_real_rename_does_not_undo_committed_domain_save(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            async with create_backend(HubConfig(root, auto_title=False, log_sink=RotatingLogSink(8192, 3))) as app:
                project = await app.projects.acreate("before")
                primary = project.paths.logs / "service.log"
                primary.parent.mkdir(exist_ok=True)
                primary.write_text("original diagnostic\n" * 600)
                rename = os.rename
                reached = []
                def fail_after_rotation(source, target):
                    result = rename(source, target)
                    if str(source) == str(primary):
                        reached.append((str(source), str(target)))
                        raise OSError("PRIVATE injected rotation failure")
                    return result
                with patch("logging.handlers.os.rename", side_effect=fail_after_rotation):
                    with self.assertWarnsRegex(RuntimeWarning, "could not write an operational log") as warning:
                        await project.asave(title="committed")
                self.assertEqual(len(reached), 1)
                self.assertNotIn("PRIVATE", str(warning.warning))
                self.assertEqual((await project.aget_data()).title, "committed")
                self.assertEqual((primary.parent / "service.log.1").read_text(), "original diagnostic\n" * 600)
