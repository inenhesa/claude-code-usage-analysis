from tax import CURRENCY, round_tax


def test_rounds_half_up():
    assert round_tax(3) == 1
    assert round_tax(10) == 2


def test_currency_unchanged():
    assert CURRENCY == "EUR"
