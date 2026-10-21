#!/usr/bin/env bash
# BrewBit end-to-end demo.
#
# Runs every command against a throwaway copy of the tool in a temp directory,
# so your real brewbit.json is never touched. Non-interactive: no prompts, no
# network, no arguments. Run it with:  bash demo.sh
set -euo pipefail
cd "$(dirname "$0")"

PROJECT_DIR="$(pwd)"
DEMO_DIR="$(mktemp -d "${TMPDIR:-/tmp}/brewbit-demo.XXXXXX")"
cp "$PROJECT_DIR/brewbit.py" "$DEMO_DIR/brewbit.py"

bb() { python3 "$DEMO_DIR/brewbit.py" "$@"; }

# ISO date N days before today, so the freshness report always has both states.
days_ago() {
  python3 -c 'import datetime, sys
days = int(sys.argv[1])
print((datetime.date.today() - datetime.timedelta(days=days)).isoformat())' "$1"
}

step() { printf '\n== %s ==\n' "$1"; }

step "brewbit --version"
bb --version

step "brewbit --help (first 12 lines)"
bb --help | sed -n '1,12p'

step "empty state: brewbit lot list"
bb lot list

step "empty state: brewbit shot list"
bb shot list

step "empty state: brewbit stale"
bb stale

step "brewbit lot add (four lots across a roast cycle)"
bb lot add --name Kenya-AA-Nyeri --roast-date "$(days_ago 3)"
bb lot add --name Ethiopia-Guji-Natural --roast-date "$(days_ago 12)"
bb lot add --name Brazil-Cerrado --roast-date "$(days_ago 34)"
bb lot add --name Colombia-Huila --roast-date "$(days_ago 61)"

step "brewbit shot add (dose and yield in grams)"
bb shot add --lot Kenya-AA-Nyeri --dose 18 --yield 36 --grind "fine, 28s"
bb shot add --lot Kenya-AA-Nyeri --dose 18.5 --yield 41 --grind "finer, 32s"
bb shot add --lot Ethiopia-Guji-Natural --dose 16 --yield 45 --grind "coarse, 24s"
bb shot add --lot Brazil-Cerrado --dose 19 --yield 30 --grind "choked, 41s"
bb shot add --lot Colombia-Huila --dose 18 --yield 38

step "brewbit note add (tasting notes per lot)"
bb note add --lot Kenya-AA-Nyeri --text "blackcurrant, cane sugar, long finish"
bb note add --lot Kenya-AA-Nyeri --text "second bag: dialled in at 1.5 turns out"
bb note add --lot Ethiopia-Guji-Natural --text "blueberry, jasmine, tea-like body"
bb note add --lot Brazil-Cerrado --text "cocoa nib, peanut brittle, heavy body"

step "brewbit lot list"
bb lot list

step "brewbit shot list (newest first)"
bb shot list

step "brewbit shot list --lot Kenya-AA-Nyeri"
bb shot list --lot Kenya-AA-Nyeri

step "brewbit lot show Kenya-AA-Nyeri"
bb lot show Kenya-AA-Nyeri

step "brewbit stale (default 30-day window)"
bb stale

step "brewbit stale --days 7"
bb stale --days 7

step "brewbit stale --json (machine-readable)"
bb stale --json

step "validation: duplicate lot name is rejected"
bb lot add --name Kenya-AA-Nyeri --roast-date "$(days_ago 1)" || echo "(exit $?)"

step "validation: non-numeric dose is rejected"
bb shot add --lot Kenya-AA-Nyeri --dose heavy --yield 36 || echo "(exit $?)"

step "validation: unknown lot is rejected"
bb shot add --lot Missing-Lot --dose 18 --yield 36 || echo "(exit $?)"

step "validation: empty tasting note is rejected"
bb note add --lot Kenya-AA-Nyeri --text "   " || echo "(exit $?)"

step "validation: non-integer --days is rejected"
bb stale --days soon || echo "(exit $?)"

printf '\nDemo store: %s/brewbit.json (safe to delete)\n' "$DEMO_DIR"
printf 'Start your own log with: python3 brewbit.py lot add --name <name> --roast-date <YYYY-MM-DD>\n'
