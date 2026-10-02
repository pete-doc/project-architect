"""CI для TypeScript: тестовый проект с намеренными нарушениями и «храповик».

Каждое нарушение (дубль, мёртвый код, запрещённая зависимость, удалённый тест, пакет не из
списка, модуль вне MODULES.md, `any`, ослабленный tsconfig, `it.fails`, пустое правило архитектуры)
должно быть поймано. Инструменты (tsc, Biome, Vitest, knip, dependency-cruiser, jscpd) запускаются
настоящие, нужных версий; зависимости ставятся один раз по lock-файлу тестового проекта.
"""

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from conftest import TS_SHOP, link_directory, unlink_directory

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"
WORKFLOW = REPO / "plugin" / "templates" / "ci" / "typescript.yml"
REPORT = "test-report.json"
NODE = shutil.which("node")
NEEDS_REPORT = {"tests", "skips", "baseline", "coverage"}
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

SUMMARY_BODY = """

export function summary(prices: number[], discount: number): string {
  const lines: string[] = [];
  let total = 0;
  for (const [index, price] of prices.entries()) {
    const shown = Math.round(price * (1 - discount) * 100) / 100;
    total += shown;
    lines.push(`${index + 1}. ${shown.toFixed(2)}`);
  }
  lines.push(`Total: ${total.toFixed(2)}`);
  return lines.join('\\n');
}
"""
REPORT_BODY = """

export function report(items: Map<string, number>, limit: number): string[] {
  const rows: string[] = [];
  let seen = 0;
  for (const [name, count] of [...items.entries()].sort()) {
    if (count > limit) {
      continue;
    }
    seen += count;
    rows.push(`${name.toUpperCase()}:${count * 2}`);
  }
  rows.push(`seen=${seen}`);
  rows.push(`limit=${limit}`);
  return rows;
}
"""
DEAD_EXPORT = "\n\nexport function neverCalled(): number {\n  return 42;\n}\n"

pytestmark = pytest.mark.skipif(NODE is None, reason="нужен Node.js")


class TsShop:
    """Копия тестового проекта, настоящие инструменты и запуск проверок CI."""

    def __init__(self, root: Path, modules: Path, jscpd_cmd: str) -> None:
        self.root = root
        link_directory(root / "node_modules", modules)
        node_dir = str(Path(NODE or "node").parent)
        self.env = {
            **os.environ,
            "PARCH_JSCPD": jscpd_cmd,
            "PATH": node_dir + os.pathsep + os.environ.get("PATH", ""),
        }

    def close(self) -> None:
        unlink_directory(self.root / "node_modules")

    def tool(self, package: str, binary: str, *args: str) -> subprocess.CompletedProcess[str]:
        manifest = json.loads(
            (self.root / "node_modules" / package / "package.json").read_text("utf-8")
        )
        bins = manifest["bin"]
        entry = bins if isinstance(bins, str) else bins[binary]
        return subprocess.run(
            [NODE or "node", str(self.root / "node_modules" / package / entry), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            check=False,
            timeout=300,
        )

    def vitest(self) -> subprocess.CompletedProcess[str]:
        return self.tool(
            "vitest", "vitest", "run", "--coverage", "--reporter=default", "--reporter=json",
            f"--outputFile.json={REPORT}",
        )  # fmt: skip

    def run(self, check: str, *flags: str, fresh: bool = True) -> subprocess.CompletedProcess[str]:
        if fresh and check in NEEDS_REPORT:
            self.vitest()
        return subprocess.run(
            [
                sys.executable, str(SCRIPT), check, "--language", "typescript",
                "--project", str(self.root), "--report", str(self.root / REPORT), *flags,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=self.env,
            check=False,
            timeout=600,
        )  # fmt: skip

    def passes(self, check: str, *flags: str) -> str:
        done = self.run(check, *flags)
        assert done.returncode == 0, f"{check}: {done.stdout}{done.stderr}"
        return done.stdout

    def fails(self, check: str, *flags: str) -> str:
        done = self.run(check, *flags)
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

    def all_checks(self) -> dict[str, int]:
        self.vitest()
        return {name: self.run(name, fresh=False).returncode for name in ALL_CHECKS}


@pytest.fixture
def make_shop(tmp_path: Path, ts_node_modules: Path, jscpd: str) -> Iterator[Callable[[], TsShop]]:
    created: list[TsShop] = []

    def build() -> TsShop:
        root = tmp_path / f"shop{len(created)}"
        shutil.copytree(
            TS_SHOP,
            root,
            ignore=shutil.ignore_patterns("node_modules", "coverage", "state", REPORT),
        )
        (root / "state").mkdir(exist_ok=True)
        shop = TsShop(root, ts_node_modules, jscpd)
        created.append(shop)
        return shop

    yield build
    for shop in created:
        shop.close()


@pytest.fixture
def shop(make_shop: Callable[[], TsShop]) -> TsShop:
    """Чистый проект с записанным baseline."""
    project = make_shop()
    assert "baseline обновлён" in project.passes("baseline", "--update")
    return project


def test_clean_project_passes_every_check_and_every_real_tool(shop: TsShop) -> None:
    assert set(shop.all_checks().values()) == {0}
    for package, binary, args in (
        ("typescript", "tsc", ("--noEmit",)),
        ("@biomejs/biome", "biome", ("ci", ".")),
    ):
        done = shop.tool(package, binary, *args)
        assert done.returncode == 0, f"{package}: {done.stdout}{done.stderr}"


# ---------- каждое намеренное нарушение ловится ----------


def test_duplicate_code_is_caught(shop: TsShop) -> None:
    shop.append("src/pricing/index.ts", SUMMARY_BODY)
    shop.append("src/orders/index.ts", SUMMARY_BODY.replace("summary", "summary2"))
    assert "новые дубли" in shop.fails("duplicates")


def test_dead_export_and_orphan_file_are_caught(shop: TsShop) -> None:
    shop.append("src/util/index.ts", DEAD_EXPORT)
    out = shop.fails("dead-code")
    assert "neverCalled" in out
    shop.write("src/orphan.ts", "export const orphan = 1;\n")
    assert "src/orphan.ts" in shop.fails("dead-code")


def test_forbidden_dependency_is_caught(shop: TsShop) -> None:
    shop.write(
        "src/ui/index.ts",
        "import { load } from '../db';\nimport { totalOf } from '../orders';\n\n"
        "export function show(id: number): string {\n"
        "  return `Order ${id}: ${totalOf(id)} ${load(id)}`;\n}\n",
    )
    out = shop.fails("architecture")
    assert "ui-not-db" in out
    assert "src/ui/index.ts" in out


def test_deleted_test_is_caught(shop: TsShop) -> None:
    (shop.root / "tests" / "pricing.test.ts").unlink()
    out = shop.fails("tests")
    assert "Число тестов уменьшилось" in out
    assert "pricing.test.ts::applies a discount" in out


def test_one_test_swapped_for_another_is_caught(shop: TsShop) -> None:
    shop.replace("tests/pricing.test.ts", "applies a discount", "applies a different discount")
    out = shop.fails("tests")
    assert "pricing.test.ts::applies a discount" in out


@pytest.mark.parametrize(
    ("section", "name", "spec"),
    [
        ("devDependencies", "left-pad", "1.3.0"),
        ("dependencies", "lodash", "4.17.21"),
        ("peerDependencies", "react", "18.0.0"),
        ("devDependencies", "evil", "github:someone/evil"),
        ("devDependencies", "tarball", "https://example.com/pkg.tgz"),
    ],
)
def test_package_outside_the_allowed_list_is_caught(
    shop: TsShop, section: str, name: str, spec: str
) -> None:
    manifest = json.loads(shop.read("package.json"))
    manifest.setdefault(section, {})[name] = spec
    shop.write("package.json", json.dumps(manifest, indent=2) + "\n")
    out = shop.fails("deps")
    assert name in out
    assert "Разрешённые пакеты" in out


def test_a_package_from_the_list_with_a_registry_version_is_accepted(shop: TsShop) -> None:
    manifest = json.loads(shop.read("package.json"))
    manifest["devDependencies"]["knip"] = "6.39.0"
    shop.write("package.json", json.dumps(manifest, indent=2) + "\n")
    shop.passes("deps")


def test_module_missing_from_modules_md_is_caught(shop: TsShop) -> None:
    shop.write("src/billing/index.ts", "export const billing = 1;\n")
    assert "src/billing" in shop.fails("modules")
    shop.append("docs/MODULES.md", "| billing | src/billing | Счета | TypeScript | active |\n")
    shop.passes("modules")
    assert "src/billing" in shop.fails("architecture")  # без правил архитектуры тоже нельзя


# ---------- правило архитектуры не бывает «пусто-зелёным» ----------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('"to": { "path": "^src/db/" }', '"to": { "path": "^src/dbb/" }'),
        ('"from": { "path": "^src/ui/" }', '"from": { "path": "^src/uii/" }'),
    ],
)
def test_architecture_rule_with_a_typo_fails_instead_of_passing(
    shop: TsShop, old: str, new: str
) -> None:
    shop.replace(".dependency-cruiser.json", old, new)
    out = shop.fails("architecture")
    assert "ничего не охватывают" in out
    assert "ui-not-db" in out


def test_architecture_without_path_rules_or_config_fails(shop: TsShop) -> None:
    only_circular = {
        "forbidden": [
            {"name": "no-circular", "severity": "error", "from": {}, "to": {"circular": True}}
        ]
    }
    shop.write(".dependency-cruiser.json", json.dumps(only_circular))
    assert "нет ни одного правила с путями" in shop.fails("architecture")
    (shop.root / ".dependency-cruiser.json").unlink()
    assert "нет правил архитектуры" in shop.fails("architecture")


def test_dependency_cruiser_that_analyzes_nothing_fails(shop: TsShop) -> None:
    config = json.loads(shop.read(".dependency-cruiser.json"))
    config["options"]["exclude"] = {"path": "^src/"}
    shop.write(".dependency-cruiser.json", json.dumps(config))
    out = shop.fails("architecture")
    assert "не проанализировал ни одного файла" in out


def test_project_without_tests_is_not_green(shop: TsShop) -> None:
    for test_file in (shop.root / "tests").glob("*.test.ts"):
        test_file.unlink()
    shop.write("tests/placeholder.ts", "export {};\n")
    done = shop.run("tests")
    assert done.returncode == 1
    assert "нет ни одного теста" in done.stdout or "ПРОВАЛ" in done.stdout


# ---------- явные any и @ts-expect-error считаются как подавления ----------

ANY_VARIANTS = [
    "export const a: any = 1;",
    "export const b = 1 as any;",
    "export const c = <any>1;",
    "export const d: Record<string, any> = {};",
    "export const e: any[] = [];",
    "export type F = Promise<any>;",
    "export const g = (x: number): any => x;",
    "export function h(x: any) { return x; }",
]


@pytest.mark.parametrize("line", ANY_VARIANTS)
def test_a_new_explicit_any_is_a_violation(shop: TsShop, line: str) -> None:
    shop.append("src/util/index.ts", f"\n{line}\n")
    out = shop.fails("suppressions")
    assert "explicit any" in out
    assert "src/util/index.ts" in out
    assert "--accept-suppressions" in out


@pytest.mark.parametrize(
    "snippet",
    [
        "// @ts-expect-error\nexport const x: number = 'a';",
        "// @ts-ignore\nexport const y: number = 'a';",
        "// @ts-nocheck",
        "// biome-ignore lint/suspicious/noExplicitAny: нужно\nexport const z: unknown = 1;",
        "/* eslint-disable */",
    ],
)
def test_ts_expect_error_and_other_suppressions_count_too(shop: TsShop, snippet: str) -> None:
    shop.append("src/util/index.ts", f"\n{snippet}\n")
    out = shop.fails("suppressions")
    assert "src/util/index.ts" in out


@pytest.mark.parametrize(
    "line",
    [
        "export const a = 'x' as unknown as number;",
        "export const b = 'x' as never as number;",
        "export const c = 'x' as unknown\n  as number;",
    ],
)
def test_double_cast_counts_as_a_suppression(shop: TsShop, line: str) -> None:
    shop.append("src/util/index.ts", f"\n{line}\n")
    out = shop.fails("suppressions")
    assert "double cast" in out
    assert "--accept-suppressions" in out


def test_double_cast_in_comments_and_strings_is_not_counted(shop: TsShop) -> None:
    shop.append(
        "src/util/index.ts",
        "\n// приведение as unknown as T в комментарии\nexport const note = 'v as unknown as T';\n",
    )
    shop.passes("suppressions")


def test_any_in_comments_strings_and_words_is_not_counted(shop: TsShop) -> None:
    shop.append(
        "src/util/index.ts",
        "\n// тип: any здесь только слово в комментарии, как и `as any`\n"
        "export const note = 'x: any, y as any, <any>';\n"
        "export const company = 'any company';\n"
        "/* const k: any = 1; */\n",
    )
    shop.passes("suppressions")


def test_old_any_passes_and_one_more_does_not(make_shop: Callable[[], TsShop]) -> None:
    project = make_shop()
    project.append("src/util/index.ts", "\nconst legacy: any = 1;\n")
    project.passes("baseline", "--update")
    project.passes("suppressions")
    project.append("src/util/index.ts", "\nconst another: any = 2;\n")
    assert "было 1, стало 2" in project.fails("suppressions")
    refused = project.fails("baseline", "--update")
    assert "--accept-suppressions" in refused
    project.passes("baseline", "--update", "--accept-suppressions")
    project.passes("suppressions")


# ---------- ослабление tsconfig ловит проверка settings ----------


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('"strict": true', '"strict": false'),
        ('"noImplicitAny": true', '"noImplicitAny": false'),
        ('"strictNullChecks": true', '"strictNullChecks": false'),
        (
            '"skipLibCheck": true',
            '"skipLibCheck": true,\n    "allowJs": true,\n    "checkJs": false',
        ),
    ],
)
def test_weakening_tsconfig_is_blocked(shop: TsShop, old: str, new: str) -> None:
    shop.replace("tsconfig.json", old, new)
    out = shop.fails("settings")
    assert "tsconfig.json" in out
    assert "--accept-config" in out
    assert "--accept-config" in shop.fails("baseline", "--update")
    shop.passes("baseline", "--update", "--accept-config")
    shop.passes("settings")


def test_a_second_relaxed_tsconfig_is_blocked(shop: TsShop) -> None:
    shop.write(
        "tsconfig.build.json",
        '{"extends": "./tsconfig.json", "compilerOptions": {"strict": false}}',
    )
    assert "tsconfig.build.json" in shop.fails("settings")


def test_other_check_settings_are_protected_too(shop: TsShop) -> None:
    shop.replace("biome.json", '"preset": "recommended"', '"preset": "none"')
    assert "biome.json" in shop.fails("settings")
    shop.replace("biome.json", '"preset": "none"', '"preset": "recommended"')
    shop.passes("settings")
    manifest = json.loads(shop.read("package.json"))
    manifest["scripts"]["test"] = "echo ok"
    shop.write("package.json", json.dumps(manifest, indent=2) + "\n")
    assert "package.json#scripts.test" in shop.fails("settings")


# ---------- it.fails в Vitest выглядит «прошёл»: считаем его пропуском ----------


@pytest.mark.parametrize(
    "snippet",
    [
        "it.fails('known bug', () => {\n  expect(1).toBe(2);\n});",
        "test.fails('known bug', () => {\n  expect(1).toBe(2);\n});",
    ],
)
def test_it_fails_and_test_fails_count_as_skips(shop: TsShop, snippet: str) -> None:
    header = "import { expect, it, test } from 'vitest';\n\n"
    shop.write("tests/known.test.ts", header + snippet + "\n\nexpect(test).toBeDefined();\n")
    shop.vitest()
    report = json.loads(shop.read(REPORT))
    statuses = {
        a["fullName"]: a["status"]
        for r in report["testResults"]
        for a in r["assertionResults"]
        if a["fullName"] == "known bug"
    }
    assert statuses == {"known bug": "passed"}  # в отчёте Vitest это «прошёл»
    out = shop.fails("skips")
    assert "it.fails/test.fails" in out
    assert "--accept-skips" in out


def test_real_skips_are_caught_by_the_actual_test_report(shop: TsShop) -> None:
    shop.write(
        "tests/later.test.ts",
        "import { it } from 'vitest';\n\nit.skip('later', () => {});\nit.todo('someday');\n",
    )
    out = shop.fails("skips")
    assert "по фактическому результату запуска" in out
    assert "later.test.ts::later" in out
    assert "later.test.ts::someday" in out


# ---------- knip: широких исключений нет, каждое исключение под защитой settings ----------


@pytest.mark.parametrize(
    "config",
    [
        {"ignore": ["**"]},
        {"ignore": ["src/**"]},
        {"ignore": ["**/*.ts"]},
        {"ignoreDependencies": ["*"]},
        {"ignoreBinaries": [".*"]},
        {"ignoreIssues": {"src/**": ["exports"]}},
        {"workspaces": {".": {"ignore": ["src/**/*"]}}},
    ],
)
def test_broad_knip_exceptions_are_rejected(shop: TsShop, config: dict[str, object]) -> None:
    shop.write(
        "knip.json", json.dumps({"entry": ["src/index.ts"], "project": ["src/**/*.ts"], **config})
    )
    out = shop.fails("dead-code")
    assert "knip-config" in out or "широк" in out.lower() or "knip.json" in out


def test_a_narrow_knip_exception_is_allowed_but_needs_the_owner(shop: TsShop) -> None:
    config = json.loads(shop.read("knip.json"))
    config["ignore"] = ["src/generated/client.ts"]
    shop.write("knip.json", json.dumps(config))
    shop.passes("dead-code")  # узкое исключение не «широкое»
    out = shop.fails("settings")  # но любое исключение меняет настройки: решает владелец
    assert "knip.json" in out
    assert "--accept-config" in out


def test_pattern_classifier_separates_broad_from_narrow() -> None:
    from parch_ci import is_broad_pattern

    broad = ["**", "*", "**/*", "src/**", "src/**/*.ts", "./src/*", "**/*.ts", "!src/**"]
    narrow = ["src/generated/client.ts", "src/generated/**", "src/legacy/old.ts", "vite.config.ts"]
    assert all(is_broad_pattern(p) for p in broad)
    assert not any(is_broad_pattern(p) for p in narrow)


def test_comment_and_string_stripper_for_typescript() -> None:
    from parch_ci import blank_ts_comments_and_strings

    code, comments = blank_ts_comments_and_strings(
        "const a = 'x: any'; // y: any\n/* it.skip( */ const b: any = `t ${1}`;\n"
    )
    assert "const b: any" in code
    assert "'x: any'" not in code and "y: any" not in code and "it.skip" not in code
    assert "y: any" in comments and "it.skip" in comments


# ---------- «храповик» ----------


@pytest.fixture
def legacy(make_shop: Callable[[], TsShop]) -> TsShop:
    project = make_shop()
    project.append(
        "src/util/index.ts", "\n\nexport function legacyUnused(): number {\n  return 1;\n}\n"
    )
    project.append("src/pricing/index.ts", SUMMARY_BODY)
    project.append("src/orders/index.ts", SUMMARY_BODY.replace("summary", "summary2"))
    project.passes("baseline", "--update")
    return project


def test_ratchet_lets_old_violations_through(legacy: TsShop) -> None:
    assert "новых нет" in legacy.passes("dead-code")
    legacy.passes("duplicates")
    assert set(legacy.all_checks().values()) == {0}


def test_ratchet_blocks_a_new_dead_export_but_not_the_old_one(legacy: TsShop) -> None:
    legacy.append("src/util/index.ts", "\n\nexport function brandNew(): number {\n  return 2;\n}\n")
    out = legacy.fails("dead-code")
    assert "brandNew" in out
    assert "legacyUnused" not in out


def test_ratchet_blocks_a_new_duplicate_but_not_the_old_one(legacy: TsShop) -> None:
    legacy.append("src/util/index.ts", REPORT_BODY)
    legacy.append("src/db/index.ts", REPORT_BODY.replace("report", "report2"))
    out = legacy.fails("duplicates")
    assert "новые дубли" in out
    assert "1 new" in out


def test_baseline_refuses_new_violations_and_removed_tests(legacy: TsShop) -> None:
    legacy.append("src/util/index.ts", "\n\nexport function sneaky(): number {\n  return 3;\n}\n")
    refused = legacy.fails("baseline", "--update")
    assert "sneaky" in refused and "--accept-new" in refused
    legacy.passes("baseline", "--update", "--accept-new")
    (legacy.root / "tests" / "pricing.test.ts").unlink()
    assert "--accept-removed" in legacy.fails("baseline", "--update", "--accept-new")


def test_coverage_ratchet_blocks_a_drop(shop: TsShop) -> None:
    shop.append(
        "src/pricing/index.ts",
        "\n\nexport function untested(value: number): number {\n"
        "  if (value > 1) {\n    return value;\n  }\n  return 0;\n}\n",
    )
    shop.append("src/orders/index.ts", "\n\nexport function useIt(): number {\n  return 1;\n}\n")
    assert "Покрытие упало" in shop.fails("coverage")


# ---------- отчёт тестов формирует сам CI, без отчёта CI красный ----------


def test_checks_that_need_the_test_report_fail_without_it(shop: TsShop) -> None:
    shop.vitest()
    for check in ("tests", "skips"):
        done = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                check,
                "--language",
                "typescript",
                "--project",
                str(shop.root),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=shop.env,
            check=False,
        )
        assert done.returncode == 1, check
        assert "--report" in done.stdout


def test_workflow_forms_the_report_itself_and_pins_versions() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "npm test" in text  # скрипт test формирует test-report.json (reporter=json)
    assert "--report test-report.json" in text
    assert 'node-version: "22.14.0"' in text  # Node зафиксирован точной версией
    assert "npm ci" in text and "npm install" not in text  # установка строго по lock-файлу
    assert "@latest" not in text and ": latest" not in text
    for check in ALL_CHECKS:
        assert f"parch_ci.py {check} --language typescript" in text, check
    script = json.loads((TS_SHOP / "package.json").read_text(encoding="utf-8"))["scripts"]["test"]
    assert "--reporter=json" in script and "--outputFile.json=test-report.json" in script
    assert "--coverage" in script


def test_sample_project_pins_every_tool_exactly() -> None:
    manifest = json.loads((TS_SHOP / "package.json").read_text(encoding="utf-8"))
    for name, version in manifest["devDependencies"].items():
        assert version[0].isdigit(), f"{name}: версия {version} не зафиксирована точно"
    assert (TS_SHOP / "package-lock.json").is_file()


# ---------- /parch:init-project ставит CI для TypeScript ----------

INIT = REPO / "plugin" / "skills" / "init-project" / "scripts" / "init_project.py"


def run_init(project: Path, languages: list[str]) -> dict[str, object]:
    node_dir = str(Path(NODE or "node").parent)
    request = {
        "project_dir": str(project),
        "name": "Мой сервис",
        "languages": languages,
        "description": "Считает заказы.",
        "priorities": "надёжность",
    }
    done = subprocess.run(
        [sys.executable, str(INIT)],
        input=json.dumps(request).encode("utf-8"),
        capture_output=True,
        env={**os.environ, "PATH": node_dir + os.pathsep + os.environ.get("PATH", "")},
        check=False,
        timeout=600,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    result: dict[str, object] = json.loads(done.stdout.decode("utf-8"))
    return result


@pytest.fixture
def generated(tmp_path: Path, ts_node_modules: Path, jscpd: str) -> Iterator[TsShop]:
    run_init(tmp_path / "gen", ["typescript"])
    project = TsShop(tmp_path / "gen", ts_node_modules, jscpd)
    yield project
    project.close()


def test_init_creates_a_typescript_project_with_pinned_tools(generated: TsShop) -> None:
    for rel in (
        "package.json", "package-lock.json", "tsconfig.json", "biome.json", "vitest.config.ts",
        "knip.json", ".dependency-cruiser.json", "tests/smoke.test.ts", "state/baseline.json",
        ".github/parch/parch_ci.py", ".github/workflows/ci.yml", "docs/CONSTITUTION.md",
    ):  # fmt: skip
        assert (generated.root / rel).is_file(), rel
    manifest = json.loads(generated.read("package.json"))
    sample = json.loads((TS_SHOP / "package.json").read_text(encoding="utf-8"))
    assert manifest["devDependencies"] == sample["devDependencies"]  # те же точные версии
    assert manifest["scripts"] == sample["scripts"]
    assert generated.read(".github/workflows/ci.yml") == WORKFLOW.read_text(encoding="utf-8")
    constitution = generated.read("docs/CONSTITUTION.md")
    assert "- npm: @biomejs/biome, @types/node, @vitest/coverage-v8" in constitution
    assert "TypeScript 5.9" in constitution


def test_generated_typescript_project_is_green_from_the_start(generated: TsShop) -> None:
    for package, binary, args in (
        ("typescript", "tsc", ("--noEmit",)),
        ("@biomejs/biome", "biome", ("ci", ".")),
    ):
        done = generated.tool(package, binary, *args)
        assert done.returncode == 0, f"{package}: {done.stdout[-600:]}{done.stderr[-600:]}"
    assert generated.vitest().returncode == 0
    results = {name: generated.run(name, fresh=False) for name in ALL_CHECKS}
    failed = {n: r.stdout[-500:] for n, r in results.items() if r.returncode != 0}
    assert not failed, failed
    baseline = json.loads(generated.read("state/baseline.json"))
    assert baseline["tests"]["typescript"] == ["smoke.test.ts::smoke"]
    assert "tsconfig.json" in baseline["config"]["typescript"]


def test_generated_typescript_ci_catches_violations(generated: TsShop) -> None:
    generated.vitest()
    manifest = json.loads(generated.read("package.json"))
    manifest["devDependencies"]["left-pad"] = "1.3.0"
    generated.write("package.json", json.dumps(manifest, indent=2) + "\n")
    assert "left-pad" in generated.fails("deps")  # пакет в обход команды установки (дыра ADR-0004)
    manifest["devDependencies"].pop("left-pad")
    generated.write("package.json", json.dumps(manifest, indent=2) + "\n")
    generated.replace("tsconfig.json", '"strict": true', '"strict": false')
    assert "tsconfig.json" in generated.fails("settings")
    generated.replace("tsconfig.json", '"strict": false', '"strict": true')
    generated.replace("tests/smoke.test.ts", "it('smoke'", "it.skip('smoke'")
    assert "smoke.test.ts" in generated.fails("skips")
    generated.write(
        "tests/smoke.test.ts", "import { it } from 'vitest';\n\nit('other', () => {});\n"
    )
    assert "smoke.test.ts::smoke" in generated.fails("tests")


def test_python_and_typescript_together_get_separate_workflows(tmp_path: Path) -> None:
    run_init(tmp_path / "both", ["python", "typescript"])
    workflows = sorted(p.name for p in (tmp_path / "both" / ".github" / "workflows").iterdir())
    assert workflows == ["ci-typescript.yml", "ci.yml"]
    baseline = json.loads((tmp_path / "both" / "state" / "baseline.json").read_text("utf-8"))
    assert set(baseline["tests"]) == {"python", "typescript"}
    assert set(baseline["config"]) == {"python", "typescript"}


# ---------- остальные виды пропусков и подавлений TypeScript ----------

SKIP_SNIPPETS = [
    "it.only('x', () => {});",
    "describe.skip('x', () => {});",
    "test.todo('x');",
    "it.skipIf(true)('x', () => {});",
    "it.concurrent.skip('x', async () => {});",
]


@pytest.mark.parametrize("snippet", SKIP_SNIPPETS)
def test_other_ts_skip_forms_are_caught(shop: TsShop, snippet: str) -> None:
    shop.write(
        "tests/more.test.ts",
        "import { describe, it, test } from 'vitest';\n\n"
        f"{snippet}\nexport const used = [describe, it, test];\n",
    )
    out = shop.fails("skips")
    assert "tests/more.test.ts" in out
    assert "--accept-skips" in out


@pytest.mark.parametrize(
    "snippet",
    [
        "/* istanbul ignore next */",
        "// v8 ignore next",
        "// prettier-ignore",
        "/* c8 ignore start */",
    ],
)
def test_coverage_and_formatter_ignores_count_as_suppressions(shop: TsShop, snippet: str) -> None:
    shop.append("src/util/index.ts", f"\n{snippet}\n")
    assert "src/util/index.ts" in shop.fails("suppressions")


def test_skip_words_in_strings_and_comments_are_not_ts_skips(shop: TsShop) -> None:
    shop.append(
        "tests/pricing.test.ts",
        "\n// it.skip('later') и it.fails: просто слова в комментарии\n"
        "export const note = \"it.skip('x') и test.fails('y')\";\n",
    )
    shop.passes("skips")


def test_vitest_report_parser_reads_a_real_report() -> None:
    from conftest import FIXTURES_DIR
    from parch_ci import REPORT_PARSERS, skipped_ids

    outcomes = REPORT_PARSERS["typescript"](FIXTURES_DIR / "vitest.json")
    assert skipped_ids(outcomes) == {f"a.test.js::{n}" for n in ("later", "someday", "conditional")}
    assert outcomes["a.test.js::ok"] == "passed"
