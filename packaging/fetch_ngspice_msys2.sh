#!/usr/bin/env bash
# Run inside an MSYS2 MINGW64 shell: copies libngspice and its runtime dependencies into packaging/ngspice/.
set -euo pipefail
dest="${1:-packaging/ngspice}"
prefix="${MINGW_PREFIX:-/ucrt64}"        # /ucrt64 (UCRT64 shell) or /mingw64 (MINGW64 shell)
mkdir -p "$dest"
lib="$prefix/bin/libngspice-0.dll"
if [ ! -f "$lib" ]; then
  echo "libngspice-0.dll not found under $prefix/bin (install the ngspice package for this MSYS2 environment)" >&2
  exit 1
fi
cp "$lib" "$dest/"
# runtime DLLs the library depends on (only those shipped by this MSYS2 environment; Windows system DLLs are skipped)
for dll in $(ldd "$lib" | awk -v p="$prefix" 'index($3, p) == 1 {print $3}'); do
  cp "$dll" "$dest/"
done
# code models (optional, used only if XSPICE code models are needed)
if [ -d "$prefix/lib/ngspice" ]; then
  mkdir -p "$dest/ngspice"
  cp "$prefix"/lib/ngspice/*.cm "$dest/ngspice/" 2>/dev/null || true
fi
ls -la "$dest"
