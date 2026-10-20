"""Positive and negative fixtures for bounded AST gates, not product mocks."""

from pathlib import Path
import unittest

from tests.llm.check_architecture import collect, compare, scan_source


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

    def test_retired_alias_argument_and_dynamic_lookup_are_detected(self):
        for source in ("old = backend.on_event", "def setup(on_run_event=None): pass",
                       "old = getattr(backend, 'upgrade_backup')",
                       "theme = HubTheme.from_preferences(record)"):
            with self.subTest(source=source):
                self.assertIn("S1", self.rules(source))

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
