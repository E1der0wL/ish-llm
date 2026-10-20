"""Python mapping reconstruction and strict JSON/API configuration boundaries."""

from dataclasses import asdict, dataclass
import tempfile
import unittest

from llm.core.configuration import resolve_engine_config
from llm.core.models import ProjectConfig
from llm.engines.registry import EngineRegistry
from llm.llm import LargeLanguageModel


class MappingConstructionContracts(unittest.TestCase):
    def test_dataclass_mapping_reconstruction_and_json_roundtrip(self):
        @dataclass
        class Record:
            config: ProjectConfig

        original = ProjectConfig(data={"zero": 0, "false": False, "null": None, "empty": []})
        projected = asdict(Record(original))
        self.assertEqual(projected["config"], original)
        projected["config"].data["empty"].append("detached")
        self.assertEqual(original.data["empty"], [])
        self.assertEqual(ProjectConfig(iter(original.items())), original)
        self.assertEqual(ProjectConfig.deserialize(original.serialize()), original)

    def test_json_configuration_boundaries_do_not_consume_iterators(self):
        consumed = []

        def pairs():
            consumed.append(True)
            yield "data", {}

        for operation in (ProjectConfig.from_dict, lambda value: resolve_engine_config(value, "loop"),
                          EngineRegistry().validate_config):
            with self.subTest(operation=operation), self.assertRaises(TypeError):
                operation(pairs())
        self.assertEqual(consumed, [])


class ProjectJSONContracts(unittest.IsolatedAsyncioTestCase):
    async def test_create_save_preview_and_session_reject_non_objects_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, components=[], engines={}) as app:
                project = await app.projects.acreate(config={"data": {"kept": False}})
                before = (project.paths.root / "project.json").read_bytes()
                factories = (lambda: False, lambda: 0, lambda: "", lambda: [], lambda: (),
                             lambda: iter([("data", {"surprise": True})]))
                for factory in factories:
                    operations = (lambda value: app.projects.acreate(config=value),
                                  lambda value: project.asave(config=value),
                                  lambda value: project.avalidate_config(value),
                                  lambda value: project.sessions.acreate(config=value))
                    for operation in operations:
                        with self.subTest(value=factory, operation=operation), self.assertRaises((TypeError, ValueError)):
                            await operation(factory())
                        self.assertEqual((project.paths.root / "project.json").read_bytes(), before)
                        self.assertEqual([p.id for p in await app.projects.alist()], [project.id])
                        self.assertEqual(await project.sessions.alist(), [])

    async def test_lifecycle_create_rejects_wrong_falsey_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            async with LargeLanguageModel(directory, components=[], engines={}) as app:
                for value in (False, 0, "", [], ()):
                    with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                        app.project_manager.create("invalid", config=value)
                self.assertEqual(await app.projects.alist(), [])
