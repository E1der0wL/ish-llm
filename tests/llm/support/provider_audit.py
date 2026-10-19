"""Reproduce stage-A ownership inventory; report new paths for manual classification.

Run: python -m tests.llm.support.provider_audit
This inventory does not infer correctness from spelling. Categories were reviewed
against the callers documented in llm/providers/REFACTOR_A.md.
"""
import ast
from collections import Counter
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
SEARCH = re.compile(r'CompletionMessage|CompletionRequest|tool_calls|tool_call_id|function_call|tool_choice|choices|relevance_score|embedding|to_kwargs|image_url|response_format|finish_reason|litellm')
LITERALS = {'choices', 'delta', 'tool_calls', 'tool_call_id', 'relevance_score', 'image_url',
            'function_call', 'tool_choice', 'response_format', 'finish_reason', 'function',
            'role', 'content', 'messages', 'embedding', 'results'}
COMMON = {
    'core/model_protocol.py', 'components/processing.py', 'engines/base.py', 'engines/loop/engine.py',
}
COMPAT = {
    'llm.py', 'core/results.py', 'components/memory/compaction.py', 'components/memory/processing.py',
    'components/rag/_client.py', 'components/rag/embedding.py', 'components/rag/rerank.py',
    'components/vision/backends.py', 'components/vision/data.py',
}
DOMAIN = {
    'services/runtime/transfers.py',  # Durable Session transport and detached public projections.
    'core/agents.py',  # Persisted Agent schema and its content revision, moved out of Component ownership.
    'components/agents/component.py', 'engines/agents.py', 'components/memory/component.py',
    'components/memory/data.py', 'components/memory/tools.py', 'components/memory/work_state.py',
    'components/prompts/component.py', 'components/rag/__init__.py', 'components/rag/component.py',
    'components/rag/data.py', 'components/rag/extraction.py', 'components/rag/ingestion.py',
    'components/rag/jobs.py', 'components/rag/prompts.py', 'components/rag/search.py',
    'components/rag/splitting.py', 'components/rag/indexing.py', 'components/refinement/component.py',
    'components/refinement/data.py', 'components/tools/builtin/catalog.py', 'components/tools/builtin/files.py',
    'core/policies.py', 'services/history/conversation.py', 'services/infrastructure/journal.py',
    'services/lifecycle/sessions.py', 'services/results.py', 'services/runtime/runs.py',
}


def inventory():
    rows = []
    for path in sorted((ROOT / 'llm').rglob('*.py')):
        source = path.read_text()
        lines = {i for i, line in enumerate(source.splitlines(), 1) if SEARCH.search(line)}
        lines.update(node.lineno for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)
                     and isinstance(node.value, str) and node.value in LITERALS)
        if not lines:
            continue
        relative = str(path.relative_to(ROOT / 'llm'))
        category = ('Provider wire / adapter' if relative.startswith('providers/') else
                    'Common contract / explicit compatibility boundary' if relative in COMMON else
                    'Compatibility / public API' if relative in COMPAT else
                    'Persisted domain / application data' if relative in DOMAIN else 'UNCLASSIFIED')
        rows.append({'path': 'llm/' + relative, 'category': category, 'matched_lines': len(lines)})
    return {'files': len(rows), 'matched_lines': sum(r['matched_lines'] for r in rows),
            'categories': dict(Counter(r['category'] for r in rows)),
            'unclassified': [r['path'] for r in rows if r['category'] == 'UNCLASSIFIED'], 'inventory': rows}


if __name__ == '__main__':
    result = inventory()
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result['unclassified']))
