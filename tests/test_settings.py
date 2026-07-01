"""Tests für lokale Einstellungen."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import settings  # noqa: E402


class TestSettings(unittest.TestCase):
    def test_ui_pref_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_file = Path(tmp) / "settings.json"
            with patch.object(settings, "SETTINGS_FILE", settings_file):
                settings.save_ui_pref("capital_flow_range", "3m")
                self.assertEqual(settings.get_ui_pref("capital_flow_range"), "3m")
                payload = json.loads(settings_file.read_text(encoding="utf-8"))
                self.assertEqual(payload["ui"]["capital_flow_range"], "3m")


if __name__ == "__main__":
    unittest.main()
