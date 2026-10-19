"""Generate real pre-domain Agent and queued Message records from a verified checkout."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path('/workspace/ish-llm')
EXPECTED = '7984c8e225ca07711c866607d110727dfbb3c391'
assert subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip() == EXPECTED
subprocess.run(['git', '-C', str(ROOT), 'diff', '--exit-code', EXPECTED, '--', 'llm'], check=True)
sys.path.insert(0, str(ROOT))
from llm.llm import LargeLanguageModel
from llm.core.models import MessageRole, MessageStatus
from tests.llm.support.fake_engine import FakeStreamingEngine


async def generate():
    destination = Path('/workspace/refactor-bf-evidence/legacy-agents')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        workspace = Path(directory) / 'workspace'
        async with LargeLanguageModel(workspace, engines={'loop': FakeStreamingEngine(chunks=('legacy answer',))}) as app:
            project = app.projects.create('Legacy Agent definitions', components=['agents'])
            project.components.agents.create({'engine': 'loop', 'purpose': 'legacy work',
                'system_prompt': 'Keep the original contract.', 'metadata': {'fixture': True}}, identifier='legacy')
            snapshot = project.components.agents.snapshot('legacy')
            completed = project.sessions.create('completed')
            run = await (await completed.run.submit('old request', engine='loop')).wait()
            assert run.data.status == 'completed', run.data.error
            queued = project.sessions.create('queued')
            with app.project_manager.ownership.scope():
                store = app.project_manager.sessions.conversations(queued.data)
                message = store.create(MessageRole.USER, 'old queued request', MessageStatus.QUEUED,
                                       metadata={'engine': 'loop', 'engine_options': {}})
            manifest = {'baseline_sha': EXPECTED, 'domain_storage_version': 1,
                'agent_storage_version': 'legacy raw definition, no envelope', 'project_id': project.id,
                'agent_id': 'legacy', 'agent_snapshot': snapshot, 'completed_session_id': completed.id,
                'completed_run_id': run.id, 'queued_session_id': queued.id, 'queued_message_id': message.id}
        manifest['files'] = {str(path.relative_to(workspace)): hashlib.sha256(path.read_bytes()).hexdigest()
                             for path in sorted(workspace.rglob('*')) if path.is_file()}
        with tarfile.open(destination / 'workspace.tar.gz', 'w:gz') as archive:
            for path in sorted(workspace.rglob('*')):
                archive.add(path, arcname=str(path.relative_to(workspace)), recursive=False)
        (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({key: value for key, value in manifest.items() if key != 'files'}, ensure_ascii=False))
        print('files:', len(manifest['files']))


asyncio.run(generate())
