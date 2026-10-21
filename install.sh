#!/usr/bin/env bash
# BrewBit setup: verify the interpreter, self-check the module and the test
# suite, then EXIT. Runs entirely offline - BrewBit is standard library only,
# so this script never contacts a package index and never starts anything
# long-running.
#
# Run as an unprivileged user:  bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "BrewBit setup (standard library only - nothing to download, no network)"

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)'; then
  echo "error: BrewBit needs Python 3.8 or newer; found $(python3 -V 2>&1)" >&2
  exit 1
fi
echo "python3: $(python3 -V 2>&1)"

# Cheap self-checks so a broken tree fails loudly here, not at first use.
python3 -c 'import brewbit; print("brewbit", brewbit.VERSION, "imports cleanly")'
python3 brewbit.py --help > /dev/null
python3 -m unittest discover -s tests > /dev/null 2>&1 \
  && echo "test suite: OK" \
  || { echo "error: the test suite failed; run: python3 -m unittest discover -s tests -v" >&2; exit 1; }

echo "Setup complete. Demo: bash demo.sh   |   Use: python3 brewbit.py --help"
