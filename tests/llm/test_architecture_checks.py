"""Positive and negative fixtures for bounded AST gates, not product mocks."""

import ast
from pathlib import Path
import unittest

from tests.llm.check_architecture import collect, compare, exact_version_guards, scan_source


class ArchitectureCheckTests(unittest.TestCase):
    def rules(self, source, file="llm/example.py"):
        return {item["rule"] for item in scan_source(source, file)}

    def test_detects_each_rule(self):
        cases = {
            "S1": "backend.projects.upgrade_backup(source, destination, transform=change)",
            "S2": "policy = policy or {}",
            "S3": "request = {'max_tool_calls': 1}",
            "S4": "if record['schema_version'] != 1: raise ValueError()",
            "S5": "def validate_settings(data): pass",
            "S6": "import litellm\nresponse['choices'][0]",
        }
        for rule, source in cases.items():
            with self.subTest(rule=rule):
                self.assertIn(rule, self.rules(source))
        self.assertIn("S4", self.rules("if record['format_version'] != 1: raise ValueError()"))

    def test_retired_alias_argument_and_dynamic_lookup_are_detected(self):
        for source in ("old = backend.on_event", "def setup(on_run_event=None): pass",
                       "old = getattr(backend, 'upgrade_backup')",
                       "theme = HubTheme.from_preferences(record)",
                       "class RunEventPublisher: pass",
                       "await builtin.ensure_selected(project)",
                       "value.get('requires_retry', key.startswith('tool:'))",
                       "value.setdefault('requires_retry', False)"):
            with self.subTest(source=source):
                self.assertIn("S1", self.rules(source))
        self.assertNotIn("S1", self.rules("type(value.get('requires_retry')) is bool"))

    def test_example_consumers_are_included_in_retired_api_gate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sample = root / "examples/llm/demo.py"
            sample.parent.mkdir(parents=True)
            sample.write_text("backend = LargeLanguageModel(on_event=display)\n")
            report = collect(root)
            self.assertIn("examples/llm/demo.py", report["files"])
            self.assertTrue(report["findings"])
            self.assertEqual({item["rule"] for item in report["findings"]}, {"S1"})
            sample.write_text("backend.events.subscribe(display, channel='engine')\n")
            self.assertEqual(collect(root)["findings"], [])

    def test_cli_ratchet_is_not_a_zero_debt_claim(self):
        import json
        import subprocess
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "llm").mkdir()
            source = "policy = policy or {}"
            (root / "llm/example.py").write_text(source)
            baseline = root / "baseline.json"
            baseline.write_text(json.dumps({"findings": scan_source(source, "llm/example.py")}))
            command = [sys.executable, "-m", "tests.llm.check_architecture",
                       "--root", str(root), "--baseline", str(baseline)]
            for arguments, expected in (([], 0), (["--strict"], 1),
                                        (["--require", "S2"], 1), (["--require", "S6"], 0)):
                with self.subTest(arguments=arguments):
                    result = subprocess.run([*command, *arguments], capture_output=True, text=True,
                                            cwd=Path(__file__).resolve().parents[2], timeout=30)
                    self.assertEqual(result.returncode, expected, result.stdout + result.stderr)

    def test_owner_references_presence_and_real_adapter_are_allowed(self):
        source = ("MAX_CALLS = 1\n"
                  "if policy is None:\n    policy = {}\n"
                  "request = {'max_calls': MAX_CALLS}\n"
                  "value = config['max_calls'] if 'max_calls' in config else None\n")
        self.assertEqual(self.rules(source), set())
        self.assertEqual(self.rules("import litellm\nresult = response['choices'][0]",
                                    "llm/providers/real.py"), set())
        self.assertEqual(self.rules("class SettingsService: pass", "hub/services/settings.py"), set())

    def test_validation_predicates_are_not_value_fallbacks(self):
        for source in (
            "type(max_calls) is not int or max_calls < 1",
            "policy is None or policy.get('max_calls') is None",
            "not isinstance(config, dict) or len(config) == 0",
            "config is None or (max_calls is not None and max_calls < 1)",
            "retry is None or not (retry.allowed() or retry.required())",
        ):
            with self.subTest(source=source):
                # The last expression still contains an unproven nested OR.
                if "retry.allowed" in source:
                    self.assertIn("S2", self.rules(source))
                else:
                    self.assertNotIn("S2", self.rules(source))

    def test_predicate_context_does_not_hide_nested_or_unproven_fallbacks(self):
        for source in (
            "if config or True: execute()",
            "if (policy or {}) == {} or max_calls is None: execute()",
            "if not (policy or {}): execute()",
            "value = policy is None or policy",
            "value = policy or policy is None",
            "value = isinstance(config, dict) or config",
            "value = any(config) or policy is None",
            "value = limits.ready or max_calls is None",
            "value = config['enabled'] or policy is None",
        ):
            with self.subTest(source=source):
                self.assertIn("S2", self.rules(source))

    def test_key_shape_is_distinct_from_domain_value_comparison(self):
        for source in ("set(record) != {'version', 'config'}",
                       "{'status', 'version'} == record.keys()"):
            with self.subTest(source=source):
                self.assertEqual(self.rules(source), set())
        for source in ("record['version'] != 1",
                       "record.get('version') != {'version', 'config'}",
                       "set(record) != record['version']"):
            with self.subTest(source=source):
                self.assertIn("S4", self.rules(source))
        self.assertIn("S2", self.rules("set(config or {}) != {'version', 'config'}"))

    def test_mutation_cannot_hide_in_recorded_file_or_move_to_another_owner(self):
        old = scan_source("policy = policy or {}", "llm/example.py")
        baseline = {"findings": old}
        changed = scan_source("policy = policy or {}\nlimits = limits or {}", "llm/example.py")
        result = compare({"findings": changed}, baseline)
        self.assertEqual(len(result["new"]), 1)
        self.assertEqual(result["outstanding"], 1)
        moved = scan_source("def resolve():\n    policy = policy or {}", "llm/example.py")
        self.assertEqual(len(compare({"findings": moved}, baseline)["new"]), 1)

    def test_whitespace_is_stable_but_changed_default_is_not(self):
        old = scan_source("count = policy.get('max_calls', 1)", "llm/example.py")
        spaced = scan_source("\ncount = policy.get( 'max_calls', 1 )", "llm/example.py")
        self.assertEqual([item["id"] for item in old], [item["id"] for item in spaced])
        changed = scan_source("count = policy.get('max_calls', 2)", "llm/example.py")
        self.assertTrue(compare({"findings": changed}, {"findings": old})["new"])

    def test_removed_and_duplicated_debt_cannot_silently_pass(self):
        old = scan_source("policy = policy or {}", "llm/example.py")
        self.assertTrue(compare({"findings": []}, {"findings": old})["stale"])
        doubled = scan_source("policy = policy or {}\npolicy = policy or {}", "llm/example.py")
        self.assertEqual(len(compare({"findings": doubled}, {"findings": old})["new"]), 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            compare({"findings": old}, {"findings": old + old})

    def test_scan_covers_both_product_trees_and_ratchet(self):
        import json
        root = Path(__file__).resolve().parents[2]
        result = collect(root)
        self.assertTrue(any(path.startswith("llm/") for path in result["files"]))
        self.assertTrue(any(path.startswith("hub/") for path in result["files"]))
        baseline = json.loads((Path(__file__).parent / "fixtures/architecture_debt.json").read_text())
        compared = compare(result, baseline)
        self.assertEqual(compared["new"], [])
        self.assertEqual(compared["stale"], [])


class VersionGuardTests(unittest.TestCase):
    def test_rejects_mutated_type_owner_or_boolean_logic(self):
        source = """
class Store:
    FORMAT_VERSION = 1
    def read(self, record):
        if type(record.get("format_version")) is not int or record["format_version"] != self.FORMAT_VERSION:
            raise ValueError("Unsupported current format")
"""
        self.assertEqual(len(exact_version_guards(ast.parse(source), {"format_version"})), 2)
        self.assertEqual(scan_source(source, "llm/example.py"), [])
        changes = [
            ("type(record.get", "isinstance(record.get"),
            ('type(record.get("format_version")) is not int or ', ""),
            (" is not int", " is int"),
            (" or ", " and "),
            ("self.FORMAT_VERSION", "other.FORMAT_VERSION"),
            ("self.FORMAT_VERSION", "self.BUFFER_SIZE"),
            ("self.FORMAT_VERSION", "1"),
            ('record["format_version"]', 'other["format_version"]'),
            ("FORMAT_VERSION = 1", "FORMAT_VERSION = True"),
            ('raise ValueError("Unsupported current format")', "pass"),
        ]
        for old, new in changes:
            with self.subTest(old=old, new=new):
                changed = source.replace(old, new)
                self.assertEqual(exact_version_guards(ast.parse(changed), {"format_version"}), set())
                self.assertIn("S4", {item["rule"] for item in scan_source(changed, "llm/example.py")})
