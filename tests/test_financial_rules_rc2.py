from decimal import Decimal, ROUND_DOWN, localcontext
import random

import pytest

from services.billing import totals_from_lines
from services.money import line_total, money_storage, truncate_money


pytestmark = pytest.mark.unit
Q = Decimal("0.01")


GOLDEN_CASES = [
    ("FIN-RC2-G-01", [("1", "100")], "5", "19", ("100.00", "5.00", "95.00", "18.05", "113.05")),
    ("FIN-RC2-G-02", [("2", "100")], "5", "19", ("200.00", "10.00", "190.00", "36.10", "226.10")),
    ("FIN-RC2-G-03", [("1.5", "99.99")], "5", "19", ("149.98", "7.49", "142.49", "27.07", "169.56")),
    ("FIN-RC2-G-04", [("0.01", "0.01")], "5", "19", ("0.00", "0.00", "0.00", "0.00", "0.00")),
    ("FIN-RC2-G-05", [("3", "0")], "0", "0", ("0.00", "0.00", "0.00", "0.00", "0.00")),
    ("FIN-G-06", [("1", "0.005")], "5", "19", ("0.00", "0.00", "0.00", "0.00", "0.00")),
    ("FIN-G-07", [("1", "0.015")], "5", "19", ("0.01", "0.00", "0.01", "0.00", "0.01")),
    ("FIN-RC2-G-08", [("2.5", "1234.567")], "5", "19", ("3086.41", "154.32", "2932.09", "557.09", "3489.18")),
    ("FIN-RC2-G-09", [("999999", "999999.99")], "5", "19", ("999998990000.01", "49999949500.00", "949999040500.01", "180499817695.00", "1130498858195.01")),
    ("FIN-RC2-G-10", [("1", "1"), ("1", "2")], "5", "19", ("3.00", "0.15", "2.85", "0.54", "3.39")),
    ("FIN-RC2-G-11", [("0.333", "3")], "5", "19", ("0.99", "0.04", "0.95", "0.18", "1.13")),
    ("FIN-RC2-G-12", [("7.25", "8.125")], "10", "20", ("58.90", "5.89", "53.01", "10.60", "63.61")),
    ("FIN-RC2-G-13", [("10", "0.333")], "10", "20", ("3.33", "0.33", "3.00", "0.60", "3.60")),
    ("FIN-RC2-G-14", [("100", "0.01")], "0", "19", ("1.00", "0.00", "1.00", "0.19", "1.19")),
    ("FIN-RC2-G-15", [("1", "123456789.12")], "5", "19", ("123456789.12", "6172839.45", "117283949.67", "22283950.43", "139567900.10")),
    ("FIN-RC2-G-16", [("4.4", "100.05")], "7.5", "9", ("440.22", "33.01", "407.21", "36.64", "443.85")),
    ("FIN-RC2-G-17", [("1.01", "1.01")], "2.25", "17.75", ("1.02", "0.02", "1.00", "0.17", "1.17")),
    ("FIN-RC2-G-18", [("12", "19.19"), ("6", "5.05"), ("2.2", "1000")], "5", "19", ("2460.58", "123.02", "2337.56", "444.13", "2781.69")),
    ("FIN-G-19", [("1", "0.025")], "5", "19", ("0.02", "0.00", "0.02", "0.00", "0.02")),
    ("FIN-RC2-G-20", [("9.999", "9.999")], "10", "20", ("99.98", "9.99", "89.99", "17.99", "107.98")),
    ("FIN-RC2-G-21", [("50", "50"), ("25", "25")], "3.33", "17", ("3125.00", "104.06", "3020.94", "513.55", "3534.49")),
    ("FIN-RC2-G-22", [("0.1", "0.1")], "5", "19", ("0.01", "0.00", "0.01", "0.00", "0.01")),
    ("FIN-RC2-G-23", [("123.456", "78.901")], "6.75", "20.5", ("9740.80", "657.50", "9083.30", "1862.07", "10945.37")),
    ("FIN-RC2-G-24", [("1", "999999999.99")], "0", "20", ("999999999.99", "0.00", "999999999.99", "199999999.99", "1199999999.98")),
    ("FIN-RC2-G-25", [("5", "6.66"), ("7", "8.88")], "12.5", "7.25", ("95.46", "11.93", "83.53", "6.05", "89.58")),
]


def oracle_truncate(value):
    with localcontext() as context:
        context.prec = 50
        return Decimal(value).quantize(Q, rounding=ROUND_DOWN)


def oracle(lines, rg_rate, tva_rate):
    with localcontext() as context:
        context.prec = 50
        amounts = [oracle_truncate(Decimal(quantity) * Decimal(price)) for quantity, price in lines]
        ht = oracle_truncate(sum(amounts, Decimal("0")))
        rg = oracle_truncate(ht * Decimal(rg_rate) / Decimal("100"))
        after = oracle_truncate(ht - rg)
        tva = oracle_truncate(after * Decimal(tva_rate) / Decimal("100"))
        ttc = oracle_truncate(after + tva)
        return amounts, (ht, rg, after, tva, ttc)


@pytest.mark.parametrize(
    ("case_id", "source_lines", "rg_rate", "tva_rate", "expected"),
    GOLDEN_CASES,
    ids=[case[0] for case in GOLDEN_CASES],
)
def test_approved_financial_golden_cases(case_id, source_lines, rg_rate, tva_rate, expected):
    line_amounts = [line_total(price, quantity) for quantity, price in source_lines]
    actual = totals_from_lines(
        [{"montant_ht": amount} for amount in line_amounts],
        retention_rate=rg_rate,
        tax_rate=tva_rate,
    )
    assert tuple(money_storage(value) for value in actual) == expected, case_id


def test_2500_generated_cases_match_independent_approved_oracle():
    generator = random.Random(20260825)
    for case_index in range(2500):
        source_lines = []
        for _ in range(generator.randint(1, 20)):
            quantity = f"{generator.randint(1, 1_000_000)}.{generator.randint(0, 999):03d}"
            price = f"{generator.randint(0, 100_000_000)}.{generator.randint(0, 9999):04d}"
            source_lines.append((quantity, price))
        rg_rate = f"{generator.randint(0, 2000) / 100:.2f}"
        tva_rate = f"{generator.randint(0, 3000) / 100:.2f}"

        oracle_lines, expected = oracle(source_lines, rg_rate, tva_rate)
        actual_lines = [line_total(price, quantity) for quantity, price in source_lines]
        actual = totals_from_lines(
            [{"montant_ht": value} for value in actual_lines],
            retention_rate=rg_rate,
            tax_rate=tva_rate,
        )

        assert actual_lines == oracle_lines, case_index
        assert actual == expected, case_index
        ht, rg, after, tva, ttc = actual
        assert ht - rg == after
        assert after + tva == ttc
        assert all(value == truncate_money(value) for value in actual)
