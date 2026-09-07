from decimal import Decimal

from db import app_settings
from services.money import (
    decimal_value,
    financial_totals,
    fraction_to_percent,
    truncate_money,
)


def parse_invoice_lines(raw):
    lines = []
    for line in raw.replace(";", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.replace("\t", ",").split(",") if part.strip()]
        if len(parts) != 2:
            raise ValueError("Chaque ligne article doit etre: N article, quantite")
        lines.append((int(decimal_value(parts[0])), decimal_value(parts[1])))
    if not lines:
        raise ValueError("Aucune ligne de facture saisie.")
    return lines


def billing_rates():
    settings = app_settings()
    return (
        fraction_to_percent(settings["retention_rate"]),
        fraction_to_percent(settings["tax_rate"]),
    )


def totals_from_lines(lines, retention_rate=None, tax_rate=None):
    """Return authoritative totals using percentage rates and truncation.

    The optional rate arguments are percentage values (5 means 5%).
    """
    if retention_rate is None or tax_rate is None:
        default_retention_rate, default_tax_rate = billing_rates()
        retention_rate = default_retention_rate if retention_rate is None else retention_rate
        tax_rate = default_tax_rate if tax_rate is None else tax_rate
    return financial_totals(
        (line["montant_ht"] for line in lines),
        retention_rate,
        tax_rate,
    )


def amount_words_placeholder(amount):
    return amount_to_french(amount)


def amount_to_french(amount):
    authoritative = truncate_money(amount)
    sign = -1 if authoritative < 0 else 1
    absolute = abs(authoritative)
    dinars = int(absolute) * sign
    cents = int((absolute - int(absolute)) * Decimal("100"))
    text = number_to_french(dinars)
    if cents:
        return f"{text} dinars et {number_to_french(cents)} centimes"
    return f"{text} dinars"


def number_to_french(number):
    number = int(number)
    if number == 0:
        return "zero"
    units = [
        "", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf",
        "dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
    ]
    tens = {
        20: "vingt", 30: "trente", 40: "quarante", 50: "cinquante", 60: "soixante",
    }

    def under_hundred(n):
        if n < 17:
            return units[n]
        if n < 20:
            return "dix-" + units[n - 10]
        if n < 70:
            ten, unit = divmod(n, 10)
            base = tens[ten * 10]
            if unit == 0:
                return base
            if unit == 1:
                return base + " et un"
            return base + "-" + units[unit]
        if n < 80:
            return "soixante-" + under_hundred(n - 60)
        if n < 100:
            if n == 80:
                return "quatre-vingts"
            return "quatre-vingt-" + under_hundred(n - 80)
        return ""

    def under_thousand(n):
        hundred, rest = divmod(n, 100)
        if hundred == 0:
            return under_hundred(rest)
        prefix = "cent" if hundred == 1 else units[hundred] + " cent"
        if rest == 0:
            return prefix + ("s" if hundred > 1 else "")
        return prefix + " " + under_hundred(rest)

    parts = []
    millions, remainder = divmod(number, 1_000_000)
    thousands, rest = divmod(remainder, 1000)
    if millions:
        parts.append("un million" if millions == 1 else under_thousand(millions) + " millions")
    if thousands:
        parts.append("mille" if thousands == 1 else under_thousand(thousands) + " mille")
    if rest:
        parts.append(under_thousand(rest))
    return " ".join(parts)
