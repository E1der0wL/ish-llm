"""Generate supplemental Graph/worker evidence using an untouched git archive.

PYTHONPATH=<git archive BASE directory> python <this script> <output directory>
The imported sources are checked against the baseline commit before generation.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

from llm.llm import LargeLanguageModel
from llm.engines.graph import GraphEngine
from llm.components.workflows import WorkflowGraph, WorkflowComponent
from llm.components.tools import Tool, ToolRegistry
from llm.components.tools.function import function_tool
from llm.components.tools.process import descriptor_fingerprint
from tests.llm.support.runtime_tools import RuntimeTools
import llm

BASE = '94229cb865374d99f20ec48c5e48269488041a4e'
SOURCE = '''from llm.components.tools import tool
@tool(strict=True)
async def main(query: str, limit: int = 3):
    """Golden worker definition."""
    return query
'''

async def generate():
    checkout = Path(__file__).resolve().parents[4]
    baseline = Path(llm.__file__).resolve().parent.parent
    source_hashes = {}
    for name in subprocess.check_output(['git', '-C', str(checkout), 'ls-tree', '-r', '--name-only', BASE, 'llm'], text=True).splitlines():
        expected = subprocess.check_output(['git', '-C', str(checkout), 'show', BASE + ':' + name])
        assert (baseline / name).read_bytes() == expected, name
        source_hashes[name] = hashlib.sha256(expected).hexdigest()
    target = Path(sys.argv[1]); target.mkdir(parents=True, exist_ok=True)
    namespace = {}; exec(SOURCE, namespace)
    tool = function_tool('worker', namespace['main'])
    descriptor = {'name': tool.name, 'description': tool.description, 'parameters': tool.parameters,
                  'definition': tool.definition, 'contract': tool.contract.to_dict()}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        effects = []
        async def work(node):
            effects.append(node.node_id)
            return {'seen': node.state.get('seen', []) + [node.node_id]}
        async def act(arguments): return 'unused'
        tools = RuntimeTools(ToolRegistry((Tool('act', 'action', {'type': 'object'}, act),)))
        graph = (WorkflowGraph(entry='first').node('first', 'work')
                 .node('second', 'work', pause_before=True).node('end', 'end')
                 .connect('first', 'second').connect('second', 'end').to_dict())
        async with LargeLanguageModel(root, components=[tools, WorkflowComponent()], engines={
                'graph': GraphEngine(handlers={'work': work}, revision='golden-v1')}) as app:
            project = await app.projects.acreate('golden', components=['tools', 'workflows'])
            project.components.tools.enable('act')
            await (await project.components.aget('workflows')).acreate(graph, identifier='flow')
            session = await project.sessions.acreate()
            run = await (await session.run.submit('graph', engine='graph', engine_options={'workflow': 'flow'})).wait()
            assert run.data.status == 'paused', run.data.error
            assert effects == ['first']
            identities = {'project': project.id, 'session': session.id, 'run': run.id}
        files = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(root.rglob('*')) if p.is_file() and p.suffix != '.log' and '.lock' not in p.name}
        with tarfile.open(target / 'graph.tar.gz', 'w:gz') as archive:
            for name in files: archive.add(root / name, arcname=name)
        (target / 'graph_manifest.json').write_text(json.dumps({
            'baseline': BASE, 'source_hashes': source_hashes, 'storage': 'original schema v1',
            'identities': identities, 'files': files, 'worker_source': SOURCE,
            'worker_descriptor': descriptor, 'worker_fingerprint': descriptor_fingerprint(descriptor)}, indent=2)+'\n')

if __name__ == '__main__': asyncio.run(generate())
