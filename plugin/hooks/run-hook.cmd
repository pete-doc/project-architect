: << 'CMDBLOCK'
@echo off
rem Запускающий файл hooks ProjectArchitect. Один файл работает и как скрипт для sh (macOS, Linux,
rem Git Bash на Windows), и как пакетный файл Windows: sh пропускает блок до CMDBLOCK, cmd
rem пропускает первую строку. Он сам находит Python 3.12+ (python3, python, py -3) и запускает
rem скрипт hook из этой же папки. Python вызывается через call: иначе .bat/.cmd-заглушки (pyenv-win)
rem не возвращали бы управление. Переносы строк в файле должны быть LF (см. .gitattributes).
setlocal EnableExtensions EnableDelayedExpansion
set "SCRIPT=%~1"
set "HERE=%~dp0"
set "CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)"
for %%C in ("python3" "python" "py -3") do (
  call %%~C -c "!CHECK!" <nul >nul 2>nul
  if !errorlevel! equ 0 (
    call %%~C "!HERE!!SCRIPT!"
    exit /b !errorlevel!
  )
)
set "PROJECT=%CLAUDE_PROJECT_DIR%"
if not defined PROJECT set "PROJECT=%CD%"
set "MANAGED="
if exist "!PROJECT!\docs\CONSTITUTION.md" set "MANAGED=1"
if exist "!PROJECT!\CONSTITUTION.md" set "MANAGED=1"
if not defined MANAGED exit /b 0
>&2 echo [parch] Не найден Python 3.12 или новее (искали python3, python, py -3^). Hooks защиты не работают. Установите Python с python.org или отключите плагин: claude plugin disable parch@project-architect
set "KIND=%SCRIPT:~0,6%"
if "!KIND!"=="guard_" exit /b 2
exit /b 1
CMDBLOCK
# ---- часть для sh (macOS, Linux, Git Bash) ----
script="$1"
self=$(printf '%s' "$0" | tr '\\' '/')
here=$(cd "$(dirname "$self")" && pwd)
for candidate in "python3" "python" "py -3"; do
  # $candidate намеренно без кавычек: "py -3" должно разделиться на команду и аргумент
  # shellcheck disable=SC2086
  if $candidate -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' </dev/null >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    exec $candidate "$here/$script"
  fi
done
project="${CLAUDE_PROJECT_DIR:-$PWD}"
if [ ! -f "$project/docs/CONSTITUTION.md" ] && [ ! -f "$project/CONSTITUTION.md" ]; then
  exit 0
fi
echo "[parch] Не найден Python 3.12 или новее (искали python3, python, py -3). Hooks защиты не работают. Установите Python с python.org или отключите плагин: claude plugin disable parch@project-architect" >&2
case "$script" in
  guard_*) exit 2 ;;
esac
exit 1
