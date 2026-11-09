"""Completion evidence, token scopes and durable user-directed transitions."""
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from dok.features.completion.analysis import HelpAnalysis
from dok.features.completion.models import Analysis, Argument, Candidate, Catalog, ItemKind, Join, MergeMode, Registration
from dok.features.completion.store import CompletionStore


class CompletionTests(unittest.TestCase):
    def test_generated_suggestions_sort_after_inheritance_without_changing_records(self):
        original = (Candidate("--Zoo"), Candidate("-b"), Candidate("--alpha"), Candidate("-A"))
        root = Registration(("tool",), items=original)
        sub = Registration(("tool", "sub"), items=(Candidate("--beta"), Candidate("--alpha", help="specific"),
            Candidate("-a"), *(Candidate(name, kind=ItemKind.COMMAND)
                              for name in ("do_more", "do:more", "do.more", "do-more"))))
        catalog = Catalog(registrations=(root, sub))
        suggestions = catalog.suggestions(("tool", "sub"), "")
        self.assertEqual([item[0] for item in suggestions],
                         ["--alpha", "--beta", "--Zoo", "-A", "-a", "-b", "do-more", "do.more", "do:more", "do_more"])
        self.assertEqual(suggestions[0][1], "specific")
        self.assertEqual(catalog.get(("tool",)).items, original)
        for join, words, current in ((Join.SPACE, ("tool", "--mode"), ""), (Join.EQUAL, ("tool",), "--mode=")):
            option = Candidate("--mode", argument=Argument.REQUIRED, join=join,
                               values=("Zebra", "alpha", "Beta", "-fast", "--slow", "+auto", "_auto"))
            choices = Catalog(registrations=(Registration(("tool",), items=(option,)),)).suggestions(words, current)
            self.assertEqual([item[0] for item in choices],
                             [current + value for value in ("+auto", "--slow", "-fast", "_auto", "alpha", "Beta", "Zebra")])

    def test_merge_and_replace_are_prefix_scoped(self):
        a, b, c = Candidate("--one"), Candidate("--two"), Candidate("--three")
        registration = Registration(("tool", "list"), "tool docs", (a, b))
        changed = replace(b, help="updated")
        merged = registration.apply(Analysis((changed, c)), MergeMode.MERGE, "tool docs")
        self.assertEqual(merged.items, (a, changed, c))
        self.assertEqual(registration.apply(Analysis((c,)), MergeMode.MERGE, "docs").items, (a, b, c))
        self.assertEqual(registration.apply(Analysis((c,)), MergeMode.REPLACE, "docs").items, (c,))
        other = Registration(("other",), items=(c,))
        catalog = Catalog(registrations=(registration, other))
        updated = catalog.put(registration.apply(Analysis((c,)), MergeMode.REPLACE, "new docs"))
        self.assertEqual(updated.get(other.prefix), other)
        self.assertEqual(updated.get(registration.prefix).items, (c,))

    def test_token_scope_option_values_and_end_of_options(self):
        root = Registration(("git",), items=(Candidate("-C", argument=Argument.REQUIRED),
            Candidate("log", kind=ItemKind.COMMAND), Candidate("--version")))
        sub = Registration(("git", "log"), items=(Candidate("--graph"), Candidate("--color", argument=Argument.REQUIRED,
            join=Join.EQUAL, values=("always", "never"))))
        catalog = Catalog(registrations=(root, sub))
        names = lambda before, current="": [item[0] for item in catalog.suggestions(before, current)]
        self.assertIn("log", names(["git"]))
        self.assertIn("--graph", names(["git", "-C", "/repo", "log"]))
        self.assertNotIn("log", names(["git", "log"]))
        self.assertNotIn("--graph", names(["git", "logger"]))
        self.assertEqual(names(["git", "log"], "--color=a"), ["--color=always"])
        self.assertEqual(names(["git", "--"], "--"), [])
        self.assertEqual(names(["git", "-C"]), [])
        self.assertEqual(names(["git", "--unknown", "log"]), [])

    def test_local_analysis_requires_declaration_columns_and_explicit_command_heading(self):
        source = HelpAnalysis.clean("\x1b[31mOptions:\x1b[0m\n  -a, --all\tShow all\n  --output FILE  Write a file\n"
            "Example mentions --danger\nCommands:\n  list  List records\nUsage:\n  filename  Some argument\n")
        result = HelpAnalysis.local(source)
        self.assertEqual([item.name for item in result.items], ["-a", "--all", "--output", "list"])
        self.assertEqual(result.items[2].argument, Argument.REQUIRED)
        self.assertEqual(result.items[3].kind, ItemKind.COMMAND)
        self.assertEqual(Analysis.from_dict(result.to_dict()), result)
        with self.assertRaises(ValueError):
            Analysis((replace(result.items[0], name="--invented"),)).verified(source)
        with self.assertRaises(ValueError):
            Candidate("--color", argument=Argument.REQUIRED, values=("invented",), evidence="--color=auto").verify("--color=auto")

    def test_atomic_store_detects_concurrent_edits_and_preserves_corrupt_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CompletionStore(directory)
            original = store.load()
            registered = store.update(original, lambda catalog: catalog.put(Registration(("tool",), items=(Candidate("--all"),))))
            self.assertEqual(CompletionStore(directory).load(), registered)
            with self.assertRaisesRegex(ValueError, "changed"):
                store.update(original, lambda catalog: catalog.put(Registration(("other",))))
            self.assertEqual(store.load(), registered)
            store.path.write_text("invalid", encoding="utf-8")
            with self.assertRaises(ValueError):
                store.update(registered, lambda catalog: Catalog())
            self.assertEqual(store.path.read_text(), "invalid")
            self.assertEqual({p.name for p in Path(directory).iterdir()}, {store.FILENAME, "completions.lock"})
