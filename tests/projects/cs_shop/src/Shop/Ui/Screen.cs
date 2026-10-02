using System.Globalization;
using Shop.Orders;

namespace Shop.Ui;

public static class Screen
{
    public static string Show(int id) =>
        string.Create(CultureInfo.InvariantCulture, $"Order {id}: {OrderService.TotalOf(id):F2}");
}
