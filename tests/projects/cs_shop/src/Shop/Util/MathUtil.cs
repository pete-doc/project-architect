namespace Shop.Util;

public static class MathUtil
{
    public static decimal Clamp(decimal value, decimal low, decimal high) =>
        Math.Max(low, Math.Min(high, value));
}
