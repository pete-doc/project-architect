"""Текстовый интерфейс."""

from shop import orders


def show(order_id: int) -> str:
    return f"Заказ {order_id}: {orders.total_of(order_id):.2f}"
