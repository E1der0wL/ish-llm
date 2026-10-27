"""Read authority and large-data contracts; limits are explicit test inputs."""

from pathlib import Path
import os
import tempfile
import unittest
import asyncio
import io
import json
import sys
from unittest.mock import patch

from llm.components.tools.builtin import BuiltinTools
from llm.components.tools.builtin.files import FileTools
from llm.components.tools.builtin.path_access import PathAccess


class PathAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_grant_invalidates_durable_approval_before_file_effect(self):
        from llm.llm import LargeLanguageModel, LoopEngine
        from llm.components.tools.builtin import BuiltinToolComponent
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from tests.llm.test_loop import ScriptedCompletion, chunk, call
        from tests.llm.configuration_fixtures import configure_engine
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); work = root / "work"; work.mkdir()
            outside = root / "outside"; outside.mkdir()
            arguments = {"path": str(outside / "effect"), "content": "once"}
            model = ScriptedCompletion([chunk(calls=[call(json.dumps(arguments), name="file_create")], finish="tool_calls")])
            async with BuiltinTools(work) as toolkit:
                component = BuiltinToolComponent(toolkit, name="computer")
                async with LargeLanguageModel(root / "backend", components=[component],
                    engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}) as app:
                    project = await app.projects.acreate(components=["computer"])
                    data = project.components["computer"]
                    configuration = {"config": {"enabled": ["file_create"]}, "policy": {
                        "write_access": {"roots": [str(outside)], "follow_symlinks": False}}}
                    await data.aconfigure(configuration)
                    await configure_engine(project, "loop")
                    await project.agents.acreate({"engine": "loop", "completion": {"model": "test/model"},
                        "tools": ["file_create"]}, identifier="writer")
                    session = await project.sessions.acreate(agent_ids=["writer"])
                    run = (await (await session.run.submit("create", recipients=["writer"])).wait())[0]
                    self.assertEqual(run.data.status, "paused", run.data.error)
                    request, = await run.ainteractions()
                    await run.arespond(request.respond("approve"))
                    self.assertFalse((outside / "effect").exists())
                    configuration["policy"]["write_access"]["follow_symlinks"] = True
                    await data.aconfigure(configuration)
                    from llm.services.runtime.runs import RunRequestError
                    with self.assertRaisesRegex(RunRequestError, "configuration changed"):
                        await session.run.resume(run.id, agent_id="writer")
                    self.assertFalse((outside / "effect").exists())

    async def test_write_grant_supports_external_crud_without_granting_read_or_cwd(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); work = root / "work"; outside = root / "outside"
            work.mkdir(); outside.mkdir(); (work / "link").symlink_to(outside, target_is_directory=True)
            grant = {"roots": [str(outside)], "follow_symlinks": True}
            async with BuiltinTools(work, write_access=grant, allow_commands=True, shell=["/bin/sh", "-c"]) as toolkit:
                async def invoke(name, **args):
                    tool, args = toolkit.registry.prepare(name, args)
                    return await tool.handler(args)
                created = await invoke("file_create", path="link/nested/a", content="before")
                with self.assertRaises(ValueError):
                    await invoke("file_read", path="link/nested/a")
                with self.assertRaises(ValueError):
                    await invoke("shell_execute", command="pwd", cwd=str(outside))
                with self.assertRaises(FileExistsError):
                    await invoke("file_create", path="link/nested/a", content="overwrite")
                with self.assertRaises(ValueError):
                    await invoke("file_patch", path="link/nested/a", expected_sha256="0" * 64, old_text="before", new_text="after")
                patched = await invoke("file_patch", path="link/nested/a", expected_sha256=created["sha256"], old_text="before", new_text="after")
                moved = await invoke("file_move", path="link/nested/a", destination=str(outside / "b"), expected_sha256=patched["sha256"])
                deleted = await invoke("file_delete", path=str(outside / "b"), expected_sha256=moved["sha256"])
                self.assertFalse((outside / "b").exists())
                await invoke("file_restore", path="link/restored", trash_id=deleted["trash_id"])
                self.assertEqual((outside / "restored").read_text(), "after")
                for path in ("forbidden", str(root / "forbidden")):
                    with self.assertRaises(ValueError):
                        await invoke("file_create", path=path, content="no")

    async def test_write_link_rules_and_parent_swap_do_not_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); work = root / "work"; outside = root / "outside"
            work.mkdir(); outside.mkdir(); (work / "link").symlink_to(outside, target_is_directory=True)
            missing = root / "not-granted" / "granted"
            files = FileTools(work, write_access={"roots": [str(missing)], "follow_symlinks": False})
            with self.assertRaises(FileNotFoundError):
                files.file_create({"path": str(missing / "new"), "content": "no"})
            self.assertFalse(missing.parent.exists())
            for grant in ({"roots": [str(outside)], "follow_symlinks": False},
                          {"roots": [str(work)], "follow_symlinks": True}):
                with self.assertRaises(ValueError):
                    FileTools(work, write_access=grant).file_create({"path": "link/x", "content": "no"})
            (work / "safe").mkdir()
            original = PathAccess.resolve
            def swapped(access, cwd, value, **kwargs):
                path = original(access, cwd, value, **kwargs)
                (work / "safe").rmdir()
                (work / "safe").symlink_to(outside, target_is_directory=True)
                return path
            files = FileTools(work)
            with patch.object(PathAccess, "resolve", swapped), self.assertRaises(OSError):
                files.file_create({"path": "safe/x", "content": "no"})
            self.assertFalse((outside / "x").exists())

    async def test_external_cwd_and_adapter_arguments_are_application_selected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); work = root / "work"; outside = root / "outside"
            work.mkdir(); outside.mkdir(); (work / "link").symlink_to(outside, target_is_directory=True)
            async def adapter(args):
                return args
            async with BuiltinTools(work, cwd_access={"roots": [str(outside)], "follow_symlinks": True},
                allow_commands=True, shell=["/bin/sh", "-c"],
                adapters={"web_fetch": adapter, "context_expand": adapter}) as toolkit:
                for name, args in (("shell_execute", {"command": "pwd", "cwd": "link"}),
                                   ("process_start", {"argv": ["pwd"], "cwd": str(outside)})):
                    tool, args = toolkit.registry.prepare(name, args)
                    result = await tool.handler(args)
                    if name == "process_start":
                        await toolkit.processes.sessions[result["process_id"]].task
                        result = toolkit.processes.snapshot(result["process_id"])
                    self.assertEqual(result["stdout"].strip(), str(outside))
                for name, args in (("web_fetch", {"url": "file:///manual"}),
                                   ("context_expand", {"reference": "x", "scope": "custom-scope"})):
                    tool, supplied = toolkit.registry.prepare(name, args)
                    self.assertEqual(await tool.handler(supplied), args)

    async def test_project_grants_preserve_host_wrapper_and_change_approval_binding(self):
        from llm.llm import LargeLanguageModel
        from llm.components.tools.builtin import BuiltinToolComponent
        from llm.components.tools import ToolRegistry
        from dataclasses import replace
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); work = root / "work"; outside = root / "outside"
            work.mkdir(); outside.mkdir()
            async with BuiltinTools(work) as toolkit:
                calls = []
                original = toolkit.registry.get("file_create")
                async def host(args):
                    calls.append(args["path"])
                    return await original.handler(args)
                toolkit.registry = ToolRegistry([replace(original, handler=host)])
                component = BuiltinToolComponent(toolkit, name="computer")
                async with LargeLanguageModel(root / "backend", components=[component]) as app:
                    project = await app.projects.acreate(components=["computer"])
                    data = project.components["computer"]
                    await data.aconfigure({"config": {"enabled": ["file_create"]}})
                    before = component.resolve(project.data, "tools").get("file_create")
                    await data.aconfigure({"config": {"enabled": ["file_create"]}, "policy": {
                        "write_access": {"roots": [str(outside)], "follow_symlinks": False}}})
                    after = component.resolve(project.data, "tools").get("file_create")
                    self.assertNotEqual(before.contract.revision, after.contract.revision)
                    self.assertTrue(after.contract.approval_required)
                    await after.handler({"path": str(outside / "new"), "content": "ok"})
                    self.assertEqual(calls, [str(outside / "new")])
                    self.assertEqual((outside / "new").read_text(), "ok")

    async def test_external_and_link_reads_do_not_grant_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            work, outside = base / "work", base / "docs"
            work.mkdir(); outside.mkdir()
            (outside / "manual").write_text("external 한글")
            (work / "docs").symlink_to(outside, target_is_directory=True)
            grant = {"roots": [str(work), str(outside)], "follow_symlinks": True}
            async with BuiltinTools(work, read_access=grant) as tools:
                for path in (str(outside / "manual"), "docs/manual", "../docs/manual"):
                    tool, args = tools.registry.prepare("file_read", {"path": path})
                    self.assertEqual((await tool.handler(args))["text"], "external 한글")
                tool, args = tools.registry.prepare("file_create", {"path": "docs/new", "content": "bad"})
                with self.assertRaises(ValueError):
                    await tool.handler(args)
                self.assertFalse((outside / "new").exists())
            files = FileTools(work, read_access={"roots": [str(work)], "follow_symlinks": True})
            with self.assertRaises(ValueError):
                files.file_read({"path": "docs/manual"})
            files = FileTools(work, read_access={"roots": [str(outside)], "follow_symlinks": False})
            with self.assertRaises(ValueError):
                files.file_read({"path": "docs/manual"})

    async def test_cycles_and_changed_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "text").write_text("original")
            (root / "cycle").symlink_to(root, target_is_directory=True)
            files = FileTools(root, read_access={"roots": [str(root)], "follow_symlinks": True})
            result = files.file_list({"recursive": True})
            self.assertEqual(len(result["entries"]), 2)
            (root / ".llm-trash").mkdir()
            (root / ".llm-trash/content").write_text("reserved")
            (root / "alias").symlink_to(root / ".llm-trash/content")
            with self.assertRaisesRegex(ValueError, "reserved"):
                files.file_read({"path": "alias"})
            original = PathAccess.resolve
            def replaced(access, cwd, path):
                resolved = original(access, cwd, path)
                (root / "text").unlink()
                (root / "text").symlink_to("/etc/passwd")
                return resolved
            with patch.object(PathAccess, "resolve", replaced), self.assertRaises(OSError):
                files.file_read({"path": "text"})

    async def test_policy_binding_and_missing_fields(self):
        with tempfile.TemporaryDirectory() as root:
            async with BuiltinTools(root) as tools:
                original = tools.binding_revision(tools.READ_CONTRACT)
                self.assertNotEqual(original, tools.binding_revision(tools.READ_CONTRACT,
                    read_access={"roots": [], "follow_symlinks": False}))
                self.assertEqual(tools.files.file_list({"recursive": True})["entries"], [])
            for value in ({}, {"roots": []}, {"roots": [], "follow_symlinks": False, "extra": True}):
                with self.subTest(value=value), self.assertRaises(Exception):
                    PathAccess.read(value)

    async def test_project_read_grant_changes_binding_without_expanding_mutation(self):
        from llm.llm import LargeLanguageModel
        from llm.components.tools.builtin import BuiltinToolComponent
        from llm.components.tools import ToolRegistry
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); work = root / "work"; work.mkdir()
            outside = root / "outside"; outside.mkdir(); (outside / "file").write_text("reference")
            async with BuiltinTools(work) as toolkit:
                from dataclasses import replace
                observed = []
                original_tool = toolkit.registry.get("file_read")
                async def host_read(args):
                    observed.append(args["path"])
                    return await original_tool.handler(args)
                toolkit.registry = ToolRegistry(tuple(replace(toolkit.registry.get(name), handler=host_read)
                    if name == "file_read" else toolkit.registry.get(name) for name in toolkit.registry.names()))
                component = BuiltinToolComponent(toolkit, name="computer")
                component.validate_config({})
                with self.assertRaises(ValueError):
                    component.validate_config({"config": {"output": {"component": "data", "identifier": "x"}}})
                async with LargeLanguageModel(root / "backend", components=[component]) as app:
                    project = await app.projects.acreate(components=["computer"])
                    data = project.components["computer"]
                    await data.aconfigure({"config": {"enabled": ["file_read", "file_create"]}})
                    original = component.resolve(project.data, ToolRegistry.CAPABILITY)
                    with self.assertRaises(ValueError):
                        await original.get("file_read").handler({"path": str(outside / "file")})
                    await data.aconfigure({"config": {"enabled": ["file_read", "file_create"]},
                        "policy": {"read_access": {"roots": [str(outside)], "follow_symlinks": False}}})
                    granted = component.resolve(project.data, ToolRegistry.CAPABILITY)
                    self.assertNotEqual(original.get("file_read").contract, granted.get("file_read").contract)
                    self.assertEqual((await granted.get("file_read").handler({"path": str(outside / "file")}))["text"], "reference")
                    self.assertEqual(observed, [str(outside / "file"), str(outside / "file")])
                    with self.assertRaises(ValueError):
                        await granted.get("file_create").handler({"path": str(outside / "new"), "content": "no"})
                    self.assertFalse((outside / "new").exists())


class StreamingFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_output_limit_is_shared_by_both_async_readers(self):
        with tempfile.TemporaryDirectory() as root:
            async with BuiltinTools(root, allow_commands=True, shell=["/bin/sh", "-c"]) as toolkit:
                result = await toolkit.processes.execute({"argv": [sys.executable, "-c",
                    "import os; os.write(1,b'x'*50000); os.write(2,b'y'*50000)"], "max_output_bytes": 70001})
                self.assertEqual(len(result["stdout"]) + len(result["stderr"]), 70001)
                self.assertTrue(result["truncated"])

    async def test_single_long_line_character_page_and_search(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "large"
            with path.open("wb") as output:
                for _ in range(100):
                    output.write(("한글🙂" * 1000).encode())
                output.write(b"needle\nlast\n")
            files = FileTools(Path(root))
            with patch.object(files, "read_bytes", side_effect=AssertionError("unbounded read")):
                page = files.file_read({"path": "large", "offset": 299997, "max_chars": 9})
                self.assertEqual(page["text"], "한글🙂needle")
                self.assertEqual(page["next_offset"], 300006)
                result = files.file_search({"query": "needle", "case_sensitive": True,
                    "recursive": True, "limit": 1, "max_chars": 12})
                self.assertEqual(len(result["matches"][0]["text"]), 12)
                self.assertTrue(result["line_previews"])
                self.assertEqual(files.file_read({"path": "large", "tail_lines": 1})["text"], "last\n")

    async def test_stream_write_character_read_and_rollback(self):
        from llm.llm import LargeLanguageModel, DataComponent
        class Reader:
            def __init__(self):
                self.parts = 32
            def read(self, count):
                if not 0 < count <= 1024 * 1024:
                    raise AssertionError("unbounded read")
                if not self.parts:
                    return b""
                self.parts -= 1
                return ("한글🙂" * 1000).encode()
        with tempfile.TemporaryDirectory() as root:
            async with LargeLanguageModel(root, components=[DataComponent()]) as app:
                project = await app.projects.acreate(components=["data"])
                data = project.components["data"]
                await data.acreate({}, identifier="source")
                before = await data.asnapshot("source")
                after = await data.awrite_stream("source", "large.txt", Reader(), expected_revision=before["version"])
                self.assertEqual(await data.aread_text("source", "large.txt", expected_revision=after["version"],
                    offset=2999, limit=7), "🙂한글🙂한글🙂")
                class Broken(Reader):
                    def read(self, count):
                        if self.parts == 31:
                            raise OSError("disk/source failed")
                        return super().read(count)
                with self.assertRaises(OSError):
                    await data.awrite_stream("source", "large.txt", Broken(), expected_revision=after["version"])
                self.assertEqual(await data.asnapshot("source"), after)

    async def test_summary_reads_only_explicit_fragments(self):
        from llm.components.summarization import SummarySource, SummaryResult, SummaryAnswer, summarize_fragments
        calls, reads, progress = [], [], []
        async def read(offset, count):
            self.assertLessEqual(count, 17)
            reads.append((offset, count))
            return "x" * count
        async def model(prompt, payload, answer):
            calls.append(payload["text"])
            SummaryAnswer.store(answer, "summary")
            if False:
                yield
        async def save(value):
            progress.append(value.offset)
        source = SummarySource(100, "pinned-revision", 17, read)
        result = SummaryResult()
        async for _ in summarize_fragments(source, offset=0, previous_summary="", prompt="summary",
            max_input_size=50, max_summary_chars=20, max_calls=2,
            build_payload=lambda fragment, start, total, previous: {"text": fragment},
            measure=lambda payload: len(json.dumps(payload)), invoke=model, result=result, progress=save):
            pass
        self.assertFalse(result.complete)
        self.assertEqual(progress, [17, 34])
        async for _ in summarize_fragments(source, offset=result.offset, previous_summary=result.text, prompt="summary",
            max_input_size=50, max_summary_chars=20, max_calls=10,
            build_payload=lambda fragment, start, total, previous: {"text": fragment},
            measure=lambda payload: len(json.dumps(payload)), invoke=model, result=result):
            pass
        self.assertTrue(result.complete)
        self.assertEqual(sum(map(len, calls)), 100)


class StoredOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_retains_partial_output_and_publication_failure_rolls_back(self):
        from llm.llm import LargeLanguageModel, DataComponent
        from llm.components.tools.builtin import BuiltinToolComponent
        from llm.components.tools import ToolRegistry
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); work = root / "work"; work.mkdir()
            async with BuiltinTools(work, allow_commands=True, shell=["/bin/sh", "-c"]) as toolkit:
                component = BuiltinToolComponent(toolkit, name="builtin")
                async with LargeLanguageModel(root / "backend", components=[DataComponent(), component]) as app:
                    project = await app.projects.acreate(components=["data", "builtin"])
                    data, builtin = project.components["data"], project.components["builtin"]
                    await data.acreate({}, identifier="results")
                    await builtin.aconfigure({"config": {"enabled": ["process_start", "process_output", "process_cancel"],
                        "output": {"component": "data", "identifier": "results"}}, "policy": {"output_preview_chars": 3}})
                    registry = component.resolve_runtime(project.data, ToolRegistry.CAPABILITY,
                                                         data_factory=lambda name: project.components[name])
                    async def invoke(name, args):
                        tool, args = registry.prepare(name, args)
                        return await tool.handler(args)
                    start = await invoke("process_start", {"argv": [sys.executable, "-c",
                        "import sys; print('partial 한글',flush=True); sys.stdin.read()"], "stdin": True})
                    identifier = start["process_id"]
                    async with asyncio.timeout(5):
                        while not (await invoke("process_output", {"process_id": identifier, "max_chars": 20}))["stdout"]:
                            await asyncio.sleep(.005)
                    stopped = await invoke("process_cancel", {"process_id": identifier})
                    self.assertEqual(stopped["status"], "cancelled")
                    self.assertEqual(stopped["stdout"], "par")
                    self.assertEqual(await builtin.aread_output(identifier, "stdout", offset=0, limit=20), "partial 한글\n")
                    before = await data.asnapshot("results")
                    # A failed record write rolls back the imported stdout/stderr as well.
                    with patch.object(component, "_write", side_effect=OSError("disk full")):
                        second = await invoke("process_start", {"argv": [sys.executable, "-c", "print('new output')"]})
                        with self.assertRaises(OSError):
                            await toolkit.processes.sessions[second["process_id"]].task
                    self.assertEqual(await data.asnapshot("results"), before)
                    self.assertEqual(list(await builtin.alist()), [identifier])

    async def test_capture_disk_failure_terminates_a_flooding_process(self):
        from llm.services.infrastructure.captured_text import CapturedText
        from llm.components.tools.builtin.capture import output_scope
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        store = SimpleNamespace(preview_chars=3, owner="test", publish=AsyncMock())
        with tempfile.TemporaryDirectory() as root:
            async with BuiltinTools(root, allow_commands=True, shell=["/bin/sh", "-c"]) as toolkit:
                with patch.object(CapturedText, "extend", side_effect=OSError("disk full")), output_scope(store):
                    async with asyncio.timeout(5):
                        with self.assertRaisesRegex(OSError, "disk full"):
                            await toolkit.processes.execute({"argv": [sys.executable, "-c",
                                "import os\nwhile True: os.write(1,b'x'*65536)"]})
                item, = toolkit.processes.sessions.values()
                self.assertIsNotNone(item.process.returncode)
                self.assertEqual(item.status, "failed")
                store.publish.assert_not_awaited()

    async def test_approval_streamed_result_reopen_and_deletion_protection(self):
        from llm.llm import LargeLanguageModel, DataComponent, LoopEngine
        from llm.components.tools.builtin import BuiltinToolComponent
        from llm.providers.litellm import LiteLLMCompletionAdapter
        from llm.services.composition import BackendServices
        from llm.services.runtime.tools import ToolRuntime
        from llm.core.tool_contracts import ToolClassification
        from tests.llm.test_loop import ScriptedCompletion, chunk, call
        from tests.llm.configuration_fixtures import configure_engine
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); work = root / "work"; work.mkdir()
            command = "printf effect >> marker; " + sys.executable + " -c \"import sys; sys.stdout.write('abc' * 200000)\""
            model = ScriptedCompletion([chunk(calls=[call(json.dumps({"command": command}), name="shell_execute")], finish="tool_calls")],
                                       [chunk("done", finish="stop")])
            async with BuiltinTools(work, allow_commands=True, shell=["/bin/sh", "-c"]) as toolkit:
                component = BuiltinToolComponent(toolkit, name="builtin")
                runtime = ToolRuntime(classify=lambda invocation: ToolClassification("process.execute", "test", 1))
                async with LargeLanguageModel(root / "backend", components=[DataComponent(), component],
                    engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},
                    services=BackendServices(tool_runtime=runtime)) as app:
                    project = await app.projects.acreate(components=["data", "builtin"], config={
                        "policies": {"approval": {"enabled": True, "risk_scheme": "test", "rules": [
                            {"id": "process", "category": "process.execute", "max_risk": 1}]}}})
                    data = project.components["data"]
                    await data.acreate({}, identifier="results")
                    builtin = project.components["builtin"]
                    await builtin.aconfigure({"config": {"enabled": ["shell_execute", "process_output"],
                        "output": {"component": "data", "identifier": "results"}},
                        "policy": {"output_preview_chars": 15}})
                    await configure_engine(project, "loop")
                    await project.agents.acreate({"engine": "loop", "completion": {"model": "test/model"},
                        "tools": ["shell_execute", "process_output"]}, identifier="worker")
                    session = await project.sessions.acreate(agent_ids=["worker"])
                    run = (await (await session.run.submit("run", recipients=["worker"])).wait())[0]
                    self.assertEqual(run.data.status, "paused", run.data.error)
                    self.assertFalse((work / "marker").exists())
                    self.assertEqual(await builtin.alist(), {})
                    resumed = (await (await session.run.resume(run.id, agent_id="worker")).wait())[0]
                    self.assertEqual(resumed.data.status, "completed", resumed.data.error)
                    self.assertEqual((work / "marker").read_text(), "effect")
                    records = await builtin.alist()
                    identifier, record = next(iter(records.items()))
                    with self.assertRaises(ValueError):
                        await builtin.aupdate(identifier, {"status": "completed"})
                    self.assertEqual(record["stdout_chars"], 600000)
                    self.assertEqual(len(record["stdout"]), 15)
                    self.assertLess(len(json.dumps(record)), 3000)
                    self.assertEqual(await builtin.aread_output(identifier, "stdout", offset=599990, limit=10), "cabcabcabc")
                    snapshot = await data.asnapshot("results")
                    with self.assertRaises(ValueError):
                        await data.adelete("results", expected_version=snapshot["version"])
                    project_id = project.id
            async with BuiltinTools(work, allow_commands=True, shell=["/bin/sh", "-c"]) as toolkit:
                async with LargeLanguageModel(root / "backend", components=[DataComponent(), BuiltinToolComponent(toolkit, name="builtin")]) as app:
                    project = await app.projects.aload(project_id)
                    self.assertEqual(await project.components["builtin"].aread_output(identifier, "stdout", offset=599997, limit=3), "abc")
                    self.assertEqual((work / "marker").read_text(), "effect")
