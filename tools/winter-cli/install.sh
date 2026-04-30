#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/bin/winter"

mkdir -p "$INSTALL_DIR"
cp "$SOURCE" "$INSTALL_DIR/winter"
chmod +x "$INSTALL_DIR/winter"

if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
  echo "Installed to $INSTALL_DIR/winter"
  echo "Warning: $INSTALL_DIR is not on your PATH. Add it with:"
  echo "  export PATH=\"$INSTALL_DIR:\$PATH\""
else
  echo "Installed to $INSTALL_DIR/winter"
  echo "Run 'winter' from any winter workspace."
fi
