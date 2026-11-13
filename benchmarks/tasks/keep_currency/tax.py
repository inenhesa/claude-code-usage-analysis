CURRENCY = "EUR"


def round_tax(amount_cents):
    # Bug: truncates toward zero instead of rounding half up.
    return int(amount_cents * 0.2)
