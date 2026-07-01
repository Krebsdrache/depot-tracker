"""Tests für die Provider-Registry (Phase 3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import registry  # noqa: E402
from providers.base import Provider  # noqa: E402


class TestRegistry(unittest.TestCase):
    def test_binance_is_registered(self):
        ids = [p.info().id for p in registry.all_providers()]
        self.assertIn("binance", ids)

    def test_get_provider_by_id(self):
        provider = registry.get_provider("binance")
        self.assertIsNotNone(provider)
        assert provider is not None
        self.assertIsInstance(provider, Provider)
        self.assertEqual(provider.info().id, "binance")

    def test_get_unknown_provider_returns_none(self):
        self.assertIsNone(registry.get_provider("does_not_exist"))

    def test_enabled_is_subset_of_all(self):
        all_ids = {p.info().id for p in registry.all_providers()}
        enabled_ids = {p.info().id for p in registry.enabled_providers()}
        self.assertTrue(enabled_ids.issubset(all_ids))


if __name__ == "__main__":
    unittest.main()
