"""Tests for the deterministic value-metric math (value_metrics.py).

Pure Python — no network, no config. The whole point of this module is
that unit math is computed in code, never by an LLM, so these tests pin
the exact parsing and rounding behavior.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from deal_bot.value_metrics import compute_value_metric, value_metric_field


class ComputeValueMetricTests(unittest.TestCase):
    def test_tb_capacity(self):
        # $79.99 for 2TB -> $39.99/TB (after float formatting)
        self.assertEqual(compute_value_metric(["Capacity: 2TB"], 79.99), "$39.99/TB")

    def test_exact_tb_math(self):
        self.assertEqual(compute_value_metric(["Capacity: 2TB"], 160.0), "$80.00/TB")

    def test_gb_storage_renders_per_tb(self):
        # 256GB is storage-sized (>=100GB) — renders as $/TB
        self.assertEqual(compute_value_metric(["Capacity: 256GB"], 32.0), "$128.00/TB")

    def test_small_capacity_renders_per_gb(self):
        # 16GB is RAM-sized (<100GB) — renders as $/GB
        self.assertEqual(compute_value_metric(["Memory: 16GB"], 48.0), "$3.00/GB")

    def test_fractional_tb(self):
        self.assertEqual(compute_value_metric(["Capacity: 1.5 TB"], 90.0), "$60.00/TB")

    def test_case_insensitive_units(self):
        self.assertEqual(compute_value_metric(["capacity: 4TB"], 200.0), "$50.00/TB")

    def test_no_parseable_spec_returns_none(self):
        self.assertIsNone(compute_value_metric(["Interface: PCIe Gen4", "Warranty: 5 years"], 50.0))

    def test_empty_specs_return_none(self):
        self.assertIsNone(compute_value_metric([], 50.0))
        self.assertIsNone(compute_value_metric(None, 50.0))

    def test_zero_price_returns_none(self):
        self.assertIsNone(compute_value_metric(["Capacity: 2TB"], 0))
        self.assertIsNone(compute_value_metric(["Capacity: 2TB"], -1))


class ValueMetricFieldTests(unittest.TestCase):
    def test_field_built_when_metric_exists(self):
        field = value_metric_field(["Capacity: 2TB"], 160.0)
        self.assertEqual(field, {"name": "Value", "value": "$80.00/TB", "inline": True})

    def test_none_when_no_metric(self):
        self.assertIsNone(value_metric_field(["Interface: PCIe Gen4"], 50.0))


if __name__ == "__main__":
    unittest.main()
