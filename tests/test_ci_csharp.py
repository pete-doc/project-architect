# ruff: noqa: E501  (в строках лежат куски C#-кода: переносить их нельзя без потери читаемости)
"""CI для C#: тестовый проект с намеренными нарушениями и «храповик».

Каждое нарушение (дубль, мёртвый код, нарушение границ модулей, опечатка в имени пространства
имён в правиле архитектуры, удалённый и пропущенный тест, пакет не из списка, модуль вне
MODULES.md, подавление предупреждений, ослабленные настройки сборки) должно быть поймано.
Запускаются настоящие dotnet, анализаторы, xUnit, coverlet, ArchUnitNET и jscpd.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import CS_SHOP, INIT_ANSWERS

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
WORKFLOW = REPO / "plugin" / "templates" / "ci" / "csharp.yml"
TEMPLATES = REPO / "plugin" / "templates" / "csharp"
INIT = REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"
REPORT = "test-results"
DOTNET = shutil.which("dotnet")
NODE = shutil.which("node")
ALL_CHECKS = [
    "settings",
    "tests",
    "skips",
    "suppressions",
    "modules",
    "deps",
    "dead-code",
    "duplicates",
    "architecture",
    "coverage",
]
FLAGS = ["-nodeReuse:false", "-p:UseSharedCompilation=false"]

SUMMARY_BODY = """
    public static string Summary(IReadOnlyList<decimal> prices, decimal discount)
    {
        var lines = new List<string>();
        var total = 0m;
        for (var index = 0; index < prices.Count; index++)
        {
            var shown = Math.Round(prices[index] * (1m - discount), 2);
            total += shown;
            lines.Add($"{index + 1}. {shown:F2}");
        }
        lines.Add($"Total: {total:F2}");
        return string.Join(", ", lines);
    }
"""
REPORT_BODY = """
    public static IReadOnlyList<string> Report(IReadOnlyDictionary<string, int> items, int limit)
    {
        var rows = new List<string>();
        var seen = 0;
        foreach (var pair in items.OrderBy(p => p.Key, StringComparer.Ordinal))
        {
            if (pair.Value > limit)
            {
                continue;
            }
            seen += pair.Value;
            rows.Add($"{pair.Key.ToUpperInvariant()}:{pair.Value * 2}");
        }
        rows.Add($"seen={seen}");
        rows.Add($"limit={limit}");
        return rows;
    }
"""
DEAD_SNIPPETS = {
    "IDE0051": "    private static int hidden() => 42;\n",
    "IDE0052": "    private static int written;\n\n    public static void Remember(int value) => written = value;\n",
    "IDE0060": "    public static int Extra(int used, int unused) => used;\n",
    "CS0169": "    private static int never;\n",
}

pytestmark = pytest.mark.skipif(
    DOTNET is None or NODE is None, reason="нужны .NET SDK и Node.js (jscpd)"
)


class CsShop:
    """Копия тестового проекта, настоящий dotnet и запуск проверок CI."""

    def __init__(self, root: Path, jscpd_cmd: str) -> None:
        self.root = root
        node_dir = str(Path(NODE or "node").parent)
        self.env = {
            **os.environ,
            "PARCH_JSCPD": jscpd_cmd,
            "PATH": node_dir + os.pathsep + os.environ.get("PATH", ""),
            "DOTNET_CLI_UI_LANGUAGE": "en",
            "DOTNET_NOLOGO": "1",
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        }

    def dotnet(self, *args: str, timeout: int = 600) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [DOTNET or "dotnet", *args, *(FLAGS if args[0] in {"build", "test"} else [])],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            check=False,
            timeout=timeout,
        )

    def test(self) -> subprocess.CompletedProcess[str]:
        """Тесты с отчётом trx и покрытием: так же, как в шаблоне CI."""
        shutil.rmtree(self.root / REPORT, ignore_errors=True)
        return self.dotnet(
            "test", "--logger", "trx", "--results-directory", REPORT,
            "--collect", "XPlat Code Coverage",
        )  # fmt: skip

    def run(self, check: str, *flags: str, fresh: bool = False) -> subprocess.CompletedProcess[str]:
        if fresh:
            self.test()
        return subprocess.run(
            [
                sys.executable, str(SCRIPT), check, "--language", "csharp",
                "--project", str(self.root), "--report", str(self.root / REPORT), *flags,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            check=False,
            timeout=900,
        )  # fmt: skip

    def passes(self, check: str, *flags: str, fresh: bool = False) -> str:
        done = self.run(check, *flags, fresh=fresh)
        assert done.returncode == 0, f"{check}: {done.stdout}{done.stderr}"
        return done.stdout

    def fails(self, check: str, *flags: str, fresh: bool = False) -> str:
        done = self.run(check, *flags, fresh=fresh)
        assert done.returncode == 1, f"{check} должен был упасть: {done.stdout}{done.stderr}"
        assert "ПРОВАЛ" in done.stdout
        return done.stdout

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def read(self, rel: str) -> str:
        return (self.root / rel).read_text(encoding="utf-8")

    def append(self, rel: str, text: str) -> None:
        self.write(rel, self.read(rel) + text)

    def replace(self, rel: str, old: str, new: str) -> None:
        text = self.read(rel)
        assert old in text, (rel, old)
        self.write(rel, text.replace(old, new, 1))

    def inside_class(self, rel: str, member: str) -> None:
        """Добавляет член в конец последнего класса файла."""
        text = self.read(rel).rstrip()
        assert text.endswith("}"), rel
        self.write(rel, text[:-1].rstrip() + "\n" + member + "}\n")

    def all_checks(self) -> dict[str, int]:
        return {name: self.run(name).returncode for name in ALL_CHECKS}


@pytest.fixture(scope="session")
def prepared(tmp_path_factory: pytest.TempPathFactory, jscpd: str) -> Path:
    """Тестовый проект один раз собран, прогнан и записан в baseline; копии берутся с него."""
    root = tmp_path_factory.mktemp("csshop") / "shop"
    shutil.copytree(CS_SHOP, root, ignore=shutil.ignore_patterns("bin", "obj", "state", REPORT))
    (root / "state").mkdir()
    project = CsShop(root, jscpd)
    done = project.test()
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-1000:]
    assert "baseline обновлён" in project.passes("baseline", "--update")
    return root


@pytest.fixture
def make_shop(tmp_path: Path, prepared: Path, jscpd: str) -> Iterator[Callable[[bool], CsShop]]:
    created = 0

    def build(baseline: bool = True) -> CsShop:
        nonlocal created
        root = tmp_path / f"shop{created}"
        created += 1
        shutil.copytree(prepared, root, ignore=shutil.ignore_patterns("bin", "obj"))
        if not baseline:
            for name in (root / "state").iterdir():
                name.unlink()
        return CsShop(root, jscpd)

    yield build


@pytest.fixture
def shop(make_shop: Callable[[bool], CsShop]) -> CsShop:
    """Чистый проект с записанным baseline и свежим отчётом о тестах."""
    return make_shop(True)


def test_clean_project_passes_every_check(shop: CsShop) -> None:
    assert set(shop.all_checks().values()) == {0}


def test_clean_project_passes_real_build_with_warnings_as_errors_and_format(
    make_shop: Callable[[bool], CsShop],
) -> None:
    project = make_shop(True)
    build = project.dotnet("build", "-warnaserror")
    assert build.returncode == 0, build.stdout[-1500:]
    done = project.dotnet("format", "--verify-no-changes")
    assert done.returncode == 0, done.stdout[-1500:] + done.stderr[-500:]


def test_real_analyzers_stop_warnings_in_the_regular_build(shop: CsShop) -> None:
    shop.inside_class("src/Shop/Util/MathUtil.cs", DEAD_SNIPPETS["IDE0051"])
    done = shop.dotnet("build")
    assert done.returncode != 0
    assert "IDE0051" in done.stdout


# ---------- дубли ----------


def test_copy_pasted_code_is_found_as_duplicate(shop: CsShop) -> None:
    for rel in ("src/Shop/Orders/OrderService.cs", "src/Shop/Pricing/Discount.cs"):
        shop.inside_class(rel, SUMMARY_BODY)
    out = shop.fails("duplicates")
    assert "Summary" in out or "OrderService.cs" in out or "Discount.cs" in out


def test_old_duplicates_pass_and_a_new_one_does_not(make_shop: Callable[[bool], CsShop]) -> None:
    project = make_shop(False)
    for rel in ("src/Shop/Orders/OrderService.cs", "src/Shop/Pricing/Discount.cs"):
        project.inside_class(rel, SUMMARY_BODY)
    project.passes("baseline", "--update")
    project.passes("duplicates")
    for rel in ("src/Shop/Ui/Screen.cs", "src/Shop/Db/OrderStore.cs"):
        project.inside_class(rel, REPORT_BODY)
    project.fails("duplicates")


# ---------- мёртвый код ----------


@pytest.mark.parametrize("code", sorted(DEAD_SNIPPETS))
def test_dead_code_is_found(shop: CsShop, code: str) -> None:
    shop.inside_class("src/Shop/Util/MathUtil.cs", DEAD_SNIPPETS[code])
    out = shop.fails("dead-code")
    assert code in out
    assert "MathUtil.cs" in out


def test_unused_using_is_found(shop: CsShop) -> None:
    shop.replace(
        "src/Shop/Util/MathUtil.cs",
        "namespace Shop.Util;",
        "using System.Text;\n\nnamespace Shop.Util;",
    )
    assert "IDE0005" in shop.fails("dead-code")


def test_old_dead_code_passes_and_new_does_not(make_shop: Callable[[bool], CsShop]) -> None:
    project = make_shop(False)
    project.inside_class("src/Shop/Util/MathUtil.cs", DEAD_SNIPPETS["IDE0051"])
    project.passes("baseline", "--update")
    project.passes("dead-code")
    project.inside_class("src/Shop/Util/MathUtil.cs", "    private static int another() => 1;\n")
    assert "another" in project.fails("dead-code")
    refused = project.fails("baseline", "--update", fresh=True)
    assert "--accept-new" in refused
    project.passes("baseline", "--update", "--accept-new")
    project.passes("dead-code")


def test_dead_code_check_rebuilds_instead_of_trusting_old_output(shop: CsShop) -> None:
    """Сборка уже выполнена, а проверка всё равно находит предупреждения (no-incremental)."""
    assert shop.dotnet("build").returncode == 0
    shop.inside_class("src/Shop/Util/MathUtil.cs", DEAD_SNIPPETS["CS0169"])
    assert "CS0169" in shop.fails("dead-code")


# ---------- архитектура ----------


ARCH = "tests/Shop.Tests/ArchitectureTests.cs"


def test_a_real_layer_violation_is_caught(shop: CsShop) -> None:
    shop.inside_class(
        "src/Shop/Ui/Screen.cs",
        "    public static decimal Raw(int id) => Shop.Db.OrderStore.Load(id);\n",
    )
    out = shop.fails("architecture")
    assert "UiDoesNotTouchTheStoreDirectly" in out
    assert "Shop.Ui.Screen" in out


def test_typo_in_the_namespace_of_the_checked_types_fails(shop: CsShop) -> None:
    shop.replace(ARCH, 'ResideInNamespace("Shop.Orders")', 'ResideInNamespace("Shop.Ordres")')
    out = shop.fails("architecture")
    assert "Shop.Ordres" in out
    assert "опечатка" in out


def test_typo_in_the_namespace_of_the_forbidden_target_fails(shop: CsShop) -> None:
    """Такая опечатка в ArchUnitNET молча даёт зелёный результат: ловит скрипт CI."""
    shop.replace(
        ARCH,
        'NotDependOnAny(Types().That().ResideInNamespace("Shop.Db"))',
        'NotDependOnAny(Types().That().ResideInNamespace("Shop.Dbb"))',
    )
    out = shop.fails("architecture")
    assert "Shop.Dbb" in out
    assert (
        shop.dotnet(
            "test", "--filter", "FullyQualifiedName~UiDoesNotTouchTheStoreDirectly"
        ).returncode
        == 0
    )  # сам по себе xUnit-тест при такой опечатке проходит: поэтому нужна проверка


def test_typo_in_a_regular_expression_pattern_fails(shop: CsShop) -> None:
    shop.replace(
        ARCH, 'ResideInNamespace("Shop.Pricing")', 'ResideInNamespaceMatching("^Shop\\\\.Prising$")'
    )
    assert "Prising" in shop.fails("architecture")


def test_a_working_regular_expression_pattern_is_accepted(shop: CsShop) -> None:
    shop.replace(
        ARCH, 'ResideInNamespace("Shop.Pricing")', 'ResideInNamespaceMatching("^Shop\\\\.Pricing$")'
    )
    shop.passes("architecture")


def test_library_default_rejects_an_empty_set_of_checked_types(shop: CsShop) -> None:
    """ArchUnitNET без WithoutRequiringPositiveResults сам падает на пустом множестве типов."""
    shop.replace(ARCH, 'ResideInNamespace("Shop.Orders")', 'ResideInNamespace("Shop.Ordres")')
    done = shop.dotnet("test", "--filter", "FullyQualifiedName~OrdersDoNotKnowTheUi")
    assert done.returncode != 0
    assert "requires positive evaluation" in done.stdout


def test_a_rule_that_accepts_empty_results_is_counted_as_a_suppression(shop: CsShop) -> None:
    shop.replace(
        ARCH,
        ".Check(Shop);\n\n    [Fact]\n    public void OrdersDoNotKnowTheUi",
        ".WithoutRequiringPositiveResults().Check(Shop);\n\n    [Fact]\n    public void OrdersDoNotKnowTheUi",
    )
    out = shop.fails("suppressions")
    assert "WithoutRequiringPositiveResults" in out


def test_namespace_given_not_as_a_literal_is_refused(shop: CsShop) -> None:
    shop.replace(
        ARCH,
        'Types().That().ResideInNamespace("Shop.Orders")',
        "Types().That().ResideInNamespace(Names.Orders)",
    )
    shop.append(
        ARCH, '\npublic static class Names\n{\n    public const string Orders = "Shop.Orders";\n}\n'
    )
    assert "не строка-литерал" in shop.fails("architecture")


def test_no_architecture_rules_at_all_fails(shop: CsShop) -> None:
    (shop.root / ARCH).unlink()
    out = shop.fails("architecture")
    assert "Нет ни одного правила" in out


def test_a_module_without_any_rule_fails(shop: CsShop) -> None:
    shop.write(
        "src/Shop/Reports/Report.cs",
        'namespace Shop.Reports;\n\npublic static class Report\n{\n    public static string Title => "Report";\n}\n',
    )
    shop.append("docs/MODULES.md", "| Reports | src/Shop/Reports | Отчёты | C# | active | F1 |\n")
    out = shop.fails("architecture")
    assert "src/Shop/Reports" in out
    assert "не охвачены" in out


def test_architecture_waits_for_code(tmp_path: Path, jscpd: str) -> None:
    root = tmp_path / "empty"
    (root / "docs").mkdir(parents=True)
    out = CsShop(root, jscpd).passes("architecture")
    assert "Кода пока нет" in out


# ---------- модули, зависимости ----------


def test_module_missing_from_modules_md_fails(shop: CsShop) -> None:
    shop.write(
        "src/Shop/Extras/Extra.cs", "namespace Shop.Extras;\n\npublic static class Extra\n{\n}\n"
    )
    out = shop.fails("modules")
    assert "src/Shop/Extras" in out


def test_package_outside_the_allowed_list_fails(shop: CsShop) -> None:
    shop.replace(
        "src/Shop/Shop.csproj",
        "</Project>",
        '  <ItemGroup>\n    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />\n  </ItemGroup>\n\n</Project>',
    )
    out = shop.fails("deps")
    assert "Newtonsoft.Json" in out
    assert "ADR" in out


def test_package_added_through_directory_packages_props_fails(shop: CsShop) -> None:
    shop.write(
        "Directory.Packages.props",
        '<Project>\n  <ItemGroup>\n    <PackageVersion Include="Serilog" Version="4.0.0" />\n  </ItemGroup>\n</Project>\n',
    )
    assert "Serilog" in shop.fails("deps")


def test_package_name_check_ignores_letter_case(shop: CsShop) -> None:
    shop.replace("tests/Shop.Tests/Shop.Tests.csproj", 'Include="xunit"', 'Include="XUnit"')
    shop.passes("deps")


# ---------- тесты и пропуски ----------


def test_a_deleted_test_fails_the_test_count_check(shop: CsShop) -> None:
    (shop.root / "tests/Shop.Tests/PricingTests.cs").unlink()
    out = shop.fails("tests", fresh=True)
    assert "Shop.Tests.PricingTests.AppliesADiscount" in out


def test_a_renamed_test_counts_as_removed(shop: CsShop) -> None:
    shop.replace("tests/Shop.Tests/PricingTests.cs", "AppliesADiscount", "AppliesAReduction")
    assert "Shop.Tests.PricingTests.AppliesADiscount" in shop.fails("tests", fresh=True)


def test_tests_check_demands_a_report(shop: CsShop) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "tests", "--language", "csharp", "--project", str(shop.root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=shop.env,
        check=False,
    )
    assert done.returncode == 1
    assert "нужен отчёт" in done.stdout


@pytest.mark.parametrize(
    "snippet",
    [
        '[Fact(Skip = "later")]',
        '[Theory(Skip = "later")]',
        '[Fact(DisplayName = "x", Skip = "later")]',
    ],
)
def test_skipped_tests_in_text_are_caught(shop: CsShop, snippet: str) -> None:
    shop.write(
        "tests/Shop.Tests/MoreTests.cs",
        f"namespace Shop.Tests;\n\npublic class MoreTests\n{{\n    {snippet}\n    public void Later() {{ }}\n}}\n",
    )
    out = shop.fails("skips")
    assert "MoreTests.cs" in out
    assert "--accept-skips" in out


def test_skip_hidden_from_text_search_is_caught_by_the_real_report(shop: CsShop) -> None:
    """Атрибут, который ставит Skip через экранированное имя: текст не находит, отчёт находит."""
    shop.write(
        "tests/Shop.Tests/LaterFact.cs",
        "namespace Shop.Tests;\n\npublic sealed class LaterFactAttribute : FactAttribute\n{\n"
        '    public LaterFactAttribute() => \\u0053kip = "later";\n}\n',
    )
    shop.write(
        "tests/Shop.Tests/HiddenTests.cs",
        "namespace Shop.Tests;\n\npublic class HiddenTests\n{\n    [LaterFact]\n    public void Later() { }\n}\n",
    )
    out = shop.fails("skips", fresh=True)
    assert "Shop.Tests.HiddenTests.Later" in out
    assert "--accept-skips" in out


def test_skip_words_in_strings_and_comments_are_not_skips(shop: CsShop) -> None:
    shop.append(
        "tests/Shop.Tests/PricingTests.cs",
        '\n// [Fact(Skip = "later")] просто слова в комментарии\n'
        'public static class Notes\n{\n    public const string Text = "[Fact(Skip = \\"x\\")] Assert.Ignore(1)";\n}\n',
    )
    shop.passes("skips", fresh=True)


# ---------- подавления ----------

SUPPRESSIONS = [
    "#pragma warning disable CS0168",
    '[System.Diagnostics.CodeAnalysis.SuppressMessage("Design", "CA1000")]',
    "#nullable disable",
    "[System.Diagnostics.CodeAnalysis.ExcludeFromCodeCoverage]",
]


@pytest.mark.parametrize("snippet", SUPPRESSIONS)
def test_a_new_suppression_is_a_violation(shop: CsShop, snippet: str) -> None:
    shop.append("src/Shop/Util/MathUtil.cs", f"\n{snippet}\n")
    out = shop.fails("suppressions")
    assert "src/Shop/Util/MathUtil.cs" in out
    assert "--accept-suppressions" in out


def test_resharper_comment_counts_as_a_suppression(shop: CsShop) -> None:
    shop.append("src/Shop/Util/MathUtil.cs", "\n// ReSharper disable once UnusedMember.Global\n")
    assert "ReSharper" in shop.fails("suppressions")


def test_suppression_words_in_strings_and_comments_are_not_counted(shop: CsShop) -> None:
    shop.append(
        "src/Shop/Util/MathUtil.cs",
        '\n// #pragma warning disable в комментарии, и [SuppressMessage("a", "b")] тоже\n'
        "/* [ExcludeFromCodeCoverage] #nullable disable */\n",
    )
    shop.replace(
        "src/Shop/Util/MathUtil.cs",
        "public static class MathUtil\n{",
        'public static class MathUtil\n{\n    public const string Note = "#pragma warning disable и #nullable disable";\n    public const string Verbatim = @"[SuppressMessage(""x"", ""y"")]";\n    public const string Raw = """#pragma warning disable""";\n',
    )
    shop.passes("suppressions")


def test_old_suppression_passes_and_one_more_does_not(make_shop: Callable[[bool], CsShop]) -> None:
    project = make_shop(False)
    project.append("src/Shop/Util/MathUtil.cs", "\n#pragma warning disable CS0168\n")
    project.passes("baseline", "--update")
    project.passes("suppressions")
    project.append("src/Shop/Util/MathUtil.cs", "\n#pragma warning disable CS0169\n")
    assert "было 1, стало 2" in project.fails("suppressions")
    assert "--accept-suppressions" in project.fails("baseline", "--update")
    project.passes("baseline", "--update", "--accept-suppressions")
    project.passes("suppressions")


# ---------- настройки сборки ----------


@pytest.mark.parametrize(
    ("rel", "old", "new"),
    [
        ("Directory.Build.props", "<TreatWarningsAsErrors>true", "<TreatWarningsAsErrors>false"),
        ("Directory.Build.props", "<Nullable>enable", "<Nullable>disable"),
        ("Directory.Build.props", "<AnalysisMode>Recommended", "<AnalysisMode>None"),
        ("Directory.Build.props", '<PackageReference Include="Roslynator.Analyzers" Version="4.16.1" PrivateAssets="all" />', ""),
        (".editorconfig", "IDE0051.severity = warning", "IDE0051.severity = none"),
        ("global.json", '"rollForward": "disable"', '"rollForward": "latestMajor"'),
        ("src/Shop/Shop.csproj", "<OutputType>Library</OutputType>", "<OutputType>Library</OutputType>\n    <Nullable>disable</Nullable>"),
        ("src/Shop/Shop.csproj", "<OutputType>Library</OutputType>", "<OutputType>Library</OutputType>\n    <NoWarn>CS8600</NoWarn>"),
        ("src/Shop/Shop.csproj", "<OutputType>Library</OutputType>", "<OutputType>Library</OutputType>\n    <RunAnalyzers>false</RunAnalyzers>"),
    ],
)  # fmt: skip
def test_weakening_build_settings_is_caught(shop: CsShop, rel: str, old: str, new: str) -> None:
    shop.replace(rel, old, new)
    out = shop.fails("settings")
    assert rel.split("/")[-1] in out
    assert "--accept-config" in out


def test_a_new_nuget_config_is_caught(shop: CsShop) -> None:
    shop.write(
        "nuget.config",
        '<configuration><packageSources><add key="x" value="https://example.org/v3/index.json" /></packageSources></configuration>\n',
    )
    assert "nuget.config" in shop.fails("settings")


def test_unrelated_project_file_edits_are_free(shop: CsShop) -> None:
    shop.replace(
        "src/Shop/Shop.csproj",
        "</Project>",
        '  <ItemGroup>\n    <InternalsVisibleTo Include="Shop.Tests" />\n  </ItemGroup>\n\n</Project>',
    )
    shop.passes("settings")


def test_accepted_config_change_is_recorded(shop: CsShop) -> None:
    shop.replace("Directory.Build.props", "<LangVersion>14.0", "<LangVersion>13.0")
    assert "--accept-config" in shop.fails("baseline", "--update", fresh=True)
    shop.passes("baseline", "--update", "--accept-config")
    shop.passes("settings")


# ---------- покрытие ----------


def test_coverage_drop_is_caught(shop: CsShop) -> None:
    shop.inside_class(
        "src/Shop/Util/MathUtil.cs",
        "    public static int Untested(int x)\n    {\n        if (x > 10)\n        {\n            return x * 2;\n        }\n\n        return x + 1;\n    }\n",
    )
    shop.test()
    assert "Покрытие упало" in shop.fails("coverage")


def test_coverage_demands_a_test_run_with_coverage(shop: CsShop) -> None:
    shutil.rmtree(shop.root / REPORT)
    assert "coverage.cobertura.xml" in shop.fails("coverage")


# ---------- CI-шаблон и версии ----------


def test_workflow_pins_every_version_and_runs_on_ubuntu() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "ubuntu-latest" in text and "windows-latest" not in text
    assert "global-json-file: global.json" in text
    assert 'node-version: "22.14.0"' in text
    assert "dotnet restore --locked-mode" in text
    assert "dotnet format --verify-no-changes" in text
    assert "-warnaserror" in text
    assert "--report test-results" in text
    assert "@latest" not in text
    for check in ALL_CHECKS:
        assert f"parch_ci.py {check} --language csharp" in text
    pinned = json.loads((CS_SHOP / "global.json").read_text(encoding="utf-8"))["sdk"]
    assert pinned["rollForward"] == "disable"
    assert pinned["version"].count(".") == 2


def test_every_package_version_is_exact_and_locked() -> None:
    files = [
        *CS_SHOP.rglob("*.csproj"),
        CS_SHOP / "Directory.Build.props",
        *TEMPLATES.glob("*.csproj"),
        TEMPLATES / "Directory.Build.props",
    ]
    for path in files:
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "Version=" in line and "PackageReference" in line:
                version = line.split('Version="')[1].split('"')[0]
                assert version.replace(".", "").isdigit(), (path.name, version)
        assert "*" not in text.replace("<!--", "")
    assert "<RestorePackagesWithLockFile>true" in (CS_SHOP / "Directory.Build.props").read_text(
        "utf-8"
    )
    assert list(CS_SHOP.rglob("packages.lock.json")), "lock-файлы должны лежать в репозитории"


def test_templates_match_the_tested_sample_project() -> None:
    for name, sample in (
        ("global.json", "global.json"),
        ("Directory.Build.props", "Directory.Build.props"),
        ("editorconfig", ".editorconfig"),
    ):
        assert (TEMPLATES / name).read_text("utf-8") == (CS_SHOP / sample).read_text("utf-8"), name
    for package in (
        "Microsoft.NET.Test.Sdk", "xunit", "xunit.runner.visualstudio", "coverlet.collector",
    ):  # fmt: skip
        template = (TEMPLATES / "App.Tests.csproj").read_text("utf-8")
        sample = (CS_SHOP / "tests/Shop.Tests/Shop.Tests.csproj").read_text("utf-8")
        line = next(x for x in sample.splitlines() if f'Include="{package}"' in x)
        assert line in template, package


# ---------- разбор кода: комментарии и строки ----------


def test_cs_lexer_blanks_comments_and_strings_but_keeps_directives() -> None:
    from parch_ci import blank_cs_comments_and_strings

    code, comments = blank_cs_comments_and_strings(
        '#pragma warning disable CS0168\nvar a = "x // y"; // тут #nullable disable\n'
        'var b = @"say ""hi"" /* z */"; /* ReSharper disable */\n'
        'var c = """raw "quoted" text"""; var d = \'"\'; var e = $"v{a}";\n'
    )
    assert "#pragma warning disable" in code
    assert "#nullable" not in code
    assert "ReSharper" not in code
    assert "ReSharper disable" in comments
    assert "hi" not in code
    assert "raw" not in code
    assert code.count("\n") == 4


def test_cs_lexer_can_keep_strings_for_reading_namespace_names() -> None:
    from parch_ci import blank_cs_comments_and_strings

    code, _ = blank_cs_comments_and_strings(
        'x.ResideInNamespace("Shop.Ui"); // ResideInNamespace("Old")\n', True
    )
    assert 'ResideInNamespace("Shop.Ui")' in code
    assert "Old" not in code


def test_trx_report_can_be_a_folder_with_several_projects(tmp_path: Path) -> None:
    from conftest import FIXTURES_DIR
    from parch_ci import REPORT_PARSERS

    shutil.copyfile(FIXTURES_DIR / "dotnet.trx", tmp_path / "one.trx")
    shutil.copyfile(FIXTURES_DIR / "dotnet.trx", tmp_path / "two.trx")
    single = REPORT_PARSERS["csharp"](FIXTURES_DIR / "dotnet.trx")
    assert REPORT_PARSERS["csharp"](tmp_path) == single


def test_an_empty_report_folder_is_an_error(tmp_path: Path) -> None:
    from parch_ci import ToolError, trx_report_outcomes

    with pytest.raises(ToolError):
        trx_report_outcomes(tmp_path)


# ---------- /parch:init-project ставит CI для C# ----------


def run_init(project: Path, languages: list[str]) -> dict[str, object]:
    request = {
        "project_dir": str(project),
        "name": "Мой сервис",
        "languages": languages,
        "description": "Считает заказы.",
        "priorities": "надёжность",
        **INIT_ANSWERS,
    }
    done = subprocess.run(
        [sys.executable, str(INIT)],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        env={**os.environ, "DOTNET_CLI_UI_LANGUAGE": "en"},
        check=False,
        timeout=900,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    result: dict[str, object] = json.loads(done.stdout.decode("utf-8"))
    return result


@pytest.fixture
def generated(tmp_path: Path, jscpd: str) -> CsShop:
    run_init(tmp_path / "gen", ["csharp"])
    return CsShop(tmp_path / "gen", jscpd)


def test_init_creates_a_csharp_project_with_pinned_tools(generated: CsShop) -> None:
    for rel in (
        "global.json", "Directory.Build.props", ".editorconfig", "App.sln",
        "tests/App.Tests/App.Tests.csproj", "tests/App.Tests/SmokeTests.cs",
        "tests/App.Tests/packages.lock.json", "state/baseline.json",
        ".github/parch/parch_ci.py", ".github/workflows/ci.yml", "docs/CONSTITUTION.md",
    ):  # fmt: skip
        assert (generated.root / rel).is_file(), rel
    for name, target in (
        ("global.json", "global.json"),
        ("Directory.Build.props", "Directory.Build.props"),
        ("editorconfig", ".editorconfig"),
    ):
        assert generated.read(target) == (TEMPLATES / name).read_text("utf-8")
    assert generated.read(".github/workflows/ci.yml") == WORKFLOW.read_text("utf-8")
    constitution = generated.read("docs/CONSTITUTION.md")
    assert "TngTech.ArchUnitNET.xUnit" in constitution
    assert "bin/" in generated.read(".gitignore")


def test_generated_csharp_project_is_green_from_the_start(generated: CsShop) -> None:
    build = generated.dotnet("build", "-warnaserror")
    assert build.returncode == 0, build.stdout[-1500:]
    assert generated.dotnet("format", "--verify-no-changes").returncode == 0
    assert generated.test().returncode == 0
    failed = {n: c for n, c in generated.all_checks().items() if c != 0}
    assert not failed, failed
    baseline = json.loads(generated.read("state/baseline.json"))
    assert baseline["tests"]["csharp"] == ["App.Tests.SmokeTests.Smoke"]
    assert "Directory.Build.props" in baseline["config"]["csharp"]


def test_generated_csharp_ci_catches_violations(generated: CsShop) -> None:
    generated.test()
    generated.replace(
        "tests/App.Tests/App.Tests.csproj",
        "</Project>",
        '  <ItemGroup>\n    <PackageReference Include="left-pad" Version="1.0.0" />\n  </ItemGroup>\n\n</Project>',
    )
    assert "left-pad" in generated.fails("deps")
    generated.replace(
        "tests/App.Tests/App.Tests.csproj",
        '    <PackageReference Include="left-pad" Version="1.0.0" />\n',
        "",
    )
    generated.replace("Directory.Build.props", "<Nullable>enable", "<Nullable>disable")
    assert "Directory.Build.props" in generated.fails("settings")
    generated.replace("Directory.Build.props", "<Nullable>disable", "<Nullable>enable")
    generated.replace("tests/App.Tests/SmokeTests.cs", "[Fact]", '[Fact(Skip = "x")]')
    assert "SmokeTests.cs" in generated.fails("skips")
    (generated.root / "tests/App.Tests/SmokeTests.cs").write_text(
        "namespace App.Tests;\n", encoding="utf-8"
    )
    assert "нет ни одного теста" in generated.fails("tests", fresh=True)


def test_python_and_csharp_together_get_separate_workflows(tmp_path: Path) -> None:
    run_init(tmp_path / "both", ["python", "csharp"])
    workflows = sorted(p.name for p in (tmp_path / "both" / ".github" / "workflows").iterdir())
    assert workflows == ["ci-csharp.yml", "ci.yml", "state.yml"]
    baseline = json.loads((tmp_path / "both" / "state" / "baseline.json").read_text("utf-8"))
    assert set(baseline["tests"]) == {"python", "csharp"}
