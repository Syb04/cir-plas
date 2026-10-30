#!/usr/bin/env bash
# Run inside an MSYS2 MINGW64 shell: copies libngspice and its runtime dependencies into packaging/ngspice/.
set -euo pipefail
dest="${1:-packaging/ngspice}"
mkdir -p "$dest"
pacman -S --noconfirm --needed mingw-w64-x86_64-ngspice >/dev/null
cp /mingw64/bin/libngspice-0.dll "$dest/"
# runtime DLLs the library depends on (mingw-w64 only; system DLLs are skipped)
for dll in $(ldd /mingw64/bin/libngspice-0.dll | awk '/mingw64/ {print $3}'); do
  cp "$dll" "$dest/"
done
# code models and spinit (optional, used only if XSPICE code models are needed)
if [ -d /mingw64/lib/ngspice ]; then
  mkdir -p "$dest/ngspice"
  cp /mingw64/lib/ngspice/*.cm "$dest/ngspice/" 2>/dev/null || true
fi
ls -la "$dest"
