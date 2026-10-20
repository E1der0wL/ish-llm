"""AST ratchet for the clean refactor, with explicit outstanding audit debt.

No blanket file exemptions and no baseline-update option. The baseline records
individual AST occurrences, not permission to repeat a pattern. ``--require``
selects stage exit rules; ``--strict`` requires all candidates to be resolved.
These bounded syntactic rules do not prove dynamic data flow or safety.
"""

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path
import re


RULES = {
    "S1": "Retired API/legacy reader: remove definition and actual consumers (D06-D11)",
    "S2": "Presence/policy fallback: classify owner and Missing/Null/Value contract (D01,D13)",
    "S3": "Inline domain declaration: move to owning declaration or justify technical value (D03-D05)",
    "S4": "Storage version comparison: exact type and current reader/writer contract (D02)",
    "S5": "Naming: current semantic vocabulary and Python spelling (D14)",
    "S6": "Provider wire interpretation outside Adapter: migrate in A (D12)",
}
RETIRED = {
    "upgrade_backup", "aupgrade_backup", "on_event", "on_run_event", "from_preferences",
    "mouse_mode", "recovery_max_attempts", "provider_error_details",
}
OLD_NAMES = {
    "SettingsLayout", "settings_name", "settings_layout", "ServiceConfig",
    "ToolPolicy", "tool_policy", "OutputPolicy", "output_policy", "LogSettings",
    "validate_settings", "from_settings", "configuration_schema",
    "effective_configuration", "settings",
}
DOMAIN_FIELDS = {
    "version", "schema_version", "storage_version", "status", "state",
    "event_type", "error_code", "isolation", "effect", "approval",
}
CONFIG_NAMES = {"config", "configuration", "policy", "policies", "session_config",
                "agent", "host", "retry", "limits"}
WIRE_FIELDS = {"choices", "tool_calls", "finish_reason", "function_call"}
SDK_MODULES = {"litellm", "openai", "anthropic"}


def symbol(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def policy_name(name):
    return (name in CONFIG_NAMES or name.endswith(("_policy", "_config", "_timeout"))
            or name.startswith(("max_", "min_", "timeout", "retry_")))


def domain_name(name):
    return name in DOMAIN_FIELDS or policy_name(name) or name.endswith(("_path", "_dir"))


def literal(node):
    if isinstance(node, ast.Constant):
        return node.value is not None
    return isinstance(node, (ast.List, ast.Tuple, ast.Set)) and any(literal(item) for item in node.elts)


def scan_source(source, file):
    tree = ast.parse(source, filename=file)
    findings, occurrences = [], Counter()

    def add(rule, node, owner, detail):
        normalized = ast.dump(node, include_attributes=False)
        identity = json.dumps([file, owner, rule, detail, normalized], ensure_ascii=False)
        digest = hashlib.sha256(identity.encode()).hexdigest()
        occurrences[digest] += 1
        findings.append({"id": f"{digest}:{occurrences[digest]}", "rule": rule,
                         "file": file, "symbol": owner or "<module>", "line": node.lineno,
                         "detail": detail, "source": ast.get_source_segment(source, node)})

    def visit(node, owner=""):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = ".".join(filter(None, (owner, node.name)))
            if node.name in RETIRED or (file == "llm/services/infrastructure/backups.py"
                                       and node.name == "upgrade"):
                add("S1", node, owner, "retired definition")
            if file.startswith("llm/") and node.name in OLD_NAMES:
                add("S5", node, owner, "retired runtime vocabulary")
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not re.fullmatch(r"_*[a-z][a-z0-9_]*|__[a-z][a-z0-9_]*__", node.name):
                    add("S5", node, owner, "callable is not snake_case")
                args = [*node.args.posonlyargs, *node.args.args]
                defaults = [*zip(args[len(args) - len(node.args.defaults):], node.args.defaults),
                            *zip(node.args.kwonlyargs, node.args.kw_defaults)]
                for arg, default in defaults:
                    if policy_name(arg.arg) and default is not None and literal(default):
                        add("S2", default, owner, f"policy argument default: {arg.arg}")
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            names = {symbol(part) for part in ast.walk(node)}
            if any(policy_name(name) for name in names):
                add("S2", node, owner, "truthiness fallback involving configuration")
        if isinstance(node, ast.Call):
            if symbol(node.func) in RETIRED:
                add("S1", node, owner, "retired call")
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "setdefault"):
                if len(node.args) >= 2 and policy_name(symbol(node.args[0])) and literal(node.args[1]):
                    add("S2", node, owner, "policy lookup supplies a literal fallback")
            for keyword in node.keywords:
                if keyword.arg in RETIRED:
                    add("S1", keyword, owner, "retired callback argument")
                if domain_name(keyword.arg or "") and literal(keyword.value):
                    add("S3", keyword, owner, f"inline domain argument: {keyword.arg}")
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if domain_name(symbol(key)) and literal(value):
                    add("S3", value, owner, f"inline domain field: {symbol(key)}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if domain_name(symbol(target)) and literal(node.value):
                    add("S3", node, owner, f"inline domain assignment: {symbol(target)}")
        if isinstance(node, ast.Compare):
            if any(symbol(part) in {"version", "schema_version", "storage_version"}
                   for part in ast.walk(node)):
                add("S4", node, owner, "verify exact version type and declaration reference")
            if (any(domain_name(symbol(part)) for part in ast.walk(node))
                    and any(literal(part) for part in node.comparators)):
                add("S3", node, owner, "inline domain comparison")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in RETIRED:
            # Only code keys, never docstrings; old names in documentation are historical evidence.
            add("S1", node, owner, "retired field or dynamic API lookup")
        if isinstance(node, (ast.Name, ast.Attribute, ast.arg)):
            name = node.arg if isinstance(node, ast.arg) else symbol(node)
            if name in RETIRED:
                add("S1", node, owner, "retired reference or argument")
        if isinstance(node, (ast.Name, ast.Attribute, ast.arg)) and file.startswith("llm/"):
            name = node.arg if isinstance(node, ast.arg) else symbol(node)
            if name in OLD_NAMES:
                add("S5", node, owner, "retired runtime vocabulary")
        if not file.startswith("llm/providers/"):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [node.module or ""] if isinstance(node, ast.ImportFrom) else [item.name for item in node.names]
                if any(module.split(".")[0] in SDK_MODULES for module in modules):
                    add("S6", node, owner, "SDK import outside Provider package")
            if isinstance(node, ast.Subscript) and symbol(node.slice) in WIRE_FIELDS:
                add("S6", node, owner, "SDK-shaped field indexing outside Provider package")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "get" and node.args and symbol(node.args[0]) in WIRE_FIELDS:
                    add("S6", node, owner, "SDK-shaped field lookup outside Provider package")
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree)
    return findings


def collect(root):
    files, findings = [], []
    for folder in ("llm", "hub"):
        for path in sorted((root / folder).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            file = path.relative_to(root).as_posix()
            files.append(file)
            findings.extend(scan_source(path.read_text(encoding="utf-8-sig"), file))
    return {"files": files, "findings": findings}


def compare(report, baseline):
    current = {item["id"] for item in report["findings"]}
    recorded = {item["id"] for item in baseline["findings"]}
    if len(recorded) != len(baseline["findings"]):
        raise ValueError("duplicate baseline finding")
    return {"new": [item for item in report["findings"] if item["id"] not in recorded],
            "stale": sorted(recorded - current),
            "outstanding": len(current & recorded)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline", type=Path, default=Path(__file__).parent / "fixtures/architecture_debt.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--require", choices=tuple(RULES), action="append", default=[])
    args = parser.parse_args()
    report = collect(args.root)
    report.update(compare(report, json.loads(args.baseline.read_text(encoding="utf-8"))))
    report["mode"] = "strict" if args.strict else "ratchet"
    report["required_rules"] = list(RULES) if args.strict else args.require
    report["required_outstanding"] = sum(item["rule"] in report["required_rules"]
                                         for item in report["findings"])
    report["rules"] = RULES
    report["limits"] = "AST candidates require owner/schema/call/reader/writer review; dynamic dispatch is not proven."
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("files", "findings")},
                     ensure_ascii=False, indent=2))
    return int(not report["files"] or bool(report["new"] or report["stale"])
               or bool(report["required_outstanding"]))


if __name__ == "__main__":
    raise SystemExit(main())
