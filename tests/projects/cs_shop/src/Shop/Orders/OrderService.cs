using Shop.Db;
using Shop.Pricing;

namespace Shop.Orders;

public static class OrderService
{
    public static decimal Place(int id, IEnumerable<decimal> prices, decimal discount)
    {
        var total = Discount.Apply(prices.Sum(), discount);
        OrderStore.Save(id, total);
        return total;
    }

    public static decimal TotalOf(int id) => OrderStore.Load(id);
}
