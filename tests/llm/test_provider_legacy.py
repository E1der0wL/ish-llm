"""Read baseline-produced bytes with fresh backends, preserving approval/effect identity."""
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


class LegacyProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_v1_completed_receipt_and_pending_approval_survive_reopen(self):
        fixture=Path(__file__).with_name('fixtures')/'provider_v1'
        manifest=json.loads((fixture/'manifest.json').read_text())
        self.assertEqual(manifest['baseline'],'94229cb865374d99f20ec48c5e48269488041a4e')
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
                        engines={'loop':LoopEngine(completion_fn=model)}) as app:
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
                    resumed=await (await session.run.resume(prior.id,engine='loop')).wait(timeout=10)
                    self.assertEqual(resumed.data.status,'completed',resumed.data.error)
                    self.assertEqual(effects,[{}] if scenario=='approval' else [])
                    self.assertEqual(len(model.requests),1)
                    self.assertEqual(model.requests[0]['messages'][-1]['content'],'golden effect')
                    self.assertEqual((await prior.aget_data()).status,identities['status'])
        self.assertEqual(hashlib.sha256((fixture/'workspace.tar.gz').read_bytes()).hexdigest(),original)

    async def test_baseline_graph_binding_and_worker_descriptor_are_stable(self):
        from llm.components.workflows import WorkflowComponent
        from llm.engines.graph import GraphEngine
        from llm.components.tools.function import function_tool
        from llm.components.tools.process import descriptor_fingerprint
        fixture = Path(__file__).with_name('fixtures') / 'provider_v1'
        manifest = json.loads((fixture / 'graph_manifest.json').read_text())
        namespace = {}; exec(manifest['worker_source'], namespace)
        tool = function_tool('worker', namespace['main'])
        descriptor = {'name': tool.name, 'description': tool.description, 'parameters': tool.parameters,
                      'definition': tool.definition, 'contract': tool.contract.to_dict()}
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
                    'graph': GraphEngine(handlers={'work': work}, revision='golden-v1')}) as app:
                ids = manifest['identities']
                session = await (await app.projects.aload(ids['project'])).sessions.aload(ids['session'])
                prior = await session.run.aload(ids['run'])
                before = await prior.acheckpoint()
                resumed = await (await session.run.resume(prior.id, engine='graph')).wait(timeout=10)
                self.assertEqual(resumed.data.status, 'completed', resumed.data.error)
                self.assertEqual(effects, ['second'])
                self.assertTrue(any(s.metadata.get('reused') for s in await resumed.steps.alist()))
                self.assertEqual((await prior.aget_data()).status, 'paused')
                self.assertEqual((await prior.acheckpoint())['header'], before['header'])
