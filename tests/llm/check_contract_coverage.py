"""Inventory evidence for the *whole* ownership audit, not a literal violation counter.

No automatic approvals or baseline update. Reporting can succeed with outstanding
work; --require-complete rejects every unreviewed/stale file, candidate or path.
The existing architecture and functional safety gates remain independently required.
"""

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from tests.source_tree import source_bytes


ROOTS = ('llm', 'hub')
CLASSIFICATIONS = {'declaration', 'non_contract', 'violation'}
REVIEW_FIELDS = {'path', 'sha256', 'decisions', 'contracts', 'dynamic_paths_reviewed', 'reason'}
DECISION_FIELDS = {'id', 'classification', 'owner', 'reason'}
CONTRACT_FIELDS = {'owner', 'declaration', 'consumers', 'tests', 'complete'}


def product_files(root):
    """Current tracked/new product files, including assets; deleted reviews are orphans."""
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', *ROOTS],
                            cwd=root, check=True, capture_output=True)
    deleted = subprocess.run(['git', 'ls-files', '--deleted', '-z', '--', *ROOTS],
                             cwd=root, check=True, capture_output=True)
    return sorted(set(result.stdout.decode().split('\0')) - set(deleted.stdout.decode().split('\0')) - {''})


def candidates(source):
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    docstrings = {node.body[0].value for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str)}
    result = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.MatchSingleton)) or node in docstrings:
            continue
        ancestors, parent = [], parents.get(node)
        while parent is not None:
            ancestors.append(parent)
            parent = parents.get(parent)
        scope = [item for item in reversed(ancestors)
                 if isinstance(item, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        runtime = any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) for item in ancestors)
        declaration = False
        if not runtime:
            container = next((item for item in ancestors if isinstance(item, (ast.Module, ast.ClassDef))), None)
            if container is not None:
                statement = next(item for item in ancestors if parents.get(item) is container)
                earlier = container.body[:container.body.index(statement)] if statement in container.body else []
                boundary = (ast.FunctionDef, ast.AsyncFunctionDef) if isinstance(container, ast.ClassDef) else (
                    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                declaration = isinstance(statement, (ast.Assign, ast.AnnAssign)) and not any(
                    isinstance(item, boundary) for item in earlier)
        role = next((type(item).__name__ for item in ancestors if isinstance(item,
                    (ast.Dict, ast.Subscript, ast.Compare, ast.Call, ast.arguments, ast.Match, ast.JoinedStr))),
                    type(parents.get(node)).__name__)
        descriptor = {'line': node.lineno, 'column': node.col_offset,
                      'symbol': '.'.join(item.name for item in scope), 'role': role,
                      'area': 'declaration' if declaration else 'implementation',
                      'literal': ast.dump(node, include_attributes=False)}
        descriptor['id'] = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()
        result.append(descriptor)
    return sorted(result, key=lambda item: (item['line'], item['column']))


def inspect(root, paths, reviews):
    """Evidence is exact-file-bound; a review cannot silently cover a changed consumer."""
    issues, index = [], {}
    for review in reviews:
        if not isinstance(review, dict) or review.keys() != REVIEW_FIELDS:
            issues.append('invalid review structure')
            continue
        path = review['path']
        if path in index:
            issues.append('duplicate review: ' + path)
        index[path] = review
    files = []
    counts = Counter(files=len(paths), unreviewed_files=0, stale_files=0, unclassified=0,
                     violations=0, incomplete_contracts=0, unreviewed_dynamic_paths=0, unreadable_files=0)
    for path in paths:
        target = root / path
        item = {'path': path, 'status': 'unreviewed', 'candidates': []}
        try:
            if target.is_symlink():
                raise ValueError('symlink requires explicit external-source scope')
            raw = source_bytes(target)
            item['sha256'] = hashlib.sha256(raw).hexdigest()
            if target.suffix == '.py':
                item['candidates'] = candidates(raw.decode())
        except (OSError, UnicodeError, SyntaxError, ValueError) as error:
            item['error'] = str(error)
            counts['unreadable_files'] += 1
        current = {entry['id']: entry for entry in item['candidates']}
        review = index.get(path)
        usable = review is not None and review['sha256'] == item.get('sha256') and 'error' not in item
        if review is None:
            counts['unreviewed_files'] += 1
        elif not usable:
            item['status'] = 'stale'
            counts['stale_files'] += 1
        decisions = {}
        if usable:
            item['status'] = 'reviewed'
            if not isinstance(review['reason'], str) or not review['reason'].strip():
                issues.append('missing review reason: ' + path)
            for decision in review['decisions']:
                if (not isinstance(decision, dict) or decision.keys() != DECISION_FIELDS
                        or decision['classification'] not in CLASSIFICATIONS
                        or not all(isinstance(decision[key], str) and decision[key].strip() for key in ('id', 'owner', 'reason'))):
                    issues.append('invalid decision: ' + path)
                    continue
                identifier = decision['id']
                if identifier in decisions or identifier not in current:
                    issues.append('duplicate or orphan decision: ' + path + ':' + identifier)
                    continue
                if decision['classification'] == 'declaration' and current[identifier]['area'] != 'declaration':
                    issues.append('implementation literal declared exempt: ' + path + ':' + identifier)
                    continue
                decisions[identifier] = decision
            for contract in review['contracts']:
                if (not isinstance(contract, dict) or contract.keys() != CONTRACT_FIELDS
                        or not all(contract[key] for key in ('owner', 'declaration', 'consumers', 'tests'))):
                    issues.append('incomplete contract evidence: ' + path)
                    counts['incomplete_contracts'] += 1
                elif contract['complete'] is not True:
                    counts['incomplete_contracts'] += 1
        counts['unclassified'] += len(current.keys() - decisions.keys())
        counts['violations'] += sum(value['classification'] == 'violation' for value in decisions.values())
        if not usable or review['dynamic_paths_reviewed'] is not True:
            counts['unreviewed_dynamic_paths'] += 1
        files.append(item)
    for path in index.keys() - set(paths):
        issues.append('orphan file review: ' + path)
    if not paths:
        issues.append('empty product inventory')
    counts['candidates'] = sum(len(item['candidates']) for item in files)
    counts['issues'] = len(issues)
    outstanding = set(counts) - {'files', 'candidates'}
    return {'complete': not any(counts[key] for key in outstanding), 'counts': dict(counts),
            'issues': issues, 'files': files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--reviews', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    reviews = json.loads(args.reviews.read_text()) if args.reviews else []
    report = inspect(args.root, product_files(args.root), reviews)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'complete': report['complete'], **report['counts']}))
    return int(args.require_complete and not report['complete'])


if __name__ == '__main__':
    raise SystemExit(main())
