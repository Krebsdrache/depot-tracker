"""Tests für den depotspezifischen Speicher-Layer und die Alt-Daten-Migration (Phase 2)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import storage  # noqa: E402


class TestDepotPaths(unittest.TestCase):
    def test_binance_dir_is_under_depots(self):
        binance = storage.binance_dir()
        self.assertEqual(binance.name, "binance")
        self.assertEqual(binance.parent.name, "depots")

    def test_depot_dir_creates_isolated_folders(self):
        a = storage.depot_dir("provider_a")
        b = storage.depot_dir("provider_b")
        self.assertNotEqual(a, b)
        self.assertTrue(a.exists())
        self.assertTrue(b.exists())


class TestMigration(unittest.TestCase):
    def test_copies_files_and_dirs_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = root / "depots" / "binance"
            root.mkdir(parents=True)
            (root / "kaeufe.csv").write_text("trade_id,coin\n1,BTC\n", encoding="utf-8")
            (root / "sync_meta.json").write_text("{}", encoding="utf-8")
            cache = root / "tages_cache"
            cache.mkdir()
            (cache / "2024-01-01.json").write_text("{}", encoding="utf-8")

            migrated = storage.migrate_legacy(
                root, target, ("kaeufe.csv", "sync_meta.json", "tages_cache")
            )

            self.assertIn("kaeufe.csv", migrated)
            self.assertIn("tages_cache", migrated)
            self.assertTrue((target / "kaeufe.csv").exists())
            self.assertTrue((target / "tages_cache" / "2024-01-01.json").exists())
            # Originale bleiben als Sicherung erhalten
            self.assertTrue((root / "kaeufe.csv").exists())

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = root / "depots" / "binance"
            root.mkdir(parents=True)
            (root / "kaeufe.csv").write_text("a", encoding="utf-8")

            first = storage.migrate_legacy(root, target, ("kaeufe.csv",))
            second = storage.migrate_legacy(root, target, ("kaeufe.csv",))

            self.assertEqual(first, ["kaeufe.csv"])
            self.assertEqual(second, [])

    def test_does_not_overwrite_existing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "data"
            target = root / "depots" / "binance"
            target.mkdir(parents=True)
            root.mkdir(parents=True, exist_ok=True)
            (root / "kaeufe.csv").write_text("NEU", encoding="utf-8")
            (target / "kaeufe.csv").write_text("ALT", encoding="utf-8")

            storage.migrate_legacy(root, target, ("kaeufe.csv",))

            self.assertEqual((target / "kaeufe.csv").read_text(encoding="utf-8"), "ALT")


if __name__ == "__main__":
    unittest.main()
