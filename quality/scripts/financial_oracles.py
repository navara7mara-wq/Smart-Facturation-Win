"""Independent financial oracle and varied golden/property scenarios for RC-1."""

from __future__ import annotations

import json
import random
import sys
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


CENT = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def oracle(lines, retention_rate=Decimal("0.05"), tax_rate=Decimal("0.19")):
    line_amounts = [q2(Decimal(str(quantity)) * Decimal(str(price))) for quantity, price in lines]
    total_ht = q2(sum(line_amounts, Decimal("0")))
    retention = q2(total_ht * retention_rate)
    after_retention = q2(total_ht - retention)
    tax = q2(after_retention * tax_rate)
    total_ttc = q2(after_retention + tax)
    return {
        "line_amounts": [str(value) for value in line_amounts],
        "total_ht": str(total_ht),
        "retenue_garantie": str(retention),
        "montant_ht_apres_rg": str(after_retention),
        "tva": str(tax),
        "total_ttc": str(total_ttc),
    }


def scenarios():
    fixed = [
        [(1, "100")], [(2, "100")], [(1.5, "99.99")], [(0.01, "0.01")],
        [(3, "0")], [(1, "0.005")], [(1, "0.015")], [(2.5, "1234.567")],
        [(999999, "999999.99")], [(1, "1"), (1, "2")], [(0.333, "3")],
        [(7.25, "8.125")], [(10, "0.333")], [(100, "0.01")],
        [(1, "123456789.12")], [(4.4, "100.05")], [(1.01, "1.01")],
        [(12, "19.19"), (6, "5.05"), (2.2, "1000")], [(1, "0.025")],
        [(9.999, "9.999")], [(50, "50"), (25, "25")], [(0.1, "0.1")],
        [(123.456, "78.901")], [(1, "999999999.99")], [(5, "6.66"), (7, "8.88")],
    ]
    return fixed


def main():
    from services.billing import totals_from_lines

    golden = []
    failures = []
    for index, lines in enumerate(scenarios(), 1):
        expected = oracle(lines)
        payload = [
            {"montant_ht": round(float(quantity) * float(price), 2)}
            for quantity, price in lines
        ]
        actual_tuple = totals_from_lines(payload, retention_rate=0.05, tax_rate=0.19)
        actual_normalized = {
            key: f"{Decimal(str(value)):.2f}"
            for key, value in zip(
                ("total_ht", "retenue_garantie", "montant_ht_apres_rg", "tva", "total_ttc"),
                actual_tuple,
            )
        }
        expected_totals = {key: value for key, value in expected.items() if key != "line_amounts"}
        passed = actual_normalized == expected_totals
        golden.append({"id": f"FIN-G-{index:02d}", "lines": lines, "expected": expected, "actual": actual_normalized, "pass": passed})
        if not passed:
            failures.append(f"FIN-G-{index:02d}")

    rng = random.Random(4601)
    property_failures = []
    property_failure_total = 0
    for index in range(2000):
        lines = [
            (Decimal(rng.randint(1, 1_000_000)) / Decimal(rng.choice([1, 10, 100, 1000])),
             Decimal(rng.randint(0, 100_000_000)) / Decimal(rng.choice([1, 10, 100, 1000])))
            for _ in range(rng.randint(1, 20))
        ]
        expected = oracle(lines)
        payload = [{"montant_ht": round(float(q) * float(p), 2)} for q, p in lines]
        actual_tuple = totals_from_lines(payload, retention_rate=0.05, tax_rate=0.19)
        normalized = {
            key: f"{Decimal(str(value)):.2f}"
            for key, value in zip(
                ("total_ht", "retenue_garantie", "montant_ht_apres_rg", "tva", "total_ttc"),
                actual_tuple,
            )
        }
        expected_totals = {key: value for key, value in expected.items() if key != "line_amounts"}
        if normalized != expected_totals:
            property_failure_total += 1
            if len(property_failures) < 20:
                property_failures.append({"index": index, "lines": [(str(q), str(p)) for q, p in lines], "expected": expected_totals, "actual": normalized})

    print(json.dumps({
        "golden_count": len(golden),
        "golden_failures": failures,
        "golden": golden,
        "property_iterations": 2000,
        "property_failure_count": property_failure_total,
        "property_failure_evidence_count": len(property_failures),
        "property_failures": property_failures,
    }, ensure_ascii=False, indent=2))
    return 1 if failures or property_failure_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
