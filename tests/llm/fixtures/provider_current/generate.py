"""Generate current-format synthetic Provider/Graph fixtures, never user data."""
from llm.providers.litellm import LiteLLMCompletionAdapter
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile

from llm.llm import LargeLanguageModel, LoopEngine, ProjectConfig
from llm.components.tools import Tool, ToolRegistry, ToolContract
from tests.llm.support.runtime_tools import RuntimeTools
from tests.llm.test_loop import ScriptedCompletion, chunk, call

from .provenance import provenance, archive_hash

async def generate():
    target = Path(__file__).parent
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)/'workspace'
        effects=[]
        async def act(arguments):
            effects.append(arguments)
            return 'golden effect'
        info={}
        for scenario, contract, responses in (
            ('completed', None, [[chunk('golden answer', finish='stop')]]),
            ('resume', None, [[chunk(calls=[call('{}',name='act')],finish='tool_calls')], [ConnectionError('fixture interruption')]]),
            ('approval', ToolContract(approval_required=True), [[chunk(calls=[call('{}',name='act')],finish='tool_calls')]]),
        ):
            registry=ToolRegistry((Tool('act','action',{'type':'object'},act,contract=contract),))
            async with LargeLanguageModel(root/scenario, components=[RuntimeTools(registry)],
                    engines={'loop':LoopEngine(completion_adapter=LiteLLMCompletionAdapter(completion_fn=ScriptedCompletion(*responses)))}) as app:
                project=await app.projects.acreate('golden',components=['tools'],config=ProjectConfig(parameters={
                    'engines':{'loop':{'config':{'completion':{'model':'test/model'}}}}}))
                project.components.tools.enable('act')
                await project.agents.acreate({'engine': 'loop'}, identifier='writer')
                session=await project.sessions.acreate('golden')
                await session.agents.aattach('writer')
                run=await (await session.run.submit('golden request',agent_id='writer')).wait()
                expected={'completed':'completed','resume':'failed','approval':'paused'}[scenario]
                assert run.data.status == expected, (scenario,run.data.status,run.data.error)
                info[scenario]={'project':project.id,'session':session.id,'run':run.id,'status':str(run.data.status)}
        assert len(effects)==1
        files={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
               for p in sorted(root.rglob('*')) if p.is_file() and p.suffix != '.log' and '.lock' not in p.name}
        with tarfile.open(target/'workspace.tar.gz','w:gz') as archive:
            for name in files:
                archive.add(root/name,arcname=name)
        (target/'manifest.json').write_text(json.dumps({**provenance(), 'archive_sha256':archive_hash(target/'workspace.tar.gz'), 'scenarios':info,'files':files},indent=2)+'\n')

if __name__=='__main__':
    asyncio.run(generate())
