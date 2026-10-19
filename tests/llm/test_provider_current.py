"""Read current-format bytes with fresh backends, preserving approval/effect identity."""
from llm.providers.litellm import LiteLLMCompletionAdapter
import hashlib
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from llm.llm import LargeLanguageModel, LoopEngine
from llm.components.tools import Tool, ToolRegistry, ToolContract
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk


class CurrentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_completed_receipt_and_pending_approval_survive_reopen(self):
        fixture=Path(__file__).with_name('fixtures')/'provider_current'
        manifest=json.loads((fixture/'manifest.json').read_text())
        self.assertEqual(manifest['fixture_schema'], 1)
        self.assertEqual(hashlib.sha256((fixture/'workspace.tar.gz').read_bytes()).hexdigest(), manifest['archive_sha256'])
        original=hashlib.sha256((fixture/'workspace.tar.gz').read_bytes()).hexdigest()
        for scenario, identities in manifest['scenarios'].items():
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                with tarfile.open(fixture/'workspace.tar.gz') as archive:
                    archive.extractall(root,filter='data')
                for name, digest in manifest['files'].items():
                    self.assertEqual(hashlib.sha256((root/name).read_bytes()).hexdigest(),digest)
                effects=[]
                async def act(arguments):
                    effects.append(arguments)
                    return 'golden effect'
                contract=ToolContract(approval_required=True) if scenario=='approval' else None
                registry=ToolRegistry((Tool('act','action',{'type':'object'},act,contract=contract),))
                model=ScriptedCompletion([chunk('resumed answer',finish='stop')])
                async with LargeLanguageModel(root/scenario,components=[RuntimeTools(registry)],
                        engines={'loop':LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=model))}) as app:
                    project=await app.projects.aload(identities['project'])
                    session=await project.sessions.aload(identities['session'])
                    prior=await session.run.aload(identities['run'])
                    self.assertEqual(str(prior.data.status),identities['status'])
                    if scenario=='completed':
                        self.assertEqual((await prior.aresponse()).content,'golden answer')
                        self.assertEqual(model.requests,[])
                        continue
                    if scenario=='approval':
                        request,=await prior.ainteractions()
                        self.assertEqual(effects,[])
                        await prior.arespond(request.respond('approve'))
                    resumed=await (await session.run.resume(prior.id)).wait(timeout=10)
                    self.assertEqual(resumed.data.status,'completed',resumed.data.error)
                    self.assertEqual(effects,[{}] if scenario=='approval' else [])
                    self.assertEqual(len(model.requests),1)
                    self.assertEqual(model.requests[0]['messages'][-1]['content'],'golden effect')
                    self.assertEqual((await prior.aget_data()).status,identities['status'])
        self.assertEqual(hashlib.sha256((fixture/'workspace.tar.gz').read_bytes()).hexdigest(),original)

    async def test_current_graph_binding_and_worker_descriptor_are_stable(self):
        from llm.components.workflows import WorkflowComponent
        from llm.engines.graph import GraphEngine
        from llm.components.tools.function import function_tool
        from llm.components.tools.process import descriptor_fingerprint
        fixture = Path(__file__).with_name('fixtures') / 'provider_current'
        manifest = json.loads((fixture / 'graph_manifest.json').read_text())
        archive_digest = hashlib.sha256((fixture / 'graph.tar.gz').read_bytes()).hexdigest()
        self.assertEqual(archive_digest, manifest['archive_sha256'])
        namespace = {}; exec(manifest['worker_source'], namespace)
        tool = function_tool('worker', namespace['main'])
        descriptor = {'name': tool.name, 'description': tool.description, 'parameters': tool.parameters,
                      'definition': tool.definition.to_dict(), 'contract': tool.contract.to_dict()}
        self.assertEqual(descriptor, manifest['worker_descriptor'])
        self.assertEqual(descriptor_fingerprint(descriptor), manifest['worker_fingerprint'])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with tarfile.open(fixture / 'graph.tar.gz') as archive:
                archive.extractall(root, filter='data')
            for name, digest in manifest['files'].items():
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
            effects = []
            async def work(node):
                effects.append(node.node_id)
                return {'seen': node.state.get('seen', []) + [node.node_id]}
            async def act(arguments): return 'unused'
            tools = RuntimeTools(ToolRegistry((Tool('act', 'action', {'type': 'object'}, act),)))
            async with LargeLanguageModel(root, components=[tools, WorkflowComponent()], engines={
                    'graph': GraphEngine(handlers={'work': work}, revision='current-provider-1')}) as app:
                ids = manifest['identities']
                session = await (await app.projects.aload(ids['project'])).sessions.aload(ids['session'])
                prior = await session.run.aload(ids['run'])
                before = await prior.acheckpoint()
                resumed = await (await session.run.resume(prior.id)).wait(timeout=10)
                self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
                self.assertEqual(effects, ['second'])
                self.assertTrue(any(s.metadata.get('reused') for s in await resumed.steps.alist()))
                self.assertEqual((await prior.aget_data()).status, 'paused')
                self.assertEqual((await prior.acheckpoint())['header'], before['header'])
        self.assertEqual(hashlib.sha256((fixture / 'graph.tar.gz').read_bytes()).hexdigest(), archive_digest)

    async def test_current_definition_fields_bind_exactly_and_old_wire_is_rejected(self):
        from llm.core.model_protocol import ToolDefinition
        from llm.components.tools.process import descriptor_fingerprint
        async def act(arguments): return None
        fingerprints = []
        for strict in (None, False, True):
            definition = ToolDefinition('act', '', {'type': 'object'}, strict)
            registry = ToolRegistry((Tool('act', '', {'type': 'object'}, act, definition=definition),))
            self.assertEqual(registry.definitions(), (definition,))
            encoded = [item.to_dict() for item in registry.definitions()]
            self.assertEqual(encoded, [definition.to_dict()])
            fingerprints.append(descriptor_fingerprint(encoded))
        self.assertEqual(len(set(fingerprints)), 3)
        with self.assertRaisesRegex(TypeError, 'ToolDefinition'):
            Tool('act', '', {'type': 'object'}, act, definition={
                'type': 'function', 'function': {'name': 'act', 'parameters': {'type': 'object'}}})
        registry = ToolRegistry((Tool('act', '', {'type': 'object'}, act),))
        with self.assertRaisesRegex(ValueError, 'invalid tool arguments'):
            registry.prepare('act', '{}')
