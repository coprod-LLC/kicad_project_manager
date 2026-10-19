#!/usr/bin/env bash
# Use a bundled KiCad Python so macOS does not need a separate Python installation.
set -euo pipefail
port_source="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for port_python in /Applications/KiCad*/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 /Applications/KiCad*.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 "$HOME"/Applications/KiCad*/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3; do
    if [[ -x "$port_python" ]]; then
        exec "$port_python" "$port_source/scripts/setup_port.py" "$@"
    fi
done
exec python3 "$port_source/scripts/setup_port.py" "$@"
