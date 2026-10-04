"""Project Python Tool 패키지의 저장 경계, schema, 공용 dependency 준비를 검증한다."""
import asyncio
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from llm.components.tools import ToolComponent, ToolRegistry, ToolPaths, tool
from llm.components.tools.function import function_tool
from llm.components.tools import packages
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository


SOURCE = '''from llm.components.tools import tool
@tool(effect="read_only", revision="2")
async def main(query: str, limit: int = 10):
    """Search documents."""
    return {"query": query, "limit": limit}
'''


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.component = ToolComponent()
        self.projects = ProjectManager(ProjectRepository(self.root / "workspace"), components=[self.component])
        self.project = self.projects.create("tools", components=["tools"])
        self.data = self.projects.component(self.project, "tools")
        self.paths = ToolPaths.for_project(self.project)

    def resolve(self, project=None):
        return self.component.resolve_tools(self.projects.load((project or self.project).id))

    def test_source_crud_is_static_and_transactional(self):
        self.assertEqual(list(self.paths.root.iterdir()), [])
        value = {"source": "raise AssertionError('CRUD must not import')", "requirements": "# none\n"}
        with self.assertRaises(ValueError):
            self.data.create(value)
        self.data.create(value, identifier="search")
        self.assertEqual(self.paths.source("search").read_text(), value["source"])
        self.assertEqual(self.data.list(), {"search": value})
        with self.assertRaises(FileExistsError):
            self.data.create(value, identifier="search")
        with self.assertRaises(FileNotFoundError):
            self.data.save("missing", value)
        self.data.update("search", {"source": SOURCE})
        self.assertEqual(self.data.load("search")["requirements"], "# none\n")
        before = self.data.load("search")
        original = packages._write_text
        def fail_second(path, text):
            if path.name == "requirements.txt":
                raise OSError("disk full")
            return original(path, text)
        with patch.object(packages, "_write_text", side_effect=fail_second), self.assertRaises(OSError):
            self.data.save("search", {"source": "# changed", "requirements": "new-dependency"})
        self.assertEqual(self.data.load("search"), before)
        self.data.enable("search")
        with self.assertRaises(ValueError):
            self.data.delete("search")
        self.data.disable("search")
        self.data.save("search", {"source": SOURCE})
        self.assertFalse(self.paths.requirements("search").exists())
        self.data.delete("search")
        self.assertEqual(self.data.list(), {})

    def test_configuration_does_not_require_installed_packages(self):
        self.data.configure({"enabled": ["not_created"]})
        self.assertEqual(self.data.enabled(), ["not_created"])
        with self.assertRaises(FileNotFoundError):
            self.resolve()
        for invalid in (["same", "same"], [1], "name"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.data.configure({"enabled": invalid})
        with self.assertRaises(TypeError):
            ToolComponent(catalog=ToolRegistry())

    def test_path_traversal_symlinks_and_special_files_rejected(self):
        for name in (None, "", "../foo", "foo/bar", "x" * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.data.create({"source": SOURCE}, identifier=name)
        outside = self.root / "outside"
        outside.mkdir()
        self.paths.package("linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.data.load("linked")
        (self.paths.root / "linked").unlink()
        self.data.create({"source": SOURCE}, identifier="search")
        for filename in ("search.py", "requirements.txt"):
            path = self.paths.package("search") / filename
            path.unlink(missing_ok=True)
            path.symlink_to(outside / "file")
            with self.assertRaises(ValueError):
                self.data.load("search")
            path.unlink()
            path.write_text(SOURCE if filename.endswith(".py") else "")
        import os
        special = self.paths.package("search") / "extra"
        os.mkfifo(special)
        with self.assertRaises(ValueError):
            self.data.load("search")

    def test_clone_backup_restore_do_not_import_or_install(self):
        value = {"source": "raise AssertionError('no import')", "requirements": "uninstalled-package>=1\n"}
        self.data.create(value, identifier="search")
        self.data.enable("search")
        with patch.object(packages, "inspect_tool", side_effect=AssertionError("no import")), \
                patch.object(packages, "_host_target", side_effect=AssertionError("no dependency access")):
            clone = self.projects.clone(self.project)
            self.assertEqual(self.projects.component(clone, "tools").load("search"), value)
            self.assertEqual(clone.config.parameters.setdefault("components", {})["tools"]["enabled"], ["search"])
            archive = self.projects.backup(self.project, self.root / "backup")
            restored_manager = ProjectManager(ProjectRepository(self.root / "restored"), components=[ToolComponent()])
            restored = restored_manager.restore_backup(archive)
            self.assertEqual(restored_manager.component(restored, "tools").load("search"), value)
        self.assertFalse(any(path.name == "plugin" for path in Path(archive).rglob("*")))

    def test_project_identity_selection_and_source_refresh(self):
        self.data.create({"source": SOURCE}, identifier="search")
        self.data.create({"source": "# invalid but not selected"}, identifier="admin")
        self.data.enable("search")
        first = self.resolve()
        other = self.projects.clone(self.project)
        other_data = self.projects.component(other, "tools")
        other_data.save("search", {"source": SOURCE.replace('"query": query', '"project": "other"')})
        second = self.resolve(other)
        self.assertEqual(first.names(), ("search",))
        self.assertEqual(asyncio.run(first.get("search").handler({"query": "a"})), {"query": "a", "limit": 10})
        self.assertEqual(asyncio.run(second.get("search").handler({"query": "a"})), {"project": "other", "limit": 10})
        self.data.save("search", {"source": SOURCE.replace('limit: int = 10', 'limit: int = 20')})
        self.assertEqual(asyncio.run(self.resolve().get("search").handler({"query": "a"}))["limit"], 20)
        self.assertFalse(list(self.paths.root.rglob("__pycache__")))
        self.assertFalse(list(self.paths.root.rglob("*.json")))

    def test_prepare_validates_without_dependency_or_run(self):
        self.data.create({"source": SOURCE}, identifier="search")
        with patch.object(packages, "_host_target", side_effect=AssertionError("not needed")):
            definition = self.data.prepare("search")
        self.assertEqual(definition["function"]["name"], "search")
        self.assertEqual(self.projects.sessions.list(self.project), [])
        self.data.save("search", {"source": "# incomplete"})
        with self.assertRaisesRegex(ValueError, "async main"):
            self.data.prepare("search")
        with patch("llm.services.runtime.tools.current_tool_call", return_value=object()), self.assertRaises(RuntimeError):
            self.data.prepare("search")

    def test_inspection_never_executes_source_in_backend(self):
        import builtins
        source = 'import builtins\nbuiltins.PROJECT_TOOL_LEAK = True\n' + SOURCE
        self.data.create({"source": source}, identifier="search")
        self.data.enable("search")
        modules = {key for key in sys.modules if key.startswith("_ish_llm_tool_")}
        self.data.prepare("search")
        resolved = self.resolve()
        self.assertFalse(hasattr(builtins, "PROJECT_TOOL_LEAK"))
        self.assertEqual({key for key in sys.modules if key.startswith("_ish_llm_tool_")}, modules)
        from llm.components.tools.process import ProjectToolHandler
        self.assertIsInstance(resolved.get("search").handler, ProjectToolHandler)


class FunctionTests(unittest.TestCase):
    def compile(self, source):
        namespace = {}
        exec(source, namespace)
        return function_tool("search", namespace.get("main"))

    def test_decorator_identity_immutable_contract_and_explicit_strict(self):
        async def main(query: str):
            """Search."""
            return query
        self.assertIs(tool(effect="read_only")(main), main)
        with self.assertRaises(FrozenInstanceError):
            main.__tool_contract__.revision = "changed"
        with self.assertRaises(TypeError):
            main.__tool_options__["strict"] = True
        item = function_tool("search", main)
        self.assertNotIn("strict", item.definition["function"])
        self.assertEqual(item.contract.effect, "read_only")
        explicit = self.compile(SOURCE.replace('@tool(effect="read_only", revision="2")', '@tool(strict=False)'))
        self.assertIs(explicit.definition["function"]["strict"], False)

    def test_supported_annotations_constraints_required_and_defaults(self):
        value = self.compile('''from typing import Annotated, Literal, Optional, Union
from pydantic import Field
from llm.components.tools import tool
@tool()
async def main(query: Annotated[str, Field(description="Query")],
               limit: Annotated[int, Field(ge=1, le=20)] = 5, *,
               number: float = 1.5, flag: bool = False, empty: None = None,
               maybe: Optional[str] = None, either: Union[str, int] = 1,
               native: str | None = None, kind: Literal["a", "b"] = "a",
               values: list[int] = [], mapping: dict[str, float] = {}):
    """Search constrained documents."""
    return limit
''')
        schema = value.parameters
        self.assertEqual(schema["required"], ["query"])
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(schema["properties"]["limit"], {"type": "integer", "minimum": 1, "maximum": 20, "default": 5})
        registry = ToolRegistry((value,))
        for args in ('{"query":"x","limit":0}', '{"query":"x","unknown":1}', '{}'):
            with self.assertRaises(ValueError):
                registry.prepare("search", args)
        self.assertEqual(asyncio.run(value.handler({"query": "x"})), 5)

    def test_invalid_entrypoints_annotations_and_parameters(self):
        sources = ["# no main", "def main(): pass", "async def main(): pass",
            SOURCE.replace('    """Search documents."""\n', ''),
            SOURCE.replace('query: str', 'query'),
            SOURCE.replace('query: str, limit: int = 10', '*args: str'),
            SOURCE.replace('query: str, limit: int = 10', '**kwargs: str'),
            SOURCE.replace('query: str, limit: int = 10', 'query: str, /'),
            SOURCE.replace('query: str', 'query: object'),
            SOURCE.replace('query: str', 'query: list'),
            SOURCE.replace('query: str', 'query: dict[int, str]')]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.compile(source)

    def test_invalid_python_defaults_are_rejected_without_coercion(self):
        for parameter in (
                'limit: int = "ten"',
                'limit: int = "10"',
                'limit: Annotated[int, Field(ge=1, le=20)] = 100',
                'mode: Literal["fast", "slow"] = "invalid"',
                'values: list[int] = ["wrong"]',
                'values: dict[str, int] = {"count": "wrong"}',
                'query: str = None'):
            name = parameter.split(":", 1)[0]
            with self.subTest(parameter=parameter), self.assertRaisesRegex(
                    ValueError, "default does not satisfy its annotation: " + name):
                self.compile('''from typing import Annotated, Literal
from pydantic import Field
from llm.components.tools import tool
@tool()
async def main(''' + parameter + '''):
    """Default validation."""
''')

    def test_signature_alone_owns_defaults_and_nullable_none_is_preserved(self):
        value = self.compile('''from typing import Annotated
from pydantic import Field
from llm.components.tools import tool
@tool()
async def main(required: Annotated[int, Field(default=100, ge=1, le=20)],
               limit: Annotated[int, Field(default=100, ge=1, le=20)] = 10,
               query: str | None = None):
    """Signature defaults override metadata without coercion."""
    return {"limit": limit, "query": query}
''')
        schema = value.parameters
        self.assertEqual(schema["required"], ["required"])
        self.assertNotIn("default", schema["properties"]["required"])
        self.assertEqual(schema["properties"]["limit"], {
            "type": "integer", "minimum": 1, "maximum": 20, "default": 10})
        self.assertIsNone(schema["properties"]["query"]["default"])
        self.assertEqual(asyncio.run(value.handler({"required": 1})), {"limit": 10, "query": None})

    def test_python_defaults_must_still_be_json_safe(self):
        for parameter, error_type in (('number: float = float("nan")', ValueError),
                                      ('number: float = float("inf")', ValueError),
                                      ('values: list[int] = {1, 2}', TypeError)):
            with self.subTest(parameter=parameter), self.assertRaises(error_type):
                self.compile('''from llm.components.tools import tool
@tool()
async def main(''' + parameter + '''):
    """JSON-safe defaults only."""
''')


class PackageRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def setup_runtime(self, source, *, policy=None, responses=None):
        from llm.llm import LargeLanguageModel
        from llm.engines.loop import LoopEngine
        from llm.services.configuration import ServiceConfig
        from tests.llm.test_loop import ScriptedCompletion, chunk, call
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        model = ScriptedCompletion(*(responses or [
            [chunk(calls=[call('{}', name='act')], finish='tool_calls')],
            [chunk('done', finish='stop')]]))
        app = LargeLanguageModel(temporary.name, components=[ToolComponent()],
            engines={"loop": LoopEngine(completion_fn=model)}, services=ServiceConfig(tool_policy=policy))
        self.addAsyncCleanup(app.shutdown)
        project = await app.projects.acreate(config={"parameters": {"engines": {"loop": {"completion": {"model": "test/model"}}}}}, components=["tools"])
        await project.components.tools.acreate({"source": source}, identifier="act")
        await project.components.tools.aprepare("act")
        await project.components.tools.aenable("act")
        return project, await project.sessions.acreate(), model

    async def test_file_tool_retry_step_and_normal_none_completion(self):
        from llm.services.runtime.tools import ToolPolicy
        source = '''from llm.components.tools import tool
from llm.services.runtime.tools import ToolExecutionError
from pathlib import Path
@tool(effect="read_only")
async def main():
    """Retry only a known no-effect failure."""
    receipt = Path(__file__).parents[2] / "retry-fixture"
    if not receipt.exists():
        receipt.write_text("attempted")
        raise ToolExecutionError("temporary", effect="none", retryable=True)
    return None
'''
        project, session, model = await self.setup_runtime(source, policy=ToolPolicy(max_retries=1))
        run = await (await session.run.submit("act", engine="loop")).wait()
        self.assertEqual(run.data.status, "completed", run.data.error)
        steps = [s for s in await run.steps.alist() if s.kind == "tool"]
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].status, "completed")
        self.assertEqual(steps[0].metadata["retry_attempt"], 1)
        self.assertIsNone(steps[0].output.data)
        self.assertEqual(model.requests[1]["messages"][-1]["content"], "null")

    async def test_file_tool_coded_error_preserved_through_step_and_run(self):
        source = '''from llm.components.tools import tool
from llm.providers.requests import ProviderError
@tool()
async def main():
    """Fail with a classified error."""
    raise ProviderError("provider_rate_limit")
'''
        _, session, _ = await self.setup_runtime(source)
        run = await (await session.run.submit("act", engine="loop")).wait()
        self.assertEqual(run.data.status, "failed")
        self.assertEqual(run.data.error_code, "provider_rate_limit")
        steps = [s for s in await run.steps.alist() if s.kind == "tool"]
        self.assertEqual(steps[0].status, "failed")

    async def test_file_tool_approval_and_operation_receipt(self):
        from llm.services.runtime.tools import ToolPolicy
        approvals = []
        async def approve(call):
            approvals.append(call)
            return True
        source = '''from llm.components.tools import tool
@tool(effect="external", approval_required=True, operation_key_required=True)
async def main():
    """Return a recorded result after authorization."""
    return {"done": True}
'''
        _, session, _ = await self.setup_runtime(source, policy=ToolPolicy(
            authorize=approve, operation_key=lambda call: "file-tool-operation"))
        run = await (await session.run.submit("act", engine="loop")).wait()
        self.assertEqual(run.data.status, "completed", run.data.error)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0].contract["approval_required"], True)
        receipt = await session.run.aoperation("file-tool-operation")
        self.assertEqual(receipt["status"], "completed")

    async def test_updated_source_is_used_by_next_run(self):
        from tests.llm.test_loop import chunk, call
        source = '''from llm.components.tools import tool
@tool()
async def main():
    """Report source revision."""
    return "first"
'''
        replies = [[chunk(calls=[call('{}', name='act')], finish='tool_calls')], [chunk('done', finish='stop')]]
        project, session, model = await self.setup_runtime(source, responses=replies + replies)
        first = await (await session.run.submit("act", engine="loop")).wait()
        self.assertEqual(first.data.status, "completed")
        await project.components.tools.asave("act", {"source": source.replace('return "first"', 'return "second"')})
        second = await (await session.run.submit("act again", engine="loop")).wait()
        self.assertEqual(second.data.status, "completed", second.data.error)
        self.assertEqual(model.requests[1]["messages"][-1]["content"], "first")
        self.assertEqual(model.requests[3]["messages"][-1]["content"], "second")


class DependencyTests(PackageTests):
    """설치 네트워크 대신 public ish installer 계약을 기록한다. host Config는 실제 소스 사용."""
    def setUp(self):
        super().setUp()
        # ish main/plugin manager를 실행하지 않고 실제 Config의 --home 의미만 사용한다.
        source = Path(__file__).resolve().parents[2] / "ish.platform/src/ish"
        if not source.exists():
            self.skipTest("ish host source required")
        host = types.ModuleType("ish")
        host.__path__ = [str(source)]
        plugin = types.ModuleType("ish.plugin")
        plugin.__path__ = [str(source / "plugin")]
        self.patch = patch.dict(sys.modules, {"ish": host, "ish.plugin": plugin})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        spec = importlib.util.spec_from_file_location("ish.config", source / "config.py")
        config_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config_module)
        config_module.config.ISH_HOME = self.root / "chosen-home"
        self.config = config_module.config
        self.modules = patch.dict(sys.modules, {"ish.config": config_module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        self.target = self.config.PLUGIN_LIB_DIR
        self.calls = []
        @contextmanager
        def install(python, requirement, target, constraints=()):
            self.calls.append((python, requirement, target, constraints))
            self.distribution("probe-dependency", "1.0")
            try:
                yield
            except BaseException:
                import shutil
                shutil.rmtree(target / "probe_dependency-1.0.dist-info")
                raise
        dependencies = types.ModuleType("ish.plugin.dependencies")
        dependencies.install_dependency = install
        self.installer = patch.dict(sys.modules, {"ish.plugin.dependencies": dependencies})
        self.installer.start()
        self.addCleanup(self.installer.stop)

    def distribution(self, name, version, requires=()):
        folder = self.target / (name.replace("-", "_") + "-" + version + ".dist-info")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "METADATA").write_text("Metadata-Version: 2.1\nName: " + name + "\nVersion: " + version + "\n" +
                                       "".join("Requires-Dist: " + req + "\n" for req in requires))

    def package(self, requirements):
        self.data.create({"source": SOURCE, "requirements": requirements}, identifier="search")
        self.data.enable("search")

    def test_missing_dependency_installs_only_in_explicit_prepare_and_host_home(self):
        self.distribution("existing", "2.0")
        self.package("probe-dependency>=1,<2")
        with self.assertRaisesRegex(ValueError, "prepare"):
            self.resolve()
        self.assertEqual(self.calls, [])
        self.data.prepare("search")
        self.assertEqual(len(self.calls), 1)
        python, requirement, target, constraints = self.calls[0]
        self.assertEqual(python, sys.executable)
        self.assertEqual(target, self.root / "chosen-home/plugin/lib")
        self.assertIn("existing==2.0", constraints)
        self.resolve()
        self.data.prepare("search")
        self.assertEqual(len(self.calls), 1)

    def test_satisfied_dependency_reused_and_conflict_never_installed(self):
        self.distribution("probe-dependency", "1.0")
        self.package("probe-dependency>=1,<2")
        self.data.prepare("search")
        self.data.update("search", {"requirements": "probe-dependency>=2"})
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.data.prepare("search")
        self.assertEqual(self.calls, [])

    def test_prepare_final_import_failure_rolls_back_installer(self):
        self.package("probe-dependency>=1")
        self.data.update("search", {"source": "# no main"})
        with self.assertRaisesRegex(ValueError, "async main"):
            self.data.prepare("search")
        self.assertEqual(packages._installed(self.target), {})

    def test_requirement_syntax_markers_extras_and_transitive_conflict(self):
        for text in ('-r other.txt', '--index-url https://invalid', '-e .', 'x @ https://invalid/x.whl', './local'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                packages.requirements(text)
        self.assertEqual(packages.requirements("# comment\n\n"), [])
        self.distribution("outer", "1", ['child>=2; extra == "more"'])
        self.distribution("child", "1")
        self.assertEqual(packages._missing(packages.requirements("outer"), packages._installed(self.target)), [])
        with self.assertRaisesRegex(ValueError, "conflict"):
            packages._missing(packages.requirements("outer[more]"), packages._installed(self.target))
        self.assertEqual(packages._missing(packages.requirements('absent; python_version < "2"'), {}), [])

    def test_active_extra_dependency_is_installed_without_rechecking_parent_marker(self):
        self.distribution("outer", "1", ['probe-dependency>=1; extra == "more"'])
        self.package("outer[more]")
        self.data.prepare("search")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], "probe-dependency>=1")

    def test_duplicate_distribution_metadata_is_rejected(self):
        self.distribution("probe-dependency", "1.0")
        self.distribution("probe-dependency", "2.0")
        self.package("probe-dependency>=1")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            self.data.prepare("search")
        self.assertEqual(self.calls, [])

    def test_child_imports_host_plugin_lib_without_backend_module_import(self):
        self.distribution("probe-dependency", "1.0")
        (self.target / "probe_dependency.py").write_text("VALUE = 42\n")
        self.package("probe-dependency>=1")
        self.data.update("search", {"source": SOURCE.replace('return {"query": query, "limit": limit}',
            'from probe_dependency import VALUE\n    return VALUE')})
        self.data.prepare("search")
        self.assertEqual(asyncio.run(self.resolve().get("search").handler({"query": "x"})), 42)
        self.assertNotIn("probe_dependency", sys.modules)
        self.assertEqual(self.calls, [])

    def test_actual_host_installer_stages_and_rolls_back_final_tool_validation(self):
        path = Path(__file__).resolve().parents[2] / "ish.platform/src/ish/plugin/dependencies.py"
        spec = importlib.util.spec_from_file_location("ish.plugin.dependencies", path)
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        commands = []
        def pip_fixture(command, *, check):
            commands.append(command)
            stage = Path(command[command.index("--target") + 1])
            metadata = stage / "probe_dependency-1.0.dist-info"
            metadata.mkdir(parents=True)
            (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: probe-dependency\nVersion: 1.0\n")
            (metadata / "RECORD").write_text("probe_dependency-1.0.dist-info/METADATA,,\nprobe_dependency.py,,\n")
            (stage / "probe_dependency.py").write_text("VALUE = 1\n")
        self.package("probe-dependency>=1")
        self.data.update("search", {"source": "# fail after dependency promotion"})
        with patch.dict(sys.modules, {"ish.plugin.dependencies": installer}), \
                patch.object(installer.subprocess, "run", side_effect=pip_fixture):
            with self.assertRaisesRegex(ValueError, "async main"):
                self.data.prepare("search")
            self.assertEqual(packages._installed(self.target), {})
            self.assertFalse((self.target / "probe_dependency.py").exists())
            self.data.update("search", {"source": SOURCE})
            self.data.prepare("search")
        self.assertEqual(packages._installed(self.target)["probe-dependency"].version, "1.0")
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][:4], [sys.executable, "-I", "-m", "pip"])
        self.assertEqual(Path(commands[0][commands[0].index("--target") + 1]).parent.parent, self.target.parent)
