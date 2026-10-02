namespace Shop.Db;

public static class OrderStore
{
    private static readonly Dictionary<int, decimal> Orders = [];

    public static void Save(int id, decimal total) => Orders[id] = total;

    public static decimal Load(int id) => Orders.TryGetValue(id, out var total) ? total : 0m;
}
