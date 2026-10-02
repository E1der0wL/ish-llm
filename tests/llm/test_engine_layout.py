"""새 프로세스의 import 순환·공개 클래스 identity·직렬화와 지연 로딩을 검증한다."""

import subprocess
import sys
import unittest


class EngineLayoutTests(unittest.TestCase):
    def test_import_order_public_identity_and_worker_serialization(self):
        for first in ('llm.engines.registry', 'llm.engines.loop', 'llm.engines.graph.agent',
                      'llm.engines.graph.tool', 'llm.engines.pipeline',
                      'llm.services.runtime.tools', 'llm.components.registry'):
            with self.subTest(first=first):
                code = '''
import importlib, pickle, sys
importlib.import_module(sys.argv[1])
from llm.engines.base import BaseEngine
from llm.engines.registry import EngineRegistry
from llm.engines.loop import LoopEngine
from llm.engines.graph import GraphEngine, GraphNodeContext
from llm.engines.graph.agent import AgentNode
from llm.engines.graph.tool import ToolNode
from llm.engines.pipeline import PipelineEngine, PreparationStep
from llm.llm import LoopEngine as HostLoop, GraphEngine as HostGraph
assert LoopEngine is HostLoop and GraphEngine is HostGraph
assert issubclass(LoopEngine, BaseEngine)
for cls in (EngineRegistry, LoopEngine, GraphEngine, GraphNodeContext, AgentNode,
            ToolNode, PipelineEngine, PreparationStep):
    assert pickle.loads(pickle.dumps(cls)) is cls
assert not any(name == 'langgraph' or name.startswith('langgraph.') for name in sys.modules)
registry = EngineRegistry()
registry.register('loop', LoopEngine())
assert isinstance(registry.resolve('loop'), LoopEngine)
for old in ('llm.engines.agent', 'llm.engines.tool', 'llm.engines.checkpoints'):
    assert importlib.util.find_spec(old) is None
assert not hasattr(importlib.import_module('llm.engines.base'), 'EngineRegistry')
'''
                result = subprocess.run([sys.executable, '-c', code, first], capture_output=True,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
