using ArchUnitNET.Domain;
using ArchUnitNET.Loader;
using ArchUnitNET.xUnit;
using static ArchUnitNET.Fluent.ArchRuleDefinition;

namespace Shop.Tests;

public class ArchitectureTests
{
    private static readonly Architecture Shop =
        new ArchLoader().LoadAssemblies(typeof(Shop.Orders.OrderService).Assembly).Build();

    [Fact]
    public void UiDoesNotTouchTheStoreDirectly() =>
        Types().That().ResideInNamespace("Shop.Ui")
            .Should().NotDependOnAny(Types().That().ResideInNamespace("Shop.Db"))
            .Check(Shop);

    [Fact]
    public void OrdersDoNotKnowTheUi() =>
        Types().That().ResideInNamespace("Shop.Orders")
            .Should().NotDependOnAny(Types().That().ResideInNamespace("Shop.Ui"))
            .Check(Shop);

    [Fact]
    public void PricingIsPure() =>
        Types().That().ResideInNamespace("Shop.Pricing")
            .Should().NotDependOnAny(Types().That().ResideInNamespace("Shop.Db")
                .Or().ResideInNamespace("Shop.Orders").Or().ResideInNamespace("Shop.Ui"))
            .Check(Shop);

    [Fact]
    public void LeafModulesDependOnNoOne() =>
        Types().That().ResideInNamespace("Shop.Db").Or().ResideInNamespace("Shop.Util")
            .Should().NotDependOnAny(Types().That().ResideInNamespace("Shop.Orders")
                .Or().ResideInNamespace("Shop.Pricing").Or().ResideInNamespace("Shop.Ui"))
            .Check(Shop);
}
