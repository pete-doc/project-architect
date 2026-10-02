"""Заказы: считают сумму и сохраняют её."""

from shop import db, pricing


def place(order_id: int, prices: list[float], discount: float) -> float:
    total = pricing.apply_discount(sum(prices), discount)
    db.save(order_id, total)
    return total


def total_of(order_id: int) -> float:
    return db.load(order_id)
