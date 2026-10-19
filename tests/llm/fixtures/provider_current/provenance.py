"""Record exactly which implementation produced the synthetic archives."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def archive_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provenance():
    root = Path(__file__).resolve().parents[4]
    paths = sorted([*root.glob('llm/**/*.py'), *root.glob('hub/**/*.py'),
                    *Path(__file__).parent.glob('*.py'),
                    root / 'tests/llm/support/runtime_tools.py', root / 'tests/llm/test_loop.py'])
    hashes = {str(path.relative_to(root)): archive_hash(path) for path in paths}
    from llm.core.models import RUN_STORAGE_VERSION, SESSION_STORAGE_VERSION
    from llm.services.runtime.agents import RunAgentResolver
    from llm.services.lifecycle.agents import AgentRepository
    return {
        'fixture_schema': 1, 'python': sys.version,
        'source_base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'source_tree_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
        'source_files': hashes,
        'source_has_uncommitted_changes': subprocess.run(
            ['git', 'diff', '--quiet', '--', 'llm', 'hub', 'tests'], cwd=root).returncode != 0,
        'storage_versions': {'run': RUN_STORAGE_VERSION, 'session': SESSION_STORAGE_VERSION,
                             'agent': AgentRepository.storage_version, 'binding': RunAgentResolver.version,
                             'model': 1},
        'selection': 'explicit_project_agent',
    }
