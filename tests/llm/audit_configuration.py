"""정적 감사 후보를 수집한다. 통과 판정기가 아니며 각 행은 코드 문맥 검토가 필요하다.

python -m tests.llm.audit_configuration --output /tmp/configuration-audit.json
"""
import argparse
import ast
import json
from pathlib import Path
import re

PATTERNS = {
    "default_helpers": r"_defaults|default_configuration|provider_defaults|search_defaults|extraction_defaults",
    "setdefault": r"setdefault\(",
    "numeric_fallback": r"\.get\([^,]+,\s*[0-9]|\bor\s+[0-9]",
    "timeout_assignment": r"timeout.*=",
    "limit_assignment": r"max_.*=",
    "schema_default": r'["\']default["\']\s*:',
    "numeric_field": r"field\(.*,[ \t]*[0-9]",
}


def collect(root):
    findings = []
    files = []
    for path in sorted(root.rglob("*.py")):
        if {"tests", "examples", "__pycache__"} & set(path.relative_to(root).parts):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        files.append(path.relative_to(root).as_posix())
        candidates = {}
        for line, text in enumerate(source.splitlines(), 1):
            matches = [name for name, pattern in PATTERNS.items() if re.search(pattern, text)]
            if matches:
                candidates[line] = matches
        # 숫자뿐 아니라 문자열/boolean fallback과 public API 기본 인자도 감사한다.
        # None/빈 container의 중립 상태는 문맥 검토에 맡긴다.
        for node in ast.walk(tree):
            values = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                values = [(v, "api_default") for v in (*node.args.defaults, *node.args.kw_defaults)]
            elif isinstance(node, ast.AnnAssign):
                values = [(node.value, "initial_value")]
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "setdefault") and len(node.args) > 1:
                values = [(node.args[1], "literal_fallback")]
            for value, category in values:
                if isinstance(value, ast.Constant) and value.value is not None:
                    names = candidates.setdefault(value.lineno, [])
                    if category not in names:
                        names.append(category)
        lines = source.splitlines()
        for line, matches in sorted(candidates.items()):
            findings.append({"file": files[-1], "line": line, "patterns": matches, "text": lines[line - 1].strip()})
    return {"scope": "all production Python under llm; fixtures/examples are explicit callers",
            "files": files, "patterns": PATTERNS, "findings": findings,
            "review": "See docs/llm/configuration-audit.md for contextual classification; grep alone is not proof."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = collect(Path(__file__).resolve().parents[2] / 'llm')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(report['files'])} production files, {len(report['findings'])} review candidates")


if __name__ == "__main__":
    main()
