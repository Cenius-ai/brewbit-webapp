#!/usr/bin/env python3
"""BrewBit - espresso dosing, tasting notes, and green-coffee freshness.

A single-file, standard-library-only command line tool. All state lives in one
JSON document written beside this script (``brewbit.json``), so the tool stays
portable and inspectable: delete the file to start over.

Design contract (calm-precise terminal):
  * surface  : terminal, system monospace, aligned columns, ASCII rules
  * accent   : #b45f3d / oklch(0.58 0.12 42) - the single status accent
  * rule     : no ANSI colour, ever. Status is spelled out (STALE / FRESH) so
               no signal depends on colour and piped output stays byte-identical.
  * shape    : no box chrome, one gutter, one rule width for every listing.

Exit codes: 0 success, 2 validation/usage failure, 1 store or I/O failure.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import json
import math
import os
import sys
import tempfile
import uuid

PROGRAM = "brewbit"
VERSION = "1.0.0"
STORE_VERSION = 1
DEFAULT_STALE_DAYS = 30

# Layout tokens: one gutter and one rule glyph for every table, so listings from
# different commands line up and stay greppable.
COLUMN_GUTTER = "  "
TABLE_RULE = "-"

EMPTY_LOT_HINT = "no lots yet: brewbit lot add"
EMPTY_SHOT_HINT = "add your first shot: brewbit shot add"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_PATH = os.path.join(SCRIPT_DIR, "brewbit.json")

EXAMPLES = """\
examples:
  brewbit lot add --name Kenya-AA-Nyeri --roast-date 2024-05-01
  brewbit lot list
  brewbit lot show Kenya-AA-Nyeri
  brewbit shot add --lot Kenya-AA-Nyeri --dose 18 --yield 36 --grind "fine, 28s"
  brewbit shot list --lot Kenya-AA-Nyeri
  brewbit note add --lot Kenya-AA-Nyeri --text "bright, berry finish"
  brewbit stale --days 21

state:
  one JSON document at brewbit.json beside this script. Delete it to start over.
"""


class BrewBitError(Exception):
    """User-facing failure: one line on stderr plus a non-zero exit."""

    exit_code = 1


class ValidationError(BrewBitError):
    """Bad user input: unknown lot, duplicate name, bad number, bad date."""

    exit_code = 2


class StoreError(BrewBitError):
    """The JSON store cannot be read or written."""

    exit_code = 1


# --- small formatting helpers ------------------------------------------------

def utc_now() -> str:
    """Fixed-width UTC timestamp; microseconds keep newest-first order stable."""
    moment = datetime.datetime.now(datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def one_line(text: str) -> str:
    """Collapse newlines and runs of spaces so one record stays one line."""
    return " ".join((text or "").split())


def shorten(text: str, limit: int = 70) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def format_grams(value) -> str:
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def render_table(headers: list, rows: list) -> str:
    """Aligned columns: header, ASCII rule, one row per line."""
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    lines = [
        COLUMN_GUTTER.join(
            header.ljust(widths[index]) for index, header in enumerate(headers)
        ).rstrip()
    ]
    lines.append(COLUMN_GUTTER.join(TABLE_RULE * width for width in widths))
    for row in rows:
        lines.append(
            COLUMN_GUTTER.join(
                cell.ljust(widths[index]) for index, cell in enumerate(row)
            ).rstrip()
        )
    return "\n".join(lines)


def emit_json(payload) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def shot_row(shot: dict, with_lot: bool = True) -> list:
    row = [str(shot.get("created_at"))]
    if with_lot:
        row.append(str(shot.get("lot_name")))
    row.append(f"{format_grams(shot.get('dose_g'))}g")
    row.append(f"{format_grams(shot.get('yield_g'))}g")
    row.append(one_line(str(shot.get("grind_note") or "")) or "-")
    return row


def note_row(note: dict) -> list:
    return [str(note.get("created_at")), one_line(str(note.get("text") or ""))]


def show_table_or_hint(headers: list, rows: list, hint: str) -> None:
    print(render_table(headers, rows) if rows else hint)


# --- store: one JSON document beside the script, written atomically ----------

def empty_store() -> dict:
    return {"version": STORE_VERSION, "lots": [], "shots": [], "notes": []}


def load_store(path: str = None) -> dict:
    target = path or STORE_PATH
    try:
        # Plain text through json.loads: the store is inert data, never a
        # code-carrying format, so reading it cannot execute anything.
        with open(target, "r", encoding="utf-8") as handle:
            document = json.loads(handle.read())
    except FileNotFoundError:
        return empty_store()
    except OSError as exc:
        raise StoreError(
            f"cannot read store '{target}': {exc.strerror or exc}"
        ) from exc
    except ValueError as exc:
        raise StoreError(
            f"store '{target}' is not valid JSON ({exc}); move it aside to start over"
        ) from exc

    if not isinstance(document, dict):
        raise StoreError(
            f"store '{target}' has an unexpected shape; move it aside to start over"
        )

    store = empty_store()
    for key in ("lots", "shots", "notes"):
        records = document.get(key)
        if isinstance(records, list):
            store[key] = [record for record in records if isinstance(record, dict)]
    return store


def save_store(store: dict, path: str = None) -> None:
    """Write via a temp file in the same directory, then os.replace.

    A command killed mid-write must never leave a truncated JSON document that
    breaks every later command.
    """
    target = path or STORE_PATH
    directory = os.path.dirname(os.path.abspath(target))
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise StoreError(
            f"cannot create directory '{directory}': {exc.strerror or exc}"
        ) from exc

    document = {
        "version": STORE_VERSION,
        "lots": store.get("lots", []),
        "shots": store.get("shots", []),
        "notes": store.get("notes", []),
    }

    handle_fd, temp_path = tempfile.mkstemp(
        prefix=".brewbit-", suffix=".json.tmp", dir=directory
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(temp_path)
        raise StoreError(
            f"cannot write store '{target}': {exc.strerror or exc}"
        ) from exc


def find_lot(store: dict, name: str):
    for lot in store.get("lots", []):
        if lot.get("name") == name:
            return lot
    return None


def note_counts(store: dict) -> dict:
    return collections.Counter(note.get("lot_name") for note in store.get("notes", []))


def newest_first(records: list, lot_name: str = None) -> list:
    """Order by created_at descending; insertion order breaks exact ties."""
    indexed = [
        (index, record)
        for index, record in enumerate(records)
        if lot_name is None or record.get("lot_name") == lot_name
    ]
    indexed.sort(
        key=lambda pair: (str(pair[1].get("created_at", "")), pair[0]), reverse=True
    )
    return [record for _, record in indexed]


# --- validation (every failure is one line, exit code 2) ---------------------

def parse_lot_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name:
        raise ValidationError("lot name must not be empty")
    return name


def parse_roast_date(raw: str) -> datetime.date:
    text = (raw or "").strip()
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise ValidationError(
            f"invalid roast date '{text}' (expected YYYY-MM-DD, e.g. 2024-05-01)"
        )


def parse_grams(raw: str, label: str):
    text = (raw or "").strip()
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ValidationError(
            f"{label} must be a positive number of grams (got '{text}')"
        )
    if not math.isfinite(value) or value <= 0:
        raise ValidationError(
            f"{label} must be a positive number of grams (got '{text}')"
        )
    value = round(value, 3)
    return int(value) if value.is_integer() else value


def parse_days(raw: str) -> int:
    text = (raw or "").strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        raise ValidationError(f"--days must be a whole number of days (got '{text}')")


def lot_age_days(lot: dict, today: datetime.date) -> int:
    roast_date = lot.get("roast_date")
    try:
        roasted = datetime.date.fromisoformat(str(roast_date))
    except ValueError:
        raise StoreError(
            f"lot '{lot.get('name', '?')}' has an invalid roast date '{roast_date}'"
        )
    return (today - roasted).days


def resolve_lot_for_write(store: dict, requested: str) -> str:
    """Return the lot name a new record attaches to, never orphaning it."""
    lots = store.get("lots", [])
    if requested is None:
        if not lots:
            raise ValidationError(EMPTY_LOT_HINT)
        if len(lots) > 1:
            names = ", ".join(sorted(str(lot.get("name")) for lot in lots))
            raise ValidationError(
                f"--lot is required when more than one lot exists (lots: {names})"
            )
        return str(lots[0].get("name"))

    name = (requested or "").strip()
    if not name:
        raise ValidationError("--lot must not be empty")
    if find_lot(store, name) is None:
        raise ValidationError(f"unknown lot '{name}' (see: brewbit lot list)")
    return name


# --- commands - one table below resolves every verb --------------------------

def cmd_lot_add(args) -> int:
    name = parse_lot_name(args.name)
    roast_date = parse_roast_date(args.roast_date)
    store = load_store()
    if find_lot(store, name) is not None:
        raise ValidationError(f"lot '{name}' already exists (choose another name)")
    store["lots"].append(
        {
            "name": name,
            "roast_date": roast_date.isoformat(),
            "created_at": utc_now(),
        }
    )
    save_store(store)
    print(f"lot added: {name} (roast date {roast_date.isoformat()})")
    return 0


def cmd_lot_list(args) -> int:
    store = load_store()
    lots = sorted(
        store["lots"], key=lambda lot: (str(lot.get("name", "")).lower(), str(lot.get("name", "")))
    )
    counts = note_counts(store)

    if args.json:
        emit_json(
            [
                {
                    "name": lot.get("name"),
                    "roast_date": lot.get("roast_date"),
                    "created_at": lot.get("created_at"),
                    "note_count": counts.get(lot.get("name"), 0),
                }
                for lot in lots
            ]
        )
        return 0

    if not lots:
        print(EMPTY_LOT_HINT)
        return 0

    rows = [
        [
            str(lot.get("name")),
            str(lot.get("roast_date")),
            str(counts.get(lot.get("name"), 0)),
        ]
        for lot in lots
    ]
    print(render_table(["LOT", "ROAST DATE", "NOTES"], rows))
    return 0


def cmd_lot_show(args) -> int:
    name = parse_lot_name(args.name)
    store = load_store()
    lot = find_lot(store, name)
    if lot is None:
        raise ValidationError(f"unknown lot '{name}' (see: brewbit lot list)")

    shots = newest_first(store["shots"], name)
    notes = newest_first(store["notes"], name)

    if args.json:
        emit_json(
            {
                "name": lot.get("name"),
                "roast_date": lot.get("roast_date"),
                "created_at": lot.get("created_at"),
                "shots": shots,
                "notes": notes,
            }
        )
        return 0

    age = lot_age_days(lot, datetime.date.today())
    facts = [
        ["LOT", str(lot.get("name"))],
        ["ROAST DATE", str(lot.get("roast_date"))],
        ["AGE", f"{age} days"],
        ["SHOTS", str(len(shots))],
        ["NOTES", str(len(notes))],
    ]
    print(_facts_block(facts))
    print()
    print("SHOTS (newest first)")
    show_table_or_hint(
        ["WHEN", "DOSE", "YIELD", "GRIND"],
        [shot_row(shot, with_lot=False) for shot in shots],
        f"  no shots yet: brewbit shot add --lot {lot.get('name')}",
    )
    print()
    print("NOTES (newest first)")
    show_table_or_hint(
        ["WHEN", "NOTE"],
        [note_row(note) for note in notes],
        f"  no notes yet: brewbit note add --lot {lot.get('name')} --text '...'",
    )
    return 0


def _facts_block(facts: list) -> str:
    width = max(len(label) for label, _ in facts)
    return "\n".join(f"{label.ljust(width)}{COLUMN_GUTTER}{value}" for label, value in facts)


def cmd_shot_add(args) -> int:
    dose_g = parse_grams(args.dose, "dose")
    yield_g = parse_grams(args.yield_g, "yield")
    grind_note = one_line(args.grind_note or "") or None
    store = load_store()
    lot_name = resolve_lot_for_write(store, args.lot)
    store["shots"].append(
        {
            "id": uuid.uuid4().hex[:12],
            "lot_name": lot_name,
            "dose_g": dose_g,
            "yield_g": yield_g,
            "grind_note": grind_note,
            "created_at": utc_now(),
        }
    )
    save_store(store)
    suffix = f" ({grind_note})" if grind_note else ""
    print(
        f"shot recorded: {lot_name} dose {format_grams(dose_g)}g "
        f"yield {format_grams(yield_g)}g{suffix}"
    )
    return 0


def cmd_shot_list(args) -> int:
    store = load_store()
    lot_name = None
    if args.lot is not None:
        requested = (args.lot or "").strip()
        if not requested:
            raise ValidationError("--lot must not be empty")
        if find_lot(store, requested) is None:
            raise ValidationError(f"unknown lot '{requested}' (see: brewbit lot list)")
        lot_name = requested

    shots = newest_first(store["shots"], lot_name)

    if args.json:
        emit_json(shots)
        return 0

    if not shots:
        if lot_name:
            print(f"no shots yet for lot '{lot_name}': brewbit shot add --lot {lot_name}")
        else:
            print(EMPTY_SHOT_HINT)
        return 0

    print(
        render_table(
            ["WHEN", "LOT", "DOSE", "YIELD", "GRIND"],
            [shot_row(shot) for shot in shots],
        )
    )
    return 0


def cmd_note_add(args) -> int:
    store = load_store()
    lot_name = resolve_lot_for_write(store, args.lot)
    text = (args.text or "").strip()
    if not text:
        raise ValidationError("note text must not be empty")
    store["notes"].append(
        {
            "id": uuid.uuid4().hex[:12],
            "lot_name": lot_name,
            "text": text,
            "created_at": utc_now(),
        }
    )
    save_store(store)
    print(f'note added: {lot_name} "{shorten(one_line(text))}"')
    return 0


def cmd_stale(args) -> int:
    days = parse_days(args.days)
    store = load_store()
    lots = store["lots"]

    if args.json:
        today = datetime.date.today()
        report = []
        for lot in lots:
            age = lot_age_days(lot, today)
            report.append(
                {
                    "name": lot.get("name"),
                    "roast_date": lot.get("roast_date"),
                    "age_days": age,
                    "status": "STALE" if age > days else "FRESH",
                }
            )
        report.sort(key=lambda row: (-row["age_days"], str(row["name"]).lower()))
        emit_json(report)
        return 0

    if not lots:
        print(EMPTY_LOT_HINT)
        return 0

    today = datetime.date.today()
    report = []
    for lot in lots:
        age = lot_age_days(lot, today)
        report.append((age, lot))
    report.sort(key=lambda pair: (-pair[0], str(pair[1].get("name", "")).lower()))

    rows = [
        [
            "STALE" if age > days else "FRESH",
            str(lot.get("name")),
            str(lot.get("roast_date")),
            f"{age} days",
        ]
        for age, lot in report
    ]
    print(render_table(["STATUS", "LOT", "ROAST DATE", "AGE"], rows))
    stale_count = sum(1 for age, _ in report if age > days)
    print()
    print(
        f"{stale_count} of {len(report)} lots past the {days}-day window "
        f"(change it with: brewbit stale --days N)"
    )
    return 0


# One module-level command table: every CLI verb resolves here.
COMMANDS = {
    "lot add": cmd_lot_add,
    "lot list": cmd_lot_list,
    "lot show": cmd_lot_show,
    "shot add": cmd_shot_add,
    "shot list": cmd_shot_list,
    "note add": cmd_note_add,
    "stale": cmd_stale,
}


# --- argument parsing --------------------------------------------------------

class BrewBitParser(argparse.ArgumentParser):
    """Argparse with one-line errors: a usage dump buries the actual mistake."""

    def error(self, message: str) -> None:
        self.exit(2, f"error: {message}\n")


def _leaf(group, name: str, command_key: str, help_text: str):
    """Register one leaf subcommand and wire it to the command table."""
    leaf = group.add_parser(name, help=help_text)
    leaf.set_defaults(command_key=command_key)
    return leaf


def _json_flag(leaf) -> None:
    leaf.add_argument("--json", action="store_true", help="machine-readable output")


def build_parser() -> argparse.ArgumentParser:
    parser = BrewBitParser(
        prog=PROGRAM,
        description="Track espresso dosing, tasting notes, and green-coffee freshness. "
        "State lives in brewbit.json beside the script.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"{PROGRAM} {VERSION}")
    groups = parser.add_subparsers(dest="group", metavar="<command>")

    lot = groups.add_parser(
        "lot", help="bean lots: add, list, show",
        description="Bean lots are what shots and tasting notes attach to.",
    )
    actions = lot.add_subparsers(dest="action", metavar="<action>", required=True)
    leaf = _leaf(actions, "add", "lot add", "register a bean lot (unique name)")
    leaf.add_argument("--name", required=True, metavar="NAME", help="unique lot name")
    leaf.add_argument("--roast-date", required=True, metavar="YYYY-MM-DD", help="ISO roast date")
    leaf = _leaf(actions, "list", "lot list", "list lots with roast date and note count")
    _json_flag(leaf)
    leaf = _leaf(actions, "show", "lot show", "show one lot with its shots and notes")
    leaf.add_argument("name", metavar="NAME", help="lot name")
    _json_flag(leaf)

    shot = groups.add_parser(
        "shot", help="espresso shots: add, list",
        description="Every shot belongs to a bean lot and records dose and yield in grams.",
    )
    actions = shot.add_subparsers(dest="action", metavar="<action>", required=True)
    leaf = _leaf(actions, "add", "shot add", "record a shot")
    leaf.add_argument("--lot", default=None, metavar="NAME",
                      help="bean lot name (optional when exactly one lot exists)")
    leaf.add_argument("--dose", required=True, metavar="GRAMS", help="dose in grams")
    leaf.add_argument("--yield", dest="yield_g", required=True, metavar="GRAMS", help="yield in grams")
    leaf.add_argument("--grind", dest="grind_note", default=None, metavar="TEXT", help="grind / time note")
    leaf = _leaf(actions, "list", "shot list", "list shots newest first")
    leaf.add_argument("--lot", default=None, metavar="NAME", help="only this lot")
    _json_flag(leaf)

    note = groups.add_parser(
        "note", help="tasting notes: add",
        description="Tasting notes are free text and attach to an existing bean lot.",
    )
    actions = note.add_subparsers(dest="action", metavar="<action>", required=True)
    leaf = _leaf(actions, "add", "note add", "add a tasting note to a lot")
    leaf.add_argument("--lot", required=True, metavar="NAME", help="bean lot name")
    leaf.add_argument("--text", required=True, metavar="TEXT", help="tasting note text")

    stale = groups.add_parser(
        "stale", help="freshness report by roast date",
        description="Mark each lot STALE or FRESH from its roast date versus today.",
    )
    stale.set_defaults(command_key="stale")
    stale.add_argument("--days", default=str(DEFAULT_STALE_DAYS), metavar="N",
                       help=f"freshness window in days (default {DEFAULT_STALE_DAYS})")
    _json_flag(stale)

    return parser


def main(argv: list = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    command_key = getattr(args, "command_key", None)
    if command_key is None:
        parser.print_help()
        return 0

    try:
        return COMMANDS[command_key](args) or 0
    except BrewBitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code


def run() -> int:
    """Entry point wrapper: a Ctrl-C or a closed pipe is not a crash."""
    try:
        return main()
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:
        # `brewbit shot list | head -1` closes stdout early; not an error.
        with contextlib.suppress(OSError):
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(run())
