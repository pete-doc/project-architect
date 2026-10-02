from shop import orders, ui


def test_place_saves_total() -> None:
    assert orders.place(1, [10.0, 5.0], 0.0) == 15.0
    assert orders.total_of(1) == 15.0


def test_ui_shows_total() -> None:
    orders.place(2, [20.0], 0.5)
    assert ui.show(2) == "Заказ 2: 10.00"
