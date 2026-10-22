"""실제 Linux namespace, 영속 공간, 공통 Tool 승인/취소 경계를 검증한다."""

from llm.providers.litellm import LiteLLMCompletionAdapter

import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from llm.components.tools.builtin import BuiltinTools, BuiltinToolComponent, BuiltinToolRunner, TestWorkspaces
from llm.components.tools import ToolClassification
from llm.components.workflows import WorkflowComponent, WorkflowGraph
from llm.core.models import RunStatus
from llm.engines.graph import GraphEngine
from llm.engines.graph.tool import ToolNode
from llm.engines.loop import LoopEngine
from llm.llm import LargeLanguageModel, BackendServices
from llm.policies import ExecutionLimitError
from llm.services.runtime.processes import ProcessToolRunner
from llm.services.runtime.tools import ToolCall, ToolRuntime
from tests.llm.test_loop import ScriptedCompletion, chunk, call
from tests.llm.support.runtime_tools import TEST_CONTRACT


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.storage = self.root / "spaces"
        self.spaces = self.new_spaces()
        self.tools = BuiltinTools(self.source, test_workspaces=self.spaces)
        self.addAsyncCleanup(self.tools.close)

    def new_spaces(self, **options):
        spaces = TestWorkspaces(self.storage, source_root=self.source, allow_network=False,
                                read_only_paths=[sys.base_prefix, sys.prefix], **options)
        self.addAsyncCleanup(spaces.close)
        return spaces

    @contextmanager
    def owner(self, project_id="project"):
        with patch("llm.components.tools.builtin.workspaces.current_tool_call", return_value=
                   ToolCall("test", {}, "step", project_id=project_id, session_id="session", run_id="run", contract=TEST_CONTRACT.to_dict())):
            yield

    async def invoke(self, name, **args):
        tool, arguments = self.tools.registry.prepare(name, args)
        return await tool.handler(arguments)

    async def create(self, paths=None):
        return (await self.invoke("test_workspace_create", source_paths=paths or []))["workspace_id"]

    def files(self, identifier, project="project"):
        return self.storage / project / identifier / "files"

    def python(self, code):
        return [str(Path(sys.executable).resolve()), "-I", "-c", code]

    async def wait_file(self, path, task):
        async with asyncio.timeout(10):
            while not path.exists():
                if task.done():
                    self.fail(f"Command exited before marker: {await task}")
                await asyncio.sleep(.01)

    async def test_empty_copy_edit_results_restart_and_explicit_cleanup(self):
        (self.source / "selected").mkdir()
        (self.source / "selected/a.py").write_text("print('original')\n")
        (self.source / "unselected.txt").write_text("private")
        with self.owner():
            empty = await self.create()
            self.assertEqual(list(self.files(empty).iterdir()), [])
            identifier = await self.create(["selected"])
            self.assertFalse((self.files(identifier) / "unselected.txt").exists())
            original = await self.invoke("file_read", workspace_id=identifier, path="selected/a.py")
            await self.invoke("file_patch", workspace_id=identifier, path="selected/a.py",
                expected_sha256=original["sha256"], old_text="original", new_text="changed")
            result = await self.invoke("test_workspace_execute", workspace_id=identifier,
                argv=self.python("from pathlib import Path; Path('result.txt').write_text('ok'); print('out'); import sys; print('err',file=sys.stderr); sys.exit(7)"))
            self.assertEqual((result["returncode"], result["stdout"], result["stderr"]), (7, "out\n", "err\n"))
            self.assertEqual((await self.invoke("file_read", workspace_id=identifier, path="result.txt"))["text"], "ok")
            self.assertEqual((self.source / "selected/a.py").read_text(), "print('original')\n")
            await self.spaces.close()
            reopened = self.new_spaces()
            with patch.object(ProcessToolRunner, "execute", side_effect=AssertionError("no replay")):
                self.assertEqual(len((await reopened.list({}))["workspaces"]), 2)
                self.assertEqual((await reopened.file_call("file_read", {"workspace_id": identifier, "path": "result.txt"}))["text"], "ok")
                await reopened.delete({"workspace_id": identifier})
                await reopened.delete({"workspace_id": empty})
            self.assertEqual((await reopened.list({}))["workspaces"], [])
            self.assertTrue((self.source / "selected/a.py").exists())

    async def test_copy_rejects_traversal_links_special_files_and_overlap(self):
        (self.source / "dir").mkdir()
        (self.source / "dir/a").write_text("x")
        (self.source / "link").symlink_to(self.source / "dir", target_is_directory=True)
        os.mkfifo(self.source / "pipe")
        for paths in ([".."], [str(self.source / "dir")], ["."], ["link"], ["pipe"], ["dir", "dir/a"]):
            with self.subTest(paths=paths), self.owner(), self.assertRaises((ValueError, OSError)):
                await self.create(paths)
        with self.owner():
            self.assertEqual((await self.spaces.list({}))["workspaces"], [])

    async def test_no_owner_foreign_owner_and_invalid_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "owning Project"):
            await self.spaces.create({"source_paths": []})
        with self.owner():
            identifier = await self.create()
            for invalid in ("../escape", "", str(self.root)):
                with self.assertRaises(ValueError):
                    await self.spaces.delete({"workspace_id": invalid})
        with self.owner("other"):
            self.assertEqual((await self.spaces.list({}))["workspaces"], [])
            with self.assertRaises(FileNotFoundError):
                await self.spaces({"workspace_id": identifier, "argv": ["/bin/true"]})
        self.assertTrue(self.files(identifier).exists())

    async def test_actual_sandbox_hides_host_files_env_and_network_and_protects_runtime(self):
        self.assertIsNotNone(shutil.which("bwrap"), "Bubblewrap required for isolation validation")
        secret = self.root / "secret"
        secret.write_text("host secret")
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with self.owner(), patch.dict(os.environ, {"ISH_PRIVATE_TEST": "must not inherit"}):
            identifier = await self.create()
            code = f'''
import json, os, pathlib, socket
s=socket.socket(); s.settimeout(.3)
try: s.connect(('127.0.0.1', {port})); network=True
except OSError: network=False
try: pathlib.Path('/usr/ish-sandbox-test').write_text('bad'); readonly=False
except OSError: readonly=True
print(json.dumps({{'secret':pathlib.Path({str(secret)!r}).exists(),
 'record':pathlib.Path('../record.json').exists(), 'env':os.environ.get('ISH_PRIVATE_TEST'),
 'network':network,'readonly':readonly}}))
'''
            result = await self.spaces({"workspace_id": identifier, "argv": self.python(code)})
            self.assertEqual(result["returncode"], 0, result["stderr"])
            self.assertEqual(json.loads(result["stdout"]), {"secret": False, "record": False, "env": None, "network": False, "readonly": True})

    async def test_missing_bwrap_never_executes_on_host(self):
        with self.owner():
            identifier = await self.create()
            with patch("llm.services.runtime.processes.shutil.which", return_value=None), self.assertRaises(ExecutionLimitError):
                await self.spaces({"workspace_id": identifier, "argv": self.python("open('unsafe','w').write('bad')")})
            self.assertFalse((self.files(identifier) / "unsafe").exists())

    async def test_command_environment_uses_host_or_explicit_replacement(self):
        spaces = self.new_spaces(env={"ISH_CONFIGURED": "host"})
        with self.owner(), patch.dict(os.environ, {"ISH_PRIVATE_TEST": "not inherited"}):
            identifier = await self.create()
            for options, expected in (({}, {"ISH_CONFIGURED": "host"}),
                    ({"env": {}}, {}), ({"env": {"LM_LICENSE_FILE": "27000@example", "EMPTY": "", "VALUE": "한글 space=$HOME"}},
                     {"LM_LICENSE_FILE": "27000@example", "EMPTY": "", "VALUE": "한글 space=$HOME"})):
                with self.subTest(options=options):
                    result = await spaces({"workspace_id": identifier, "argv": ["/usr/bin/env", "-0"], **options})
                    self.assertEqual(result["returncode"], 0, result["stderr"])
                    actual = dict(part.split("=", 1) for part in result["stdout"].split("\0") if part)
                    # Bubblewrap publishes its actual --chdir as PWD, even
                    # with --clearenv. Other parent/config values must not leak.
                    self.assertEqual(actual, {**expected, "PWD": str(self.files(identifier))})
            result = await self.invoke("test_workspace_execute", workspace_id=identifier,
                argv=["/bin/sh", "-c", 'printf "%s\\n" "$VALUE"; pwd'], env={"VALUE": "explicit"})
            self.assertEqual(result["stdout"], "explicit\n" + str(self.files(identifier)) + "\n")
            self.assertEqual(spaces._runtime["env"], {"ISH_CONFIGURED": "host"})

    async def test_invalid_environment_is_rejected_before_sandbox_spawn(self):
        with self.owner():
            identifier = await self.create()
            with patch("llm.services.runtime.processes.asyncio.create_subprocess_exec") as spawn:
                for env in (None, [], {"X": 1}, {"": "x"}, {"A=B": "x"}, {"A\0B": "x"}, {"X": "x\0y"}):
                    with self.subTest(env=env), self.assertRaises(ValueError):
                        await self.invoke("test_workspace_execute", workspace_id=identifier, argv=["/bin/true"], env=env)
                    with self.assertRaises(ValueError):
                        await self.spaces({"workspace_id": identifier, "argv": ["/bin/true"], "env": env})
                spawn.assert_not_called()

    async def test_concurrent_sandbox_environments_do_not_mutate_host_or_baseline(self):
        before = dict(os.environ)
        spaces = self.new_spaces(env={"ISH_ENV_VALUE": "baseline"})
        with self.owner():
            identifiers = [await self.create(), await self.create()]
            results = await asyncio.gather(*(spaces({"workspace_id": identifier,
                "argv": self.python("import os,time; time.sleep(.05); print(os.environ['ISH_ENV_VALUE'])"),
                "env": {"ISH_ENV_VALUE": value}}) for identifier, value in zip(identifiers, ("first", "second"))))
        self.assertEqual([item["stdout"] for item in results], ["first\n", "second\n"])
        self.assertEqual(spaces._runtime["env"], {"ISH_ENV_VALUE": "baseline"})
        self.assertEqual(dict(os.environ), before)

    async def test_unsafe_mounts_and_nonboolean_network_are_rejected(self):
        for mount in (self.root, self.source, Path("/")):
            with self.assertRaises(ValueError):
                TestWorkspaces(self.storage, source_root=self.source, allow_network=False, read_only_paths=[mount])
        with self.assertRaises(ValueError):
            TestWorkspaces(self.storage, source_root=self.source, allow_network=None)

    async def test_busy_workspace_rejects_delete_file_changes_and_second_command(self):
        with self.owner():
            identifier = await self.create()
            ready = self.files(identifier) / "ready"
            task = asyncio.create_task(self.spaces({"workspace_id": identifier,
                "argv": self.python("import pathlib,time; pathlib.Path('ready').touch(); time.sleep(60)")}))
            try:
                await self.wait_file(ready, task)
                other = self.new_spaces()
                for action in (other.delete({"workspace_id": identifier}),
                               other.file_call("file_create", {"workspace_id": identifier,"path":"bad","content":"bad"}),
                               other({"workspace_id": identifier,"argv":["/bin/true"]})):
                    with self.assertRaisesRegex(ValueError, "busy"):
                        await action
                # 별도 백엔드 프로세스도 동일 flock을 사용한다.
                probe = f'''
import asyncio
from unittest.mock import patch
from llm.components.tools.builtin import TestWorkspaces
from llm.services.runtime.tools import ToolCall
from tests.llm.support.runtime_tools import TEST_CONTRACT
async def main():
    w=TestWorkspaces({str(self.storage)!r},source_root={str(self.source)!r},allow_network=False)
    with patch('llm.components.tools.builtin.workspaces.current_tool_call',return_value=ToolCall('test',{{}},'s',project_id='project',contract=TEST_CONTRACT.to_dict())):
        try: await w.delete({{'workspace_id':{identifier!r}}})
        except ValueError as error: print(str(error))
        else: raise AssertionError('busy workspace deleted')
asyncio.run(main())
'''
                child = await asyncio.create_subprocess_exec(sys.executable,"-c",probe,
                    stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
                out, err = await asyncio.wait_for(child.communicate(),10)
                self.assertEqual(child.returncode,0,err.decode())
                self.assertIn("busy",out.decode())
                # 서로 다른 공간의 동시 실행은 막지 않는다.
                second = await self.create()
                result = await other({"workspace_id": second, "argv": ["/bin/true"]})
                self.assertEqual(result["returncode"], 0, result["stderr"])
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self.spaces.delete({"workspace_id": identifier})

    async def test_interrupted_creation_or_cleanup_without_record_is_recoverable(self):
        with self.owner():
            identifier = await self.create()
            (self.files(identifier).parent / "record.json").unlink()
            row, = (await self.spaces.list({}))["workspaces"]
            self.assertEqual(row["reason"], "missing_record")
            with self.assertRaisesRegex(ValueError,"incomplete"):
                await self.spaces({"workspace_id":identifier,"argv":["/bin/true"]})
            await self.spaces.delete({"workspace_id":identifier})
            self.assertFalse(self.files(identifier).parent.exists())

    async def test_cancel_and_timeout_kill_detached_descendants_preserve_results(self):
        for timed in (False, True):
            with self.subTest(timeout=timed), self.owner():
                identifier = await self.create()
                child = "import pathlib,time; time.sleep(1.5); pathlib.Path('orphan').touch(); time.sleep(60)"
                source = ("import subprocess,sys,pathlib,time; "
                          f"subprocess.Popen([sys.executable,'-I','-c',{child!r}],start_new_session=True); "
                          "pathlib.Path('ready').touch(); time.sleep(60)")
                async def execute():
                    async with asyncio.timeout(.7 if timed else None):
                        return await self.spaces({"workspace_id": identifier, "argv": self.python(source)})
                task = asyncio.create_task(execute())
                await self.wait_file(self.files(identifier) / "ready", task)
                if not timed:
                    task.cancel()
                with self.assertRaises(TimeoutError if timed else asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)
                await asyncio.sleep(1.6)
                self.assertFalse((self.files(identifier) / "orphan").exists())
                self.assertTrue((self.files(identifier) / "ready").exists())
                await self.spaces.delete({"workspace_id": identifier})

    async def test_untrusted_output_symlinks_and_fifos_are_not_opened(self):
        with self.owner():
            identifier = await self.create()
            await self.spaces({"workspace_id": identifier, "argv": self.python("import os; os.mkfifo('pipe'); os.symlink('/etc/passwd','link')")})
            for path in ("pipe", "link"):
                with self.assertRaises((ValueError, OSError)):
                    await self.invoke("file_read", workspace_id=identifier, path=path)
            await self.spaces.delete({"workspace_id": identifier})

    async def test_incomplete_creation_is_inspectable_deletable_but_not_executable(self):
        with self.owner():
            identifier = await self.create()
            path = self.files(identifier).parent / "record.json"
            record = json.loads(path.read_text())
            record["ready"] = False
            path.write_text(json.dumps(record))
            self.assertFalse((await self.spaces.list({}))["workspaces"][0]["ready"])
            with self.assertRaisesRegex(ValueError, "incomplete"):
                await self.spaces({"workspace_id": identifier, "argv": ["/bin/true"]})
            await self.spaces.delete({"workspace_id": identifier})

    async def test_backend_process_death_stops_command_and_restart_never_replays(self):
        with self.owner():
            identifier = await self.create()
        ready = self.files(identifier) / "ready"
        code = "import pathlib,time; pathlib.Path('ready').touch(); time.sleep(2); pathlib.Path('orphan').touch(); time.sleep(60)"
        source = f'''
import asyncio, os
from unittest.mock import patch
from pathlib import Path
from llm.components.tools.builtin import TestWorkspaces
from llm.services.runtime.tools import ToolCall
from tests.llm.support.runtime_tools import TEST_CONTRACT
async def main():
    spaces=TestWorkspaces({str(self.storage)!r}, source_root={str(self.source)!r}, allow_network=False,
                         read_only_paths={[sys.base_prefix,sys.prefix]!r})
    with patch('llm.components.tools.builtin.workspaces.current_tool_call', return_value=ToolCall('test',{{}},'s',project_id='project',contract=TEST_CONTRACT.to_dict())):
        task=asyncio.create_task(spaces({{'workspace_id':{identifier!r},'argv':{self.python(code)!r}}}))
        while not Path({str(ready)!r}).exists():
            if task.done(): raise RuntimeError(await task)
            await asyncio.sleep(.01)
        os._exit(23)
asyncio.run(main())
'''
        process = await asyncio.create_subprocess_exec(sys.executable, "-c", source, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await asyncio.wait_for(process.communicate(), 15)
        self.assertEqual(process.returncode, 23, stderr.decode())
        await asyncio.sleep(2.2)
        self.assertFalse((self.files(identifier) / "orphan").exists())
        with self.owner():
            reopened = self.new_spaces()
            self.assertEqual((await reopened.list({}))["workspaces"][0]["workspace_id"], identifier)
            await reopened.delete({"workspace_id": identifier})

    async def test_output_limit_fails_without_publishing_truncated_success(self):
        spaces = self.new_spaces(max_output_bytes=64)
        with self.owner():
            identifier = await self.create()
            with self.assertRaisesRegex(ExecutionLimitError, "output limit"):
                await spaces({"workspace_id": identifier,"argv":self.python("print('x'*10000)")})
            await spaces.delete({"workspace_id": identifier})


    async def test_loop_policy_approval_before_spawn_resume_once_and_backend_reopen(self):
        component = BuiltinToolComponent(self.tools, name="builtin")
        runtime = ToolRuntime(runner=BuiltinToolRunner(), classify=lambda _: ToolClassification("test.execute", "test-v1", 1))
        model = ScriptedCompletion()
        def backend():
            return LargeLanguageModel(self.root / "backend", components=[component], engines={"loop": LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},
                                      services=BackendServices(tool_runtime=runtime))
        app = backend()
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=["builtin"], config={
            "parameters": {"components": {"builtin": {"config": {"enabled": ["test_workspace_execute"]}}},
                           "engines": {"loop": {"config": {"completion": {"model": "test/model"}}}}},
            "policies": {"approval": {"enabled": True, "risk_scheme": "test-v1", "rules": [
                {"id": "sandbox", "category": "test.execute", "max_risk": 1}]}}})
        with self.owner(project.id):
            identifier = await self.create()
        args = {"workspace_id": identifier, "env": {"ISH_APPROVED_VALUE": "ok"},
                "argv": self.python("import os; from pathlib import Path; p=Path('count'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print(os.environ['ISH_APPROVED_VALUE'])")}
        model.responses.extend([[chunk(calls=[call(json.dumps(args), name="test_workspace_execute")], finish="tool_calls")], [chunk("done", finish="stop")]])
        await project.agents.acreate({"engine": "loop", "tools": ['test_workspace_execute']}, identifier="loop")
        session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
        original = ProcessToolRunner.execute
        invocations = []
        async def tracked(runner, *a, **kw):
            invocations.append(a)
            return await original(runner, *a, **kw)
        with patch.object(ProcessToolRunner, "execute", tracked), patch(
                "llm.services.runtime.processes.asyncio.create_subprocess_exec", wraps=asyncio.create_subprocess_exec) as spawn:
            paused = (await (await session.run.submit("test", recipients=["loop"])).wait())[0]
            self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
            self.assertEqual(invocations, [])
            self.assertEqual(spawn.call_count, 0)
            self.assertEqual([r.actor for r in await paused.ainteraction_responses()], ["policy"])
            request, = await paused.ainteractions()
            self.assertEqual(request.action["arguments"]["env"], {"ISH_APPROVED_VALUE": "ok"})
            self.assertFalse((self.files(identifier, project.id) / "count").exists())
            # 승인 receipt를 저장한 채 Backend를 다시 열어도 자동 실행하지 않는다.
            await app.shutdown()
            app = backend()
            self.addAsyncCleanup(app.shutdown)
            session = await (await app.projects.aload(project.id)).sessions.aload(session.id)
            self.assertEqual(spawn.call_count, 0)
            resumed = (await (await session.run.resume(paused.id, agent_id="loop")).wait())[0]
            self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)
            self.assertEqual(len(invocations), 1)
            self.assertEqual(spawn.call_count, 1)
        self.assertEqual((self.files(identifier, project.id) / "count").read_text(), "x")
        step = next(step for step in await resumed.steps.alist() if step.kind == "tool")
        self.assertEqual(step.output.data["stdout"], "ok\n")
        await app.shutdown()
        app = backend()
        self.addAsyncCleanup(app.shutdown)
        reopened = await (await app.projects.aload(project.id)).sessions.aload(session.id)
        stored = await reopened.run.aload(resumed.id)
        self.assertEqual((await stored.aresult()).status, RunStatus.COMPLETED)
        self.assertEqual((self.files(identifier, project.id) / "count").read_text(), "x")

    async def test_graph_user_approval_and_changed_environment_binding(self):
        component = BuiltinToolComponent(self.tools, name="builtin")
        app = LargeLanguageModel(self.root / "graph", components=[component, WorkflowComponent()],
            engines={"graph": GraphEngine(handlers={"tool": ToolNode()})},
            services=BackendServices(tool_runtime=ToolRuntime(runner=BuiltinToolRunner())))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(components=["builtin", "workflows"], config={
            "parameters": {"components": {"builtin": {"config": {"enabled": ["test_workspace_execute"]}}}}})
        with self.owner(project.id):
            identifier = await self.create()
        graph = (WorkflowGraph(entry="test", initial_state={"args": {"workspace_id": identifier,"argv":["/bin/true"]}})
            .node("test", "tool", tool="test_workspace_execute", arguments_key="args", result_key="result")
            .node("end", "end").connect("test","end").to_dict())
        await (await project.components.aget("workflows")).acreate(graph, identifier="test")
        await project.agents.acreate({"engine": "graph", "engine_options": {"workflow": "test"}}, identifier="graph")
        session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
        paused = (await (await session.run.submit("test",recipients=["graph"])).wait())[0]
        self.assertEqual(paused.data.status, RunStatus.PAUSED, paused.data.error)
        self.assertEqual(await paused.ainteraction_responses(), [])
        request, = await paused.ainteractions()
        await paused.arespond(request.respond("approve"))
        original = component.toolkit._execution_binding["test_workspaces"]["allow_network"]
        component.toolkit._execution_binding["test_workspaces"]["allow_network"] = True
        with self.assertRaisesRegex(ValueError, "changed"):
            await session.run.resume(paused.id, agent_id="graph")
        component.toolkit._execution_binding["test_workspaces"]["allow_network"] = original
        resumed = (await (await session.run.resume(paused.id, agent_id="graph")).wait())[0]
        self.assertEqual(resumed.data.status, RunStatus.COMPLETED, resumed.data.error)

    async def test_create_and_delete_require_decisions_before_mutating_space(self):
        model = ScriptedCompletion()
        async with LargeLanguageModel(self.root / "lifecycle", components=[BuiltinToolComponent(self.tools,name="builtin")],
                engines={"loop":LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}, services=BackendServices(tool_runtime=ToolRuntime(runner=BuiltinToolRunner()))) as app:
            project = await app.projects.acreate(components=["builtin"],config={"parameters":{
                "engines":{"loop":{"config":{"completion":{"model":"test/model"}}}},
                "components":{"builtin":{"config":{"enabled":["test_workspace_create","test_workspace_delete"]}}}}})
            await project.agents.acreate({"engine": "loop", "tools": ['test_workspace_create', 'test_workspace_delete']}, identifier="loop")
            session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
            identifier = None
            for name in ("test_workspace_create","test_workspace_delete"):
                args = {"source_paths":[]} if identifier is None else {"workspace_id":identifier}
                model.responses.extend([[chunk(calls=[call(json.dumps(args),name=name)],finish="tool_calls")],[chunk("done",finish="stop")]])
                paused = (await (await session.run.submit(name,recipients=["loop"])).wait())[0]
                self.assertEqual(paused.data.status,RunStatus.PAUSED,paused.data.error)
                with self.owner(project.id):
                    rows = (await self.spaces.list({}))["workspaces"]
                self.assertEqual(len(rows),0 if identifier is None else 1)
                request, = await paused.ainteractions()
                await paused.arespond(request.respond("approve"))
                done = (await (await session.run.resume(paused.id,agent_id="loop")).wait())[0]
                self.assertEqual(done.data.status,RunStatus.COMPLETED,done.data.error)
                if identifier is None:
                    identifier = next(s for s in await done.steps.alist() if s.kind=="tool").output.data["workspace_id"]
                else:
                    self.assertFalse(self.files(identifier,project.id).exists())

    async def test_run_interrupt_drains_sandbox_and_preserves_workspace(self):
        model = ScriptedCompletion()
        async with LargeLanguageModel(self.root / "interrupt", components=[BuiltinToolComponent(self.tools,name="builtin")],
                engines={"loop":LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))},services=BackendServices(tool_runtime=ToolRuntime(runner=BuiltinToolRunner()))) as app:
            project = await app.projects.acreate(components=["builtin"],config={"parameters":{
                "engines":{"loop":{"config":{"completion":{"model":"test/model"}}}},
                "components":{"builtin":{"config":{"enabled":["test_workspace_execute"]}}}}})
            with self.owner(project.id):
                identifier = await self.create()
            args = {"workspace_id":identifier,"argv":self.python("import pathlib,time; pathlib.Path('ready').touch(); time.sleep(2); pathlib.Path('orphan').touch(); time.sleep(60)")}
            model.responses.append([chunk(calls=[call(json.dumps(args),name="test_workspace_execute")],finish="tool_calls")])
            await project.agents.acreate({"engine": "loop", "tools": ['test_workspace_execute']}, identifier="loop")
            session = await project.sessions.acreate(agent_ids=sorted(await project.agents.alist()))
            paused = (await (await session.run.submit("test",recipients=["loop"])).wait())[0]
            self.assertEqual(paused.data.status,RunStatus.PAUSED,paused.data.error)
            request, = await paused.ainteractions()
            await paused.arespond(request.respond("approve"))
            handle = await session.run.resume(paused.id,agent_id="loop")
            pending = asyncio.create_task(handle.wait())
            await self.wait_file(self.files(identifier,project.id) / "ready",pending)
            await session.run.interrupt()
            done, = await asyncio.wait_for(pending,10)
            self.assertEqual(done.data.status,RunStatus.INTERRUPTED,done.data.error)
            await asyncio.sleep(2.1)
            self.assertFalse((self.files(identifier,project.id) / "orphan").exists())
            with self.owner(project.id):
                await self.spaces.delete({"workspace_id":identifier})

