"""Хранилище заказов (в памяти)."""

_ORDERS: dict[int, float] = {}


def save(order_id: int, total: float) -> None:
    _ORDERS[order_id] = total


def load(order_id: int) -> float:
    return _ORDERS[order_id]
