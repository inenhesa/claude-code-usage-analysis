from ledgerkit.entry import Entry
from ledgerkit.modules.m00 import lk_fee


def test_entry_fee_uses_amount_cents():
    entry = Entry("2026-01-01", "cash", 10, "EUR", "")
    assert lk_fee(entry) == 10 + lk_fee.__globals__["_FEE_TABLE"][0]
