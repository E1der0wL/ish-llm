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
    "S7": "Owned contract redeclaration: use defaults, kinds, paths and receipt/projection Owners (O1-O6)",
}
RETIRED = {
    "upgrade_backup", "aupgrade_backup", "on_event", "on_run_event", "from_preferences",
    "mouse_mode", "recovery_max_attempts", "provider_error_details",
    "RunEventPublisher", "ensure_selected",
}
OLD_NAMES = {
    "SettingsLayout", "settings_name", "settings_layout", "ServiceConfig",
    "ToolPolicy", "tool_policy", "OutputPolicy", "output_policy", "LogSettings",
    "validate_settings", "from_settings", "configuration_schema",
    "effective_configuration", "settings",
}
FORMAT_FIELDS = {"version", "schema_version", "storage_version", "format_version"}
DOMAIN_FIELDS = FORMAT_FIELDS | {
    "status", "state",
    "event_type", "error_code", "isolation", "effect", "approval",
}
CONFIG_NAMES = {"config", "configuration", "policy", "policies", "session_config",
                "agent", "host", "retry", "limits"}
WIRE_FIELDS = {"choices", "tool_calls", "finish_reason", "function_call", "thinking"}
SDK_MODULES = {"litellm", "openai", "anthropic"}

# Bounded semantic contracts, not a blanket exemption for an owning file. The
# declaration must be in the exact owner symbol; unrelated code is still scanned.
OWNED_PATHS = {
    "project.json": "ProjectPaths.record", "session.json": "SessionPaths.record",
    "run.json": "RunPaths.record", "step.json": "StepPaths.record",
    "outputs.jsonl": "RunPaths.output_path",
}
OWNED_DEFAULTS = {"index_stride", "output_index_stride"}
OWNED_KINDS = {"conversation_storage": {"file", "memory"},
               "visibility": {"user", "internal"}, "operation": {"append", "replace"}}
CHECKPOINT_OPERATIONS = {"initialize", "record"}
RESOURCE_KINDS = {"project", "session", "run", "step", "message"}
STEP_KINDS = {"llm", "tool", "shell"}


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


def predicate(node):
    """Recognize comparisons and boolean syntax, never infer a value's type.

    Names, attributes, calls (even ``any``/``isinstance``), and subscripts stay
    candidates: their bindings/return types require owner review. Children are
    still visited, so a fallback inside a comparison is not hidden.
    """
    if isinstance(node, ast.Compare):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return True
    if isinstance(node, ast.Constant) and type(node.value) is bool:
        return True
    return isinstance(node, ast.BoolOp) and all(predicate(value) for value in node.values)


def key_set_comparison(node):
    """An exact object-key check declares shape, not field values/versions."""
    if len(node.ops) != 1 or not isinstance(node.ops[0], (ast.Eq, ast.NotEq)):
        return False
    left, right = node.left, node.comparators[0]
    if isinstance(left, ast.Set):
        left, right = right, left
    return (
        isinstance(right, ast.Set) and bool(right.elts)
        and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in right.elts)
        and isinstance(left, ast.Call) and not left.keywords
        and ((isinstance(left.func, ast.Name) and left.func.id == "set" and len(left.args) == 1)
             or (isinstance(left.func, ast.Attribute) and left.func.attr == "keys" and not left.args))
    )


def exact_version_guards(tree, fields):
    """Only accept a direct OR rejection with an exact type and local constant.

    This is a syntactic proof for this shape, not whole-program data-flow proof.
    Imported versions and arbitrary helpers still require separate review.
    """
    accepted = set()

    def field(node):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "self" and node.attr in fields):
            return ast.dump(node.value, include_attributes=False), node.attr
        if isinstance(node, ast.Subscript):
            owner, key = node.value, node.slice
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "get" and len(node.args) == 1 and not node.keywords):
            owner, key = node.func.value, node.args[0]
        else:
            return None
        if isinstance(key, ast.Constant) and key.value in fields:
            return ast.dump(owner, include_attributes=False), key.value

    def terms(node):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            return [part for item in node.values for part in terms(item)]
        return [node]

    def visit(node, declarations=None):
        if isinstance(node, ast.ClassDef):
            declarations = {
                target.id for item in node.body if isinstance(item, ast.Assign)
                for target in item.targets if isinstance(target, ast.Name)
                and target.id.isupper() and isinstance(item.value, ast.Constant)
                and type(item.value.value) is int}
        if (declarations and isinstance(node, ast.If) and len(node.body) == 1
                and isinstance(node.body[0], ast.Raise) and node.body[0].exc is not None):
            types, values = {}, {}
            for part in terms(node.test):
                if not isinstance(part, ast.Compare) or len(part.ops) != 1:
                    continue
                left, right = part.left, part.comparators[0]
                if (isinstance(part.ops[0], ast.IsNot) and isinstance(right, ast.Name)
                        and right.id == "int" and isinstance(left, ast.Call)
                        and isinstance(left.func, ast.Name) and left.func.id == "type"
                        and len(left.args) == 1 and not left.keywords):
                    key = field(left.args[0])
                    if key is not None:
                        types[key] = part
                if (isinstance(part.ops[0], ast.NotEq) and isinstance(right, ast.Attribute)
                        and isinstance(right.value, ast.Name) and right.value.id in ("self", "cls")
                        and right.attr in declarations):
                    key = field(left)
                    if key is not None and right.attr == key[1].upper():
                        values[key] = part
            for key in types.keys() & values.keys():
                accepted.update((types[key], values[key]))
        for child in ast.iter_child_nodes(node):
            visit(child, declarations)

    visit(tree)
    return accepted


def scan_source(source, file):
    tree = ast.parse(source, filename=file)
    findings, occurrences = [], Counter()
    guarded_versions = exact_version_guards(tree, FORMAT_FIELDS)

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
                    if arg.arg in OWNED_DEFAULTS and default is not None and literal(default):
                        add("S7", default, owner, "journal default must reference OutputJournal.DEFAULT_INDEX_STRIDE")
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            names = {symbol(part) for part in ast.walk(node)}
            if any(policy_name(name) for name in names) and not predicate(node):
                add("S2", node, owner, "truthiness fallback involving configuration")
        if isinstance(node, ast.Call):
            if symbol(node.func) in RETIRED:
                add("S1", node, owner, "retired call")
            if symbol(node.func) == "ResourceRef":
                kinds = ([node.args[0]] if node.args else []) + [k.value for k in node.keywords if k.arg == "kind"]
                if any(isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value in RESOURCE_KINDS
                       for kind in kinds for item in ast.walk(kind)):
                    add("S7", node, owner, "built-in resource references must use ResourceKind")
            if isinstance(node.func, ast.Attribute) and node.func.attr in ("get", "setdefault"):
                if len(node.args) >= 2 and symbol(node.args[0]) == "requires_retry":
                    add("S1", node, owner, "checkpoint retry evidence must not be inferred")
                if len(node.args) >= 2 and policy_name(symbol(node.args[0])) and literal(node.args[1]):
                    add("S2", node, owner, "policy lookup supplies a literal fallback")
            for keyword in node.keywords:
                if keyword.arg in RETIRED:
                    add("S1", keyword, owner, "retired callback argument")
                if (keyword.arg == "kind" and symbol(node.func) in ("EngineEvent", "Step", "step")
                        and isinstance(keyword.value, ast.Constant) and keyword.value.value in STEP_KINDS):
                    add("S7", keyword, owner, "built-in execution Step kinds must use StepKind")
                if domain_name(keyword.arg or "") and literal(keyword.value):
                    add("S3", keyword, owner, f"inline domain argument: {keyword.arg}")
                if (keyword.arg in OWNED_KINDS and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value in OWNED_KINDS[keyword.arg]):
                    add("S7", keyword, owner, f"kind argument must reference its Owner: {keyword.arg}")
        if isinstance(node, ast.Dict):
            if file == "llm/components/skills/tools.py" and any(key is not None for key in node.keys):
                add("S7", node, owner, "Skill handlers must serialize their response Owner")
            for key, value in zip(node.keys, node.values):
                if domain_name(symbol(key)) and literal(value):
                    add("S3", value, owner, f"inline domain field: {symbol(key)}")
                kinds = OWNED_KINDS.get(symbol(key), set())
                if symbol(key) == "operation":
                    kinds = kinds | CHECKPOINT_OPERATIONS
                if isinstance(value, ast.Constant) and value.value in kinds:
                    add("S7", value, owner, "kind field must reference its Owner: " + symbol(key))
                if symbol(key) == "conversation_storage" and any(
                        isinstance(item, ast.Constant) and isinstance(item.value, str)
                        and item.value in OWNED_KINDS["conversation_storage"] for item in ast.walk(value)):
                    add("S7", value, owner, "storage choices must derive from ConversationStorage")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if (file == "llm/components/tools/registry.py" and owner == "ToolContract"
                    and isinstance(node, ast.AnnAssign) and node.value is not None):
                add("S2", node, owner, "trusted Tool execution declaration cannot supply defaults")
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if domain_name(symbol(target)) and literal(node.value):
                    add("S3", node, owner, f"inline domain assignment: {symbol(target)}")
                if symbol(target) in OWNED_DEFAULTS and node.value is not None and literal(node.value):
                    add("S7", node, owner, "journal default must reference OutputJournal.DEFAULT_INDEX_STRIDE")
        if isinstance(node, ast.Compare) and node not in guarded_versions and not key_set_comparison(node):
            if any(symbol(part) in FORMAT_FIELDS for part in ast.walk(node)):
                add("S4", node, owner, "verify exact version type and declaration reference")
            if (any(domain_name(symbol(part)) for part in ast.walk(node))
                    and any(literal(part) for part in node.comparators)):
                add("S3", node, owner, "inline domain comparison")
            names = {symbol(item) for item in ast.walk(node)}
            for name in OWNED_KINDS.keys() & names:
                kinds = OWNED_KINDS[name] | (CHECKPOINT_OPERATIONS if name == "operation" else set())
                if any(isinstance(item, ast.Constant) and isinstance(item.value, str)
                       and item.value in kinds for item in ast.walk(node)):
                    add("S7", node, owner, f"kind comparison must reference its Owner: {name}")
            if any(isinstance(item, ast.Constant) and item.value == "engine_record" for item in ast.walk(node)):
                add("S7", node, owner, "nested record discriminator must use CheckpointEnvelope")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if isinstance(node.right, ast.Constant) and node.right.value in OWNED_PATHS:
                if file != "llm/core/paths.py" or owner != OWNED_PATHS[node.right.value]:
                    add("S7", node, owner, "stored path must use the exact Paths owner property")
        if isinstance(node, ast.Subscript) or (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("get", "setdefault", "pop") and node.args):
            key = node.slice if isinstance(node, ast.Subscript) else node.args[0]
            receiver = node.value if isinstance(node, ast.Subscript) else node.func.value
            if (file == "llm/components/skills/tools.py" and (
                    symbol(receiver) not in ("records", "snapshots") or isinstance(key, ast.Constant)
                    or isinstance(key, ast.Attribute) and key.attr.isupper())):
                add("S7", node, owner, "Skill handlers must consume typed requests and snapshots")
            if (file == "llm/components/base.py" and owner in (
                    "Component._model_usage_records", "Component.save_model_usage")
                    and symbol(key) == "records"):
                add("S7", node, owner, "Component usage inventory must use ModelUsageManifest")
            if file == "llm/components/skills/storage.py" and symbol(key) in ("sha256", "bytes", "mode", "FILE_MODE"):
                add("S7", node, owner, "Skill file integrity must use SkillFileEvidence")
            if symbol(key) in ("resume", "restart") and any(
                    isinstance(item, ast.Attribute) and item.attr == "metadata" for item in ast.walk(receiver)):
                add("S7", node, owner, "Run continuation metadata must use ResumeRecord or RestartRecord")
            if symbol(key) == "completions" and not (file == "llm/core/results.py"
                    and owner.startswith("CompletionRecords.")):
                add("S7", node, owner, "completion container must use CompletionRecords")
            if (file == "llm/services/api.py" and owner == "ProjectHandle._describe_config"
                    and isinstance(key, ast.Constant)):
                add("S7", node, owner, "Project description must delegate to ProjectConfiguration")
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
    for folder in ("llm", "hub", "examples"):
        for path in sorted((root / folder).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            file = path.relative_to(root).as_posix()
            files.append(file)
            candidates = scan_source(path.read_text(encoding="utf-8-sig"), file)
            # Examples are real API consumers. Their explicit demonstration
            # policies are not backend defaults; audit retired API use here.
            findings.extend(item for item in candidates if folder != "examples" or item["rule"] == "S1")
    return {"files": files, "findings": findings}


def compare(report, baseline):
    current = {item["id"] for item in report["findings"]}
    recorded = {item["id"] for item in baseline["findings"]}
    if len(recorded) != len(baseline["findings"]):
        raise ValueError("duplicate baseline finding")
    return {"new": [item for item in report["findings"] if item["id"] not in recorded],
            "stale": sorted(recorded - current),
            "outstanding": len(current & recorded)}


def validate_reviews(report, document, root):
    """Validate individual review evidence, never infer approval from a baseline.

    Test references describe coverage; they do not claim that tests were run.
    Source/contract/test changes require a fresh review before any gate can pass.
    """
    if not isinstance(document, dict) or document.keys() != {"reviews"} or not isinstance(document["reviews"], list):
        raise ValueError("Architecture reviews require only a reviews array")
    current = {item["id"]: item for item in report["findings"]}
    root = root.resolve()
    accepted = []
    seen = set()

    def source_file(name):
        if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Review evidence must use repository-relative paths")
        path = root / name
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise ValueError(f"Missing or external review evidence: {name}")
        return path

    for entry in document["reviews"]:
        if not isinstance(entry, dict) or entry.keys() != {"id", "rule", "owner", "reason", "contracts", "sources", "tests"}:
            raise ValueError("Review requires exact occurrence, owner, reason, contracts, source hashes and tests")
        if any(not isinstance(entry[key], str) or not entry[key].strip() for key in ("id", "rule", "owner", "reason")):
            raise ValueError("Review identity and reasoning must be nonempty text")
        identifier = entry["id"]
        if identifier in seen:
            raise ValueError(f"Duplicate architecture review: {identifier}")
        seen.add(identifier)
        finding = current.get(identifier)
        if finding is None or entry["rule"] != finding["rule"] or entry["owner"] != finding["symbol"]:
            raise ValueError(f"Stale or mismatched architecture review: {identifier}")
        if entry["rule"] not in {"S2", "S3", "S4", "S5"}:
            raise ValueError("Retired API and pending Provider boundaries cannot be approved by review")
        sources = entry["sources"]
        if not isinstance(sources, dict) or finding["file"] not in sources:
            raise ValueError("Review must pin the complete candidate source file")
        for name, expected in sources.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                raise ValueError(f"Invalid review source hash: {name}")
            if hashlib.sha256(source_file(name).read_bytes()).hexdigest() != expected:
                raise ValueError(f"Changed review evidence requires re-review: {name}")
        for kind in ("contracts", "tests"):
            values = entry[kind]
            if (not isinstance(values, list) or not values
                    or any(not isinstance(value, str) or not value.strip() for value in values)
                    or len(set(values)) != len(values)):
                raise ValueError(f"Review requires unique {kind} evidence")
        for contract in entry["contracts"]:
            if contract not in sources or not contract.endswith(".md"):
                raise ValueError("Review contracts must be pinned Markdown files")
        for test in entry["tests"]:
            parts = test.split(".")
            if len(parts) < 4 or parts[0] != "tests" or not parts[-1].startswith("test_"):
                raise ValueError(f"Review requires a concrete test ID: {test}")
            name = "/".join(parts[:-2]) + ".py"
            if name not in sources:
                raise ValueError(f"Review must pin its coverage test: {test}")
            tree = ast.parse(source_file(name).read_text(encoding="utf-8-sig"))
            if not any(isinstance(cls, ast.ClassDef) and cls.name == parts[-2]
                       and any(isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name == parts[-1]
                               for method in cls.body) for cls in tree.body):
                raise ValueError(f"Missing review coverage test: {test}")
        accepted.append(entry)
    return accepted


def unresolved_findings(report, reviews):
    reviewed = {entry["id"] for entry in reviews}
    return [entry for entry in report["findings"] if entry["id"] not in reviewed]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--baseline", type=Path, default=Path(__file__).parent / "fixtures/architecture_debt.json")
    parser.add_argument("--reviews", type=Path, help="Individual current-source review evidence; no blanket exemptions")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--require", choices=tuple(RULES), action="append", default=[])
    args = parser.parse_args()
    report = collect(args.root)
    path = args.reviews if args.reviews is not None else args.root / "tests/llm/fixtures/architecture_reviews.json"
    report["reviewed"], report["review_errors"] = [], []
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() or args.reviews is not None else {"reviews": []}
        report["reviewed"] = validate_reviews(report, document, args.root)
    except (OSError, ValueError, SyntaxError) as error:
        report["review_errors"].append(str(error))
    unresolved = unresolved_findings(report, report["reviewed"])
    report.update(compare({"findings": unresolved}, json.loads(args.baseline.read_text(encoding="utf-8"))))
    report["mode"] = "strict" if args.strict else "ratchet"
    report["required_rules"] = list(RULES) if args.strict else args.require
    report["required_outstanding"] = sum(item["rule"] in report["required_rules"]
                                         for item in unresolved)
    report["rules"] = RULES
    report["limits"] = "AST candidates require owner/schema/call/reader/writer review; dynamic dispatch is not proven."
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(json.dumps({**{key: value for key, value in report.items() if key not in ("files", "findings", "reviewed")},
                      "reviewed": len(report["reviewed"])},
                     ensure_ascii=False, indent=2))
    return int(not report["files"] or bool(report["new"] or report["stale"])
               or bool(report["required_outstanding"]) or bool(report["review_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
