"""Failed saves must leave the last durable Hub settings/positions intact."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hub.config.preferences import PreferencesStore
from hub.config.view_state import ReadingPosition, ViewStateStore


class StoreFailureTests(unittest.TestCase):
    def test_failed_flush_or_replace_preserves_previous_file_and_removes_temporary(self):
        for kind in ("preferences", "positions"):
            for operation in ("fsync", "replace"):
                with self.subTest(kind=kind, operation=operation), TemporaryDirectory() as directory:
                    if kind == "preferences":
                        store = PreferencesStore(directory)
                        save = lambda value: store.save("profile", {"display_name": value})
                    else:
                        store = ViewStateStore(directory)
                        save = lambda value: store.save("project", value, {"session": ReadingPosition(value, 2, 10)})
                    save("before")
                    original = store.path.read_bytes()
                    target = "hub.config.store.os.fsync" if operation == "fsync" else "pathlib.Path.replace"
                    with patch(target, side_effect=OSError("disk unavailable")):
                        with self.assertRaisesRegex(OSError, "disk unavailable"):
                            save("after")
                    self.assertEqual(store.path.read_bytes(), original)
                    self.assertEqual(list((Path(directory) / ".hub").glob("*.tmp")), [])
