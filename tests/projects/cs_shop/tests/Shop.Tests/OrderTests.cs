using Shop.Orders;
using Shop.Ui;

namespace Shop.Tests;

public class OrderTests
{
    [Fact]
    public void PlacesAnOrder()
    {
        Assert.Equal(15m, OrderService.Place(1, [10m, 5m], 0m));
        Assert.Equal(15m, OrderService.TotalOf(1));
    }

    [Fact]
    public void ShowsTheTotal()
    {
        OrderService.Place(2, [20m], 0.5m);
        Assert.Equal("Order 2: 10.00", Screen.Show(2));
    }
}
