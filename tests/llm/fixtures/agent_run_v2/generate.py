"""Freeze actual stage-C records before changing Session/Message cardinality."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path('/workspace/ish-llm')
EXPECTED = '3076192f58f5b803136b2a1bcf14a5fc9946f6fd'
assert subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip() == EXPECTED
subprocess.run(['git', '-C', str(ROOT), 'diff', '--exit-code', EXPECTED, '--', 'llm'], check=True)
sys.path.insert(0, str(ROOT))
from llm.llm import LargeLanguageModel
from llm.engines.base import EngineEventType
from tests.llm.support.fake_engine import FakeStreamingEngine


async def generate():
    destination = Path('/workspace/refactor-bf-evidence/agent-run-v2')
    entered = asyncio.Event()
    async def observe(run, event):
        if event.type == EngineEventType.TEXT_DELTA:
            entered.set()
    engine = FakeStreamingEngine(chunks=('stage C answer',))
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory) / 'workspace'
        async with LargeLanguageModel(workspace, engines={'echo': engine}, on_event=observe) as app:
            project = await app.projects.acreate('Stage C baseline')
            await project.agents.acreate({'engine': 'echo'}, identifier='writer')
            completed = await project.sessions.acreate('completed')
            done = await (await completed.run.submit('single Agent input', agent_id='writer')).wait(timeout=10)
            assert done.data.status == 'completed', done.data.error
            entered.clear()
            engine.gate = asyncio.Event()
            queued = await project.sessions.acreate('durable queue')
            active = await queued.run.submit('interrupt this attempt', agent_id='writer')
            await asyncio.wait_for(entered.wait(), 10)
            pending = await queued.run.submit('preserve this request', agent_id='writer')
            assert pending.data.status == 'queued'
            await queued.run.shutdown()
            assert pending.data.status == 'queued'
            assert active.run.data.status == 'interrupted'
            manifest = {'baseline_sha': EXPECTED, 'session_storage_version': 1,
                'run_storage_version': 2, 'agent_storage_version': 1,
                'project_id': project.id, 'agent_id': 'writer',
                'completed_session_id': completed.id, 'completed_run_id': done.id,
                'queued_session_id': queued.id, 'queued_message_id': pending.id,
                'interrupted_run_id': active.run.id}
        manifest['files'] = {str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted(workspace.rglob('*')) if p.is_file()}
        with tarfile.open(destination / 'workspace.tar.gz', 'w:gz') as archive:
            for path in sorted(workspace.rglob('*')):
                archive.add(path, arcname=str(path.relative_to(workspace)), recursive=False)
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        print('baseline', EXPECTED, 'files', len(manifest['files']))


asyncio.run(generate())
