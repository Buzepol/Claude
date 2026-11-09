#!/usr/bin/env bash
# Bouwt DupeScout.app (Apple Silicon). Draai dit op een Mac, in een map met een venv.
#   python3 -m venv .venv && source .venv/bin/activate
#   pip install -e ".[gui,raw]" pyinstaller
#   ./packaging/build_macos.sh
# Optioneel ondertekenen/notariseren: zet SIGN_ID="Developer ID Application: Naam (TEAMID)".
set -euo pipefail
cd "$(dirname "$0")/.."

pyinstaller --noconfirm --windowed --name DupeScout \
  --target-arch arm64 \
  --collect-all pillow_heif \
  --hidden-import PIL._tkinter_finder \
  --osx-bundle-identifier nl.buzepol.dupescout \
  dupescout/ui/app.py

if [[ -n "${SIGN_ID:-}" ]]; then
  codesign --force --deep --options runtime --sign "$SIGN_ID" dist/DupeScout.app
fi

# dmg (optioneel)
if command -v hdiutil >/dev/null; then
  rm -f dist/DupeScout.dmg
  hdiutil create -volname DupeScout -srcfolder dist/DupeScout.app -ov -format UDZO dist/DupeScout.dmg
fi
echo "Klaar: dist/DupeScout.app"
