"""Deterministic value metrics — $/TB storage and $/GB RAM computed in pure
Python from the specs spec-extraction already produced.

This is deliberately NOT an LLM call: unit math must be exact, and handing
the model a precomputed "$39.99/TB" fact keeps the anti-hallucination rule
("never state a number you weren't given") enforceable by construction.
Fails open to None everywhere — a metric we can't compute confidently is
simply omitted from prompts and embeds, never guessed.

Rule: capacity >= 100GB renders as $/TB (storage-sized); anything smaller
renders as $/GB (RAM/peripheral-sized). A 256GB drive reads naturally as
$/TB; a 64GB RAM kit as $/GB.

Re-theme note (R5): this metric is deterministic and unchanged by the
keyboard re-theme — it only fires for storage/RAM-type specs that still
happen to surface from retail sources. Keyboard hardware (switches,
keycaps, PCBs) rarely carries a capacity spec, so it will typically be
absent; that is fine, the metric simply omits itself (fails open to None).
"""

import re

# Matches spec strings produced by ai.spec_extraction, e.g.
# "Capacity: 2TB", "Capacity: 1.5 TB", "Memory: 16GB", "RAM: 32GB".
_CAPACITY_RE = re.compile(r"(?i)\b(capacity|memory|ram):\s*([0-9]+(?:\.[0-9]+)?)\s*(tb|gb)\b")

_STORAGE_MIN_GB = 100


def compute_value_metric(specs: list[str], sale_price: float) -> str | None:
    """First parseable capacity spec wins. Returns e.g. '$39.99/TB' or
    '$4.99/GB', or None when no spec parses or the inputs are unusable."""
    if not sale_price or sale_price <= 0:
        return None
    for spec in specs or []:
        match = _CAPACITY_RE.search(spec)
        if not match:
            continue
        try:
            amount = float(match.group(2))
        except ValueError:
            continue
        if amount <= 0:
            continue
        gb = amount * 1024 if match.group(3).lower() == "tb" else amount
        if gb >= _STORAGE_MIN_GB:
            return f"${sale_price / (gb / 1024):.2f}/TB"
        return f"${sale_price / gb:.2f}/GB"
    return None


def value_metric_field(specs: list[str], sale_price: float) -> dict | None:
    """Discord embed field for the metric, or None when there isn't one."""
    metric = compute_value_metric(specs, sale_price)
    if not metric:
        return None
    return {"name": "Value", "value": metric, "inline": True}
