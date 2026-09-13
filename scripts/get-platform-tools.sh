#!/usr/bin/env sh
# Download Google's official Android platform-tools (adb + fastboot) into ~/platform-tools.
# mrt looks there automatically. Usage: sh scripts/get-platform-tools.sh
set -eu
case "$(uname -s)" in
  Linux*)  os=linux ;;
  Darwin*) os=darwin ;;
  MINGW*|MSYS*|CYGWIN*) os=windows ;;
  *) echo "unsupported OS: $(uname -s)"; exit 1 ;;
esac
url="https://dl.google.com/android/repository/platform-tools-latest-${os}.zip"
dest="${HOME}/platform-tools"
tmp="$(mktemp -d)"
echo "downloading ${url}"
if command -v curl >/dev/null 2>&1; then curl -fL "$url" -o "$tmp/pt.zip"; else wget -O "$tmp/pt.zip" "$url"; fi
rm -rf "$dest"
unzip -q "$tmp/pt.zip" -d "${HOME}"
rm -rf "$tmp"
echo "installed to ${dest}"
echo "add to PATH:  export PATH=\"${dest}:\$PATH\""
"$dest/adb" --version | head -1
