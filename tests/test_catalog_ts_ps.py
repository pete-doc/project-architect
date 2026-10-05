# ruff: noqa: E501
"""F15, хвост PR 3: каталог возможностей и описания функций для TypeScript и PowerShell.

Плохие примеры: экспортируемая функция TypeScript без JSDoc, функция PowerShell без описания, слишком длинное описание;
закрытые, тестовые и объявленные типами файлы в каталог не попадают; старые функции под храповиком допускаются.
"""

import subprocess
import sys
from pathlib import Path

import parch_catalog

CI = Path(__file__).resolve().parents[1] / "plugin" / "templates" / "ci" / "parch" / "parch_ci.py"

TS_OK = """const hidden = 1;

/** Считает сумму заказа со скидкой. */
export function place(prices: number[], discount: number): number {
  return prices.length - discount + hidden;
}

/**
 * Загружает заказ по номеру.
 * Подробности для читателя, в каталог не попадают.
 * @param id номер заказа
 */
export async function load(id: number): Promise<number> {
  return id;
}

/** Применяет скидку к сумме. */
export const applyDiscount = (total: number, rate: number): number => total * rate;

function notExported(): void {}
"""

PS_OK = """<#
.SYNOPSIS
Останавливает игру и ждёт закрытия окна.
#>
function Stop-Game {
    Write-Host 'stop'
}

function Start-Game {
    <#
    .SYNOPSIS
    Запускает игру через Steam.
    #>
    Write-Host 'start'
}

# Считает секунды между двумя метками времени
function Get-Elapsed {
    param($a, $b)
}

function _hidden { }
"""


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ci(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CI), "catalog", "--project", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_typescript_exported_functions_need_a_jsdoc_first_line(tmp_path: Path) -> None:
    write(tmp_path / "src" / "orders.ts", TS_OK)
    entries, missing = parch_catalog.collect(tmp_path)
    assert missing == []
    assert [(e.name, e.description) for e in entries] == [
        ("place", "Считает сумму заказа со скидкой."),
        ("load", "Загружает заказ по номеру. Подробности для читателя, в каталог не попадают."),
        ("applyDiscount", "Применяет скидку к сумме."),
    ]


def test_a_typescript_export_without_jsdoc_or_with_only_tags_fails(tmp_path: Path) -> None:
    write(
        tmp_path / "src" / "bad.ts",
        "export function forgotten(): void {}\n\n/**\n * @param x число\n */\nexport function tagsOnly(x: number): number {\n  return x;\n}\n",
    )
    done = ci(tmp_path)
    assert done.returncode == 1
    assert "src/bad.ts:1: forgotten: нет описания" in done.stdout
    assert "tagsOnly" in done.stdout


def test_typescript_tests_and_declarations_are_not_part_of_the_catalog(tmp_path: Path) -> None:
    write(tmp_path / "src" / "a.ts", "/** Делает А. */\nexport function a(): void {}\n")
    write(tmp_path / "src" / "a.test.ts", "export function testHelper(): void {}\n")
    write(tmp_path / "src" / "types.d.ts", "export function declared(): void;\n")
    write(tmp_path / "src" / "__tests__" / "b.ts", "export function inTests(): void {}\n")
    write(tmp_path / "node_modules" / "lib" / "i.ts", "export function vendor(): void {}\n")
    entries, missing = parch_catalog.collect(tmp_path)
    assert missing == [] and [e.name for e in entries] == ["a"]


def test_powershell_functions_take_the_description_from_synopsis_or_a_comment(
    tmp_path: Path,
) -> None:
    write(tmp_path / "scripts" / "game.ps1", PS_OK)
    entries, missing = parch_catalog.collect(tmp_path)
    assert missing == []
    assert [(e.name, e.description) for e in entries] == [
        ("Stop-Game", "Останавливает игру и ждёт закрытия окна."),
        ("Start-Game", "Запускает игру через Steam."),
        ("Get-Elapsed", "Считает секунды между двумя метками времени"),
    ]


def test_a_powershell_function_without_a_description_fails(tmp_path: Path) -> None:
    write(tmp_path / "scripts" / "bad.ps1", "function Do-Thing {\n    param($x)\n}\n")
    done = ci(tmp_path)
    assert done.returncode == 1 and "scripts/bad.ps1:1: Do-Thing: нет описания" in done.stdout


def test_powershell_pester_tests_are_not_part_of_the_catalog(tmp_path: Path) -> None:
    write(tmp_path / "scripts" / "game.ps1", "# Запускает игру\nfunction Start-Game { }\n")
    write(tmp_path / "scripts" / "game.Tests.ps1", "function Helper-InTest { }\n")
    entries, missing = parch_catalog.collect(tmp_path)
    assert missing == [] and [e.name for e in entries] == ["Start-Game"]


def test_the_catalog_has_sections_for_the_new_languages_and_the_check_passes(
    tmp_path: Path,
) -> None:
    write(tmp_path / "src" / "orders.ts", TS_OK)
    write(tmp_path / "scripts" / "game.ps1", PS_OK)
    assert ci(tmp_path, "--update").returncode == 0
    text = (tmp_path / "docs" / "CAPABILITIES.md").read_text(encoding="utf-8")
    assert "## TypeScript" in text and "## PowerShell" in text
    assert "| `src/orders.ts` | `place` | Считает сумму заказа со скидкой. |" in text
    assert ci(tmp_path).returncode == 0


def test_old_typescript_and_powershell_functions_without_description_sit_under_the_ratchet(
    tmp_path: Path,
) -> None:
    write(tmp_path / "src" / "old.ts", "export function legacy(): void {}\n")
    write(tmp_path / "scripts" / "old.ps1", "function Old-Thing { }\n")
    assert ci(tmp_path, "--update", "--accept-new").returncode == 0
    assert ci(tmp_path).returncode == 0
    write(tmp_path / "src" / "new.ts", "export function brandNew(): void {}\n")
    done = ci(tmp_path)
    assert done.returncode == 1 and "brandNew" in done.stdout and "legacy" not in done.stdout
