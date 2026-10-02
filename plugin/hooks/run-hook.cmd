: << 'CMDBLOCK'
@echo off
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
>&2 echo [parch] Python 3.12 or newer not found (tried python3, python, py -3^). Protection hooks are NOT working. Install Python from python.org or disable the plugin: claude plugin disable parch@project-architect
set "KIND=%SCRIPT:~0,6%"
if "!KIND!"=="guard_" exit /b 2
exit /b 1
CMDBLOCK
# Launcher for ProjectArchitect hooks (see docs/adr/0005). One file works both as an sh script
# (macOS, Linux, Git Bash on Windows) and as a Windows batch file: sh skips everything up to the
# CMDBLOCK line, cmd skips the first line.
#
# It finds Python 3.12+ itself (python3, python, py -3), really running each candidate to check it,
# then runs the hook script from its own folder. Rules for editing this file:
#  - the whole file must stay plain ASCII: cmd under a UTF-8 code page misparses any non-ASCII byte
#    (this is why the Russian message below is written as printf octal escapes);
#  - no comments in the batch half; Python is started with `call`, otherwise .bat/.cmd shims
#    (pyenv-win) would never return control;
#  - line endings must be LF (.gitattributes): CR breaks the sh half.
script="$1"
self=$(printf '%s' "$0" | tr '\\' '/')
here=$(cd "$(dirname "$self")" && pwd)
for candidate in "python3" "python" "py -3"; do
  # $candidate is deliberately unquoted: "py -3" must split into a command and an argument
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
printf '[parch] \320\235\320\265 \320\275\320\260\320\271\320\264\320\265\320\275 Python 3.12 \320\270\320\273\320\270 \320\275\320\276\320\262\320\265\320\265 (\320\270\321\201\320\272\320\260\320\273\320\270 python3, python, py -3). Hooks \320\267\320\260\321\211\320\270\321\202\321\213 \320\275\320\265 \321\200\320\260\320\261\320\276\321\202\320\260\321\216\321\202. \320\243\321\201\321\202\320\260\320\275\320\276\320\262\320\270\321\202\320\265 Python \321\201 python.org \320\270\320\273\320\270 \320\276\321\202\320\272\320\273\321\216\321\207\320\270\321\202\320\265 \320\277\320\273\320\260\320\263\320\270\320\275: claude plugin disable parch@project-architect\012' >&2
case "$script" in
  guard_*) exit 2 ;;
esac
exit 1
