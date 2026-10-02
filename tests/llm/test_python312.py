"""Python 3.12.14의 표준 API, 취소, 영속 모델 및 경로 안전성을 검증한다."""

import asyncio
import json
import sys
import tempfile
import tomllib
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import get_type_hints

from llm.core.results import EngineDelta, EngineOutput
from contextlib import aclosing
from asyncio import timeout
from llm.core.models import (
    Message, MessageRole, MessageStatus, Project, ProjectConfig, Run, RunStatus,
    Step, StepStatus, Session, SessionStatus,
)
from llm.core.paths import ProjectPaths
from llm.core.results import CompletionResult, ExecutionResult
from llm.engines.base import BaseEngine, EngineContext, EngineEvent, EngineEventType
from llm.engines.loop import LoopEngine
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.infrastructure.storage import record
from llm.services.lifecycle.sessions import SessionManager, SessionRuntime


class Python312Tests(unittest.TestCase):
    def test_validation_interpreter_is_linux_python31214(self) -> None:
        self.assertEqual(sys.platform, "linux")
        self.assertEqual(sys.version_info[:3], (3, 12, 14))

    def test_repository_version_pins_agree(self) -> None:
        root = Path(__file__).resolve().parents[2]
        self.assertEqual((root / ".python-version").read_text().strip(), "3.12.14")
        project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["requires-python"], "==3.12.14")
        self.assertFalse(any("python_version" in item for item in project["dependencies"]))

    def test_enum_string_formatting_and_json_remain_persisted_values(self) -> None:
        for enum in (MessageRole, MessageStatus, SessionStatus, RunStatus, StepStatus, EngineEventType):
            for member in enum:
                with self.subTest(enum=enum.__name__, value=member.value):
                    self.assertEqual(str(member), member.value)
                    self.assertEqual(f"{member}", member.value)
                    self.assertEqual(format(member, ">16"), format(member.value, ">16"))
                    self.assertEqual(json.loads(json.dumps(member)), member.value)
                    self.assertIs(enum(member.value), member)

    def test_public_model_type_hints_resolve(self) -> None:
        for model in (Project, Session, Message, Run, Step, SessionRuntime,
                      EngineEvent, EngineContext, CompletionResult, ExecutionResult, EngineDelta, EngineOutput):
            with self.subTest(model=model.__name__):
                hints = get_type_hints(model)
                self.assertTrue({field.name for field in fields(model)} <= hints.keys())
        self.assertTrue(get_type_hints(SessionManager.create))
        for method in (BaseEngine.__init__, BaseEngine.step, BaseEngine.stream_completion,
                       BaseEngine.delta_event, BaseEngine.output_event, BaseEngine.step_completed_event,
                       LoopEngine.__init__):
            self.assertTrue(get_type_hints(method))

    def test_dataclass_defaults_frozen_copy_and_field_only_persistence(self) -> None:
        config = ProjectConfig()
        clone = deepcopy(config)
        clone.completion["model"] = "different"
        self.assertEqual(config.completion, {})
        event = EngineEvent(EngineEventType.TEXT_DELTA, delta=EngineDelta("test-output", "hello"))
        self.assertEqual(deepcopy(event), event)
        with self.assertRaises(FrozenInstanceError):
            event.delta.text = "changed"
        self.assertIsInstance(config, dict)
        config.new_key = {"enabled": True}
        self.assertEqual(config.to_dict()["new_key"], {"enabled": True})

    def test_real_symlink_cannot_be_followed_by_permanent_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sessions = SessionManager()
            projects = ProjectManager(ProjectRepository(root / "projects"), sessions)
            project = projects.create("test")
            session = sessions.create(project, "test")
            outside = root / "outside"
            outside.mkdir()
            marker = outside / "keep.txt"
            marker.write_text("keep", encoding="utf-8")
            link = session.paths.root / "redirect"
            link.symlink_to(outside, target_is_directory=True)
            try:
                self.assertTrue(link.is_symlink())
                with self.assertRaises(ValueError):
                    sessions.delete(session, permanent=True)
                with self.assertRaises(ValueError):
                    projects.delete(project, permanent=True)
                self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
                self.assertTrue((session.paths.root / "session.json").exists())
            finally:
                # Remove only the symlink entry, never recursively its target.
                link.unlink()


class AsyncRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_expires_and_session_can_continue(self) -> None:
        with self.assertRaises(asyncio.TimeoutError):
            async with timeout(0.02):
                await asyncio.Event().wait()
        async with timeout(1):
            await asyncio.sleep(0)

    async def test_external_cancellation_stays_cancelled_and_closes_stream(self) -> None:
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def stream():
            try:
                entered.set()
                yield "partial"
                await asyncio.Event().wait()
            finally:
                closed.set()

        async def consume():
            async with timeout(10):
                async with aclosing(stream()) as chunks:
                    async for _ in chunks:
                        pass

        worker_future = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), 1)
        worker_future.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker_future
        self.assertTrue(worker_future.cancelled())
        self.assertTrue(closed.is_set())

    async def test_nested_timeout_only_expires_inner_scope(self) -> None:
        async with timeout(1):
            with self.assertRaises(asyncio.TimeoutError):
                async with timeout(0.02):
                    await asyncio.Event().wait()
            await asyncio.sleep(0)

    async def test_body_errors_are_not_converted_into_timeouts(self) -> None:
        with self.assertRaisesRegex(ValueError, "example"):
            async with timeout(1):
                raise ValueError("example")

    async def test_early_stream_close_runs_finally(self) -> None:
        closed = []

        async def stream():
            try:
                yield 1
                yield 2
            finally:
                closed.append(True)

        async with aclosing(stream()) as chunks:
            self.assertEqual(await chunks.__anext__(), 1)
        self.assertEqual(closed, [True])
