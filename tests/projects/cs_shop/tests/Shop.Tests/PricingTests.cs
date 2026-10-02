using Shop.Pricing;

namespace Shop.Tests;

public class PricingTests
{
    [Fact]
    public void AppliesADiscount() => Assert.Equal(75m, Discount.Apply(100m, 0.25m));

    [Fact]
    public void CapsTheDiscountAtNinetyPercent() => Assert.Equal(10m, Discount.Apply(100m, 5m));
}
