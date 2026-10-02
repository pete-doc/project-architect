"""Цены и скидки."""

from shop import util


def apply_discount(amount: float, discount: float) -> float:
    return round(amount * (1 - util.clamp(discount, 0.0, 0.9)), 2)
