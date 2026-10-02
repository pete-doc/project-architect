using Shop.Util;

namespace Shop.Pricing;

public static class Discount
{
    public static decimal Apply(decimal amount, decimal discount) =>
        Math.Round(amount * (1m - MathUtil.Clamp(discount, 0m, 0.9m)), 2);
}
