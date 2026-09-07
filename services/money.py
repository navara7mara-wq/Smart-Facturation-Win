"""Authoritative fixed-precision monetary operations.

Business rule: every official monetary result is truncated to two decimal
places (toward zero).  Binary floating point and conventional rounding are
not permitted in this module.
"""

from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext


MONEY_QUANTUM = Decimal("0.01")
RATE_DIVISOR = Decimal("100")
DECIMAL_PRECISION = 50


def decimal_value(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Valeur décimale invalide: {value}") from exc


def localized_decimal(value) -> Decimal:
    raw = str(value or "0").strip().replace("\u00a0", "").replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", ".")
    return decimal_value(raw or "0")


def truncate_money(value) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return decimal_value(value).quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)


def money_storage(value) -> str:
    return format(truncate_money(value), ".2f")


def rate_percent(value) -> Decimal:
    """Return an invoice snapshot rate expressed as a percentage."""
    result = decimal_value(value)
    if result < 0 or result > 100:
        raise ValueError("Le taux financier doit être compris entre 0 et 100.")
    return result.quantize(MONEY_QUANTUM, rounding=ROUND_DOWN)


def fraction_to_percent(value) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return rate_percent(decimal_value(value) * RATE_DIVISOR)


def percent_to_fraction(value) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return rate_percent(value) / RATE_DIVISOR


def line_total(unit_price, quantity) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        return truncate_money(decimal_value(unit_price) * decimal_value(quantity))


def financial_totals(line_amounts, rg_rate_percent, tva_rate_percent):
    """Calculate HT -> RG -> HT after RG -> TVA -> TTC.

    Rates are invoice-level percentage snapshots (for example 5.00 and
    19.00).  Every amount handed to the next official stage is truncated.
    """
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        total_ht = truncate_money(sum((decimal_value(v) for v in line_amounts), Decimal("0")))
        rg = truncate_money(total_ht * percent_to_fraction(rg_rate_percent))
        ht_after_rg = truncate_money(total_ht - rg)
        tva = truncate_money(ht_after_rg * percent_to_fraction(tva_rate_percent))
        total_ttc = truncate_money(ht_after_rg + tva)
        return total_ht, rg, ht_after_rg, tva, total_ttc


def format_money(value, decimal_separator=",", thousands_separator=" ") -> str:
    amount = money_storage(value)
    sign = ""
    if amount.startswith("-"):
        sign, amount = "-", amount[1:]
    whole, cents = amount.split(".")
    groups = []
    while whole:
        groups.append(whole[-3:])
        whole = whole[:-3]
    grouped = thousands_separator.join(reversed(groups)) or "0"
    return f"{sign}{grouped}{decimal_separator}{cents}"


def format_rate(value) -> str:
    return money_storage(rate_percent(value)).rstrip("0").rstrip(".") or "0"
