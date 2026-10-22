#!/usr/bin/env bash
# host/launch/build.sh: build sa2recomp, the release's launcher (host/launch/sa2_launch.cpp).
#
#   host/launch/build.sh --table FILE.cpp [--out FILE] [--cxx g++|zig] [--target TRIPLE]
#
#   --table   the installer's hash table source (tools/s7/install_table.py writes it): the
#             selftest holds every file the game opened to it, and the config to its id.
#   --cxx zig zig c++ (clang 20) from tools/env.sh; with --target x86_64-windows-gnu it builds
#             sa2recomp.exe on sa2_launch_win32.cpp and host/install's sa2_platform_win32.cpp.
#
# The launcher shares host/install's config reader and platform layer and host/player's SHA-256
# and host tweaks reader (tweaks.cpp, with runtime/sa2_tweaks.h, the list it reads), compiled from
# the directories beside this one, so a snapshot of host/ with runtime/sa2_tweaks.h builds as a
# whole. Each host tweak of the launcher's, host/launch/tweaks/<name>.cpp, is compiled by glob
# (runtime/sa2_tweaks.h, "Add a host tweak").
# Its dialogs (the first run's folder question, play's error box: sa2_dialog.h) are the release's
# own SDL3, loaded at run time (dlopen, LoadLibraryW), so it links no SDL and needs no SDL header.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOSTDIR="$(dirname "$HERE")"
ROOT="$(cd "$HOSTDIR/.." && pwd)"
CXX_KIND=g++
TARGET=""
OUT=""
TABLE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --cxx) CXX_KIND="$2"; shift 2 ;;
        --target) TARGET="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --table) TABLE="$2"; shift 2 ;;
        *) echo "build.sh: unknown argument $1" >&2; exit 2 ;;
    esac
done
[ -n "$TABLE" ] && [ -f "$TABLE" ] || { echo "build.sh: --table names no table source ($TABLE)" >&2; exit 2; }
WINDOWS=0
case "$TARGET" in *windows*) WINDOWS=1 ;; esac
if [ -z "$OUT" ]; then
    OUT="$ROOT/private/generated/s7/launch/sa2recomp"
    [ "$WINDOWS" = 1 ] && OUT="$OUT.exe"
fi
mkdir -p "$(dirname "$OUT")"
I="$HOSTDIR/install"
SOURCES=("$HERE/sa2_launch.cpp" "$HERE/sa2_relay.cpp" "$HERE/sa2_json.cpp" "$HERE/sa2_dialog.cpp"
         "$HERE/sa2_session_files.cpp" "$HERE/sa2_debug_export.cpp"
         "$I/sa2_config.cpp" "$HOSTDIR/player/sha256.cpp" "$HOSTDIR/player/tweaks.cpp" "$TABLE")
for src in "$HERE"/tweaks/*.cpp; do
    [ -e "$src" ] && SOURCES+=("$src")
done
if [ "$WINDOWS" = 1 ]; then
    SOURCES+=("$HERE/sa2_launch_win32.cpp" "$I/sa2_platform_win32.cpp")
else
    SOURCES+=("$HERE/sa2_launch_posix.cpp" "$I/sa2_platform_posix.cpp")
fi
FLAGS=(-std=c++17 -O2 -Wall -Wextra "-I$HERE" "-I$I" "-I$HOSTDIR/player")
if [ "$CXX_KIND" = zig ]; then
    # shellcheck disable=SC1091
    command -v zig >/dev/null || source "$ROOT/tools/env.sh" >/dev/null
    CXX=(zig c++)
    [ -n "$TARGET" ] && CXX+=(-target "$TARGET")
else
    CXX=("$CXX_KIND")
fi
if [ "$WINDOWS" = 1 ]; then
    "${CXX[@]}" "${FLAGS[@]}" -static "${SOURCES[@]}" -lshell32 -ladvapi32 -o "$OUT"
else
    "${CXX[@]}" "${FLAGS[@]}" -pthread "${SOURCES[@]}" -ldl -o "$OUT"
fi
echo "built $OUT"
