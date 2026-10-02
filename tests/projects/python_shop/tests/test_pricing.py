from shop import pricing


def test_discount_is_applied() -> None:
    assert pricing.apply_discount(100.0, 0.25) == 75.0


def test_discount_is_capped_at_ninety_percent() -> None:
    assert pricing.apply_discount(100.0, 5.0) == 10.0
