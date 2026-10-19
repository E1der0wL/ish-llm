"""Run with PYTHONPATH pointing to the verified baseline archive, as generate_graph.py."""
import hashlib
import json
from pathlib import Path
import sys

from llm.components.tools import Tool, ToolRegistry
import llm.components.tools.registry as registry

async def act(arguments): return None

if __name__ == '__main__':
    target = Path(sys.argv[1])
    source = Path(registry.__file__).read_bytes()
    manifest = json.loads((target / 'graph_manifest.json').read_text())
    assert hashlib.sha256(source).hexdigest() == manifest['source_hashes']['llm/components/tools/registry.py']
    definitions = []
    for extras in ({}, {'strict': None}, {'strict': False, 'description': ''}):
        definition = {'type': 'function', 'function': {'name': 'act', 'parameters': {'type': 'object'}, **extras}}
        definitions.append(ToolRegistry((Tool('act', '', {'type': 'object'}, act, definition=definition),)).definitions()[0])
    (target / 'definitions.json').write_text(json.dumps({'baseline': manifest['baseline'],
        'registry_sha256': hashlib.sha256(source).hexdigest(), 'definitions': definitions}, indent=2)+'\n')
