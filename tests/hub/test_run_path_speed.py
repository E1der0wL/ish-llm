"""Reduce disposable preparation/display work without skipping admission."""

from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from hub.backend.execution.binding_cache import BindingCache
from hub.backend.execution.engine_selection import agent_model
from hub.backend.execution.environment import EnvironmentEngine
from hub.backend.observability.snapshot import SnapshotReader
from hub.config.environment import ShellEnvironment
from hub.ui.output import OutputParser
from hub.ui.output.streaming import StreamingParse
from hub.widget.transcript.message_cache import Joined
from llm.components.tools import Tool, ToolRegistry
from llm.core.models import Message, Project, Run
from llm.engines.loop import LoopEngine
from tests.hub import test_observation_reads as observation_tests
from tests.llm.support.runtime_tools import TEST_CONTRACT


class BindingReuseTests(unittest.TestCase):
    def test_same_run_reuses_binding_but_changes_rebuild_and_copies_are_isolated(self):
        async def handler(arguments):
            return arguments
        registry = ToolRegistry((Tool('read', '', {'type': 'object'}, handler, contract=TEST_CONTRACT),))
        project = Project('p', 'Project', None, components=('builtin_tools',))
        run = Run('r', 's', 'u', 'a', 'loop', None, agent_id='assistant')
        context = NS(project=project, run=run, session=NS(id='s'), tools=registry)
        environment = ShellEnvironment.from_dict({'cwd': '/tmp', 'env': {'PWD': '/tmp'}})
        build = Mock(side_effect=lambda: registry.select(('read',)))
        cache = BindingCache()
        first = cache.bind(context, environment, build)
        second = cache.bind(context, environment, build)
        self.assertEqual(build.call_count, 1)
        self.assertIsNot(first, second)
        self.assertIs(second.get('read').handler, handler)
        first.register(Tool('extra', '', {'type': 'object'}, handler, contract=TEST_CONTRACT))
        self.assertEqual(cache.bind(context, environment, build).names(), ('read',))
        mutations = (
            lambda: project.config.update(data={'changed': True}),
            lambda: setattr(context, 'run', replace(run, id='other')),
            lambda: setattr(context, 'tools', registry.select(('read',))),
            lambda: setattr(context, 'session', NS(id='other')),
        )
        for mutate in mutations:
            before = build.call_count
            mutate()
            cache.bind(context, environment, build)
            self.assertEqual(build.call_count, before + 1)
        changed_env = ShellEnvironment.from_dict({'cwd': '/var/tmp', 'env': {'PWD': '/var/tmp'}})
        before = build.call_count
        cache.bind(context, changed_env, build)
        cache.clear()
        cache.bind(context, changed_env, build)
        self.assertEqual(build.call_count, before + 2)

    def test_custom_registry_is_never_reused(self):
        class Custom(ToolRegistry):
            pass
        context = NS(project=Project('p', 'P', None),
                     run=Run('r', 's', 'u', 'a', 'loop', None, agent_id='assistant'),
                     session=NS(id='s'), tools=Custom())
        build = Mock(return_value=context.tools)
        cache = BindingCache()
        cache.bind(context, None, build)
        cache.bind(context, None, build)
        self.assertEqual(build.call_count, 2)

    def test_standard_model_hint_does_not_construct_an_execution_engine(self):
        engine = EnvironmentEngine(LoopEngine(), None)
        engines = NS(get=Mock(return_value=engine),
                     resolve_agent=Mock(side_effect=AssertionError('display constructed an engine')))
        self.assertEqual(agent_model(engines, {'engine': 'loop', 'completion': {'model': 'test/model'}}),
                         ('test/model', False))
        self.assertEqual(agent_model(engines, {'engine': 'loop'}), ('', True))
        engines.get.return_value = NS()
        engines.resolve_agent = Mock(return_value=LoopEngine())
        self.assertEqual(agent_model(engines, {'engine': 'custom'}), ('', True))
        engines.resolve_agent.assert_called_once()

    def test_unavailable_strategy_keeps_registry_diagnostic(self):
        engines = NS(get=Mock(side_effect=KeyError('missing')),
                     resolve_agent=Mock(side_effect=ValueError('Engine is not registered: missing')))
        with self.assertRaisesRegex(ValueError, 'Engine is not registered: missing'):
            agent_model(engines, {'engine': 'missing'})


class StreamingParseTests(unittest.TestCase):
    def test_every_prefix_matches_canonical_parser_including_rewrites_and_final(self):
        samples = (
            '한국어\r\n\r\n```python\r\nprint(1)\r\n```\r\n\r\n| A | B |\r\n| - | - |\r\n| 1 | 2 |\r\n',
            'before\n<hub-card title="Hi">body</hub-card>\n\n# Title\ntext',
            '> ```\n> quote\n> ```\n\n~~~x\n<hub-card/>\n~~~~\n\nlast\n',
            '```py\nno close\n\nnext\n',
            '- list\n\n  ```\n  nested\n  ```\n\n    indent\n',
            '<hub-card>unfinished\nclosing</hub-card>\n\n[link][ref]\n\n[ref]: /url\n',
            '```\ncode\n```\n\n    indented\n\n---\n<hub-image src="x"/>\n',
            '<hub-card><nested/></hub-card>\n\ntext\n',
        )
        for sample in samples:
            state, parser, canonical = StreamingParse(), OutputParser(), OutputParser()
            for end in range(1, len(sample) + 1):
                text = sample[:end]
                actual, state = state.parse(parser, text, final=False)
                self.assertEqual(actual, canonical.parse(text, final=False), (sample, end))
            actual, state = state.parse(parser, sample, final=True)
            self.assertEqual(actual, canonical.parse(sample))
            for text in ('replacement\n', sample[:10], sample):
                actual, state = state.parse(parser, text, final=False)
                self.assertEqual(actual, canonical.parse(text, final=False))

    def test_sealed_history_is_not_reparsed_and_pending_tail_stays_live(self):
        prefix = ('```python\n' + 'print(1)\n' * 100 + '```\n\n') * 10
        parser, state = OutputParser(), StreamingParse()
        _, state = state.parse(parser, prefix, final=False)
        before = parser.parsed_bytes
        for count in range(1, 101):
            text = prefix + 'x' * count
            actual, state = state.parse(parser, text, final=False)
        self.assertEqual(actual, OutputParser().parse(text, final=False))
        self.assertLess(parser.parsed_bytes - before, 6000)

    def test_joined_viewport_slice_reads_only_requested_rows(self):
        class Rows:
            def __len__(self):
                return 100000
            def __getitem__(self, index):
                touched.append(index)
                return index
        touched = []
        rows = Joined(Rows(), (100000, 100001))
        self.assertEqual(rows[99998:100002], (99998, 99999, 100000, 100001))
        self.assertEqual(touched, [99998, 99999])
        self.assertEqual(rows[-1:-4:-1], (100001, 100000, 99999))


class DisplayTraversalTests(unittest.IsolatedAsyncioTestCase):
    async def test_changed_tail_does_not_walk_settled_history(self):
        fixture = observation_tests.ObservationReadTests()
        fixture.fixture()
        class Counted(list):
            def __iter__(self):
                for item in super().__iter__():
                    visits.append(item.id)
                    yield item
        visits = []
        records = Counted([Message(id=f'u{i}', role='user', content=str(i), status='completed')
                           for i in range(1000)] + [fixture.message])
        first = await fixture.reader.messages(None, records, latest=fixture.run)
        visits.clear()
        for _ in range(10):
            fixture.reader.projection.changed(fixture.run.id)
            actual = await fixture.reader.messages(None, records, latest=fixture.run)
        self.assertEqual(visits, [])
        self.assertIs(first[0], actual[0])
        self.assertEqual(actual, await SnapshotReader().messages(None, records, latest=fixture.run))
