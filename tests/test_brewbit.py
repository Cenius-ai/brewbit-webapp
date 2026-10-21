"""Tests for BrewBit: every command, its happy path and its failure paths.

Run with either:
    python3 -m unittest discover -s tests -v
    python3 -m pytest tests -q
"""

import contextlib
import datetime
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import brewbit  # noqa: E402  (path set up above)


def days_ago(days: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


class BrewBitTestCase(unittest.TestCase):
    """Runs the real CLI in-process against a throwaway store per test."""

    def setUp(self) -> None:
        self._original_store_path = brewbit.STORE_PATH
        self.tmpdir = tempfile.mkdtemp(prefix="brewbit-test-")
        brewbit.STORE_PATH = os.path.join(self.tmpdir, "brewbit.json")

    def tearDown(self) -> None:
        brewbit.STORE_PATH = self._original_store_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def run_cli(self, *argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                code = brewbit.main(list(argv))
            except SystemExit as exc:  # --help / --version exit through argparse
                code = exc.code if isinstance(exc.code, int) else 1
        return code, stdout.getvalue(), stderr.getvalue()

    def run_ok(self, *argv) -> str:
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 0, msg=f"{argv} failed: {err}")
        self.assertEqual(err, "", msg=f"{argv} wrote to stderr: {err}")
        return out

    def run_fail(self, *argv, exit_code: int = 2) -> str:
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, exit_code, msg=f"{argv} exited {code}, wanted {exit_code}")
        self.assertEqual(out, "", msg=f"{argv} wrote to stdout on failure: {out}")
        lines = err.strip().splitlines()
        self.assertEqual(len(lines), 1, msg=f"{argv} error was not one line: {err!r}")
        self.assertTrue(lines[0].startswith("error: "), msg=lines[0])
        return lines[0]

    def add_lot(self, name: str = "Kenya-AA-Nyeri", age_days: int = 10) -> str:
        self.run_ok("lot", "add", "--name", name, "--roast-date", days_ago(age_days))
        return name

    def store(self) -> dict:
        return brewbit.load_store()

    def write_store(self, document: dict) -> None:
        with open(brewbit.STORE_PATH, "w", encoding="utf-8") as handle:
            json.dump(document, handle)


class TestCliSurface(BrewBitTestCase):
    def test_version_flag(self):
        out = self.run_ok("--version")
        self.assertEqual(out.strip(), f"brewbit {brewbit.VERSION}")

    def test_bare_invocation_prints_help_and_exits_zero(self):
        out = self.run_ok()
        self.assertIn("lot", out)
        self.assertIn("stale", out)
        self.assertIn("examples:", out)

    def test_help_lists_every_command(self):
        out = self.run_ok("--help")
        for verb in ("lot", "shot", "note", "stale"):
            self.assertIn(verb, out)

    def test_subcommand_help_exits_zero(self):
        group_help = self.run_ok("shot", "--help")
        self.assertIn("add", group_help)
        for action in ("shot", "lot", "note"):
            out = self.run_ok(action, "add", "--help")
            self.assertIn("--help", out)
        add_help = self.run_ok("shot", "add", "--help")
        self.assertIn("--dose", add_help)
        self.assertIn("--yield", add_help)

    def test_missing_required_flag_is_one_line_error(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
            brewbit.main(["lot", "add", "--name", "Kenya-AA-Nyeri"])
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(len(stderr.getvalue().strip().splitlines()), 1)
        self.assertTrue(stderr.getvalue().startswith("error: "))

    def test_module_runs_as_a_script(self):
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "brewbit.py"), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("brewbit", result.stdout)


class TestLotCommands(BrewBitTestCase):
    def test_lot_add_creates_store_beside_script(self):
        stdout = self.run_ok(
            "lot", "add", "--name", "Kenya-AA-Nyeri", "--roast-date", "2024-05-01"
        )
        self.assertIn("Kenya-AA-Nyeri", stdout)
        self.assertEqual(len(stdout.strip().splitlines()), 1)
        self.assertTrue(os.path.exists(brewbit.STORE_PATH))
        stored = self.store()
        self.assertEqual(len(stored["lots"]), 1)
        self.assertEqual(stored["lots"][0]["name"], "Kenya-AA-Nyeri")
        self.assertEqual(stored["lots"][0]["roast_date"], "2024-05-01")
        self.assertTrue(stored["lots"][0]["created_at"].endswith("Z"))

    def test_lot_add_duplicate_name_rejected_and_original_kept(self):
        self.add_lot("Kenya-AA-Nyeri")
        message = self.run_fail(
            "lot", "add", "--name", "Kenya-AA-Nyeri", "--roast-date", "2024-06-01"
        )
        self.assertIn("already exists", message)
        lots = self.store()["lots"]
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0]["roast_date"], days_ago(10))

    def test_lot_add_rejects_bad_roast_date_without_creating_a_lot(self):
        message = self.run_fail(
            "lot", "add", "--name", "Kenya-AA-Nyeri", "--roast-date", "not-a-date"
        )
        self.assertIn("invalid roast date", message)
        self.assertFalse(os.path.exists(brewbit.STORE_PATH))

    def test_lot_add_rejects_day_first_date(self):
        self.run_fail("lot", "add", "--name", "X", "--roast-date", "01/05/2024")

    def test_lot_add_rejects_blank_name(self):
        message = self.run_fail(
            "lot", "add", "--name", "   ", "--roast-date", "2024-05-01"
        )
        self.assertIn("must not be empty", message)
        self.assertEqual(self.store()["lots"], [])

    def test_lot_list_empty_state_hint(self):
        self.assertEqual(self.run_ok("lot", "list").strip(), "no lots yet: brewbit lot add")

    def test_lot_list_shows_roast_date_and_note_count(self):
        self.add_lot("Kenya-AA-Nyeri", 12)
        self.add_lot("Brazil-Cerrado", 40)
        self.run_ok("note", "add", "--lot", "Kenya-AA-Nyeri", "--text", "bright")
        self.run_ok("note", "add", "--lot", "Kenya-AA-Nyeri", "--text", "berry finish")

        out = self.run_ok("lot", "list")
        lines = out.strip().splitlines()
        self.assertEqual(lines[0].split(), ["LOT", "ROAST", "DATE", "NOTES"])
        self.assertEqual(len(lines), 4)  # header, rule, two lots
        self.assertIn("Kenya-AA-Nyeri", out)
        self.assertIn(days_ago(12), out)
        self.assertIn(days_ago(40), out)
        kenya = next(line for line in lines if "Kenya-AA-Nyeri" in line)
        self.assertEqual(kenya.split()[-1], "2")

    def test_lot_list_json(self):
        self.add_lot("Kenya-AA-Nyeri", 7)
        payload = json.loads(self.run_ok("lot", "list", "--json"))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["name"], "Kenya-AA-Nyeri")
        self.assertEqual(payload[0]["note_count"], 0)

    def test_lot_show_prints_lot_with_shots_and_notes(self):
        self.add_lot("Kenya-AA-Nyeri", 5)
        self.run_ok("shot", "add", "--lot", "Kenya-AA-Nyeri", "--dose", "18", "--yield", "36")
        self.run_ok("note", "add", "--lot", "Kenya-AA-Nyeri", "--text", "cocoa, plum")

        out = self.run_ok("lot", "show", "Kenya-AA-Nyeri")
        self.assertIn("Kenya-AA-Nyeri", out)
        self.assertIn("ROAST DATE", out)
        self.assertIn(days_ago(5), out)
        self.assertIn("SHOTS (newest first)", out)
        self.assertIn("18g", out)
        self.assertIn("NOTES (newest first)", out)
        self.assertIn("cocoa, plum", out)

    def test_lot_show_empty_sub_sections_hint_at_the_command(self):
        self.add_lot("Kenya-AA-Nyeri", 3)
        out = self.run_ok("lot", "show", "Kenya-AA-Nyeri")
        self.assertIn("brewbit shot add", out)
        self.assertIn("brewbit note add", out)

    def test_lot_show_unknown_lot_fails(self):
        self.add_lot("Kenya-AA-Nyeri")
        message = self.run_fail("lot", "show", "MissingLot")
        self.assertIn("unknown lot 'MissingLot'", message)


class TestShotCommands(BrewBitTestCase):
    def test_shot_add_stores_dose_and_yield_and_lists_them(self):
        self.add_lot("Kenya-AA-Nyeri")
        stdout = self.run_ok(
            "shot", "add", "--lot", "Kenya-AA-Nyeri", "--dose", "18", "--yield", "36.5"
        )
        self.assertIn("Kenya-AA-Nyeri", stdout)
        self.assertEqual(len(stdout.strip().splitlines()), 1)

        stored = self.store()["shots"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["dose_g"], 18)
        self.assertEqual(stored[0]["yield_g"], 36.5)
        self.assertIsNone(stored[0]["grind_note"])

        out = self.run_ok("shot", "list")
        self.assertIn("Kenya-AA-Nyeri", out)
        self.assertIn("18g", out)
        self.assertIn("36.5g", out)
        self.assertIn(stored[0]["created_at"], out)

    def test_shot_add_stores_grind_note(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.run_ok(
            "shot",
            "add",
            "--lot",
            "Kenya-AA-Nyeri",
            "--dose",
            "18",
            "--yield",
            "36",
            "--grind",
            "fine, 28s",
        )
        self.assertEqual(self.store()["shots"][0]["grind_note"], "fine, 28s")
        self.assertIn("fine, 28s", self.run_ok("shot", "list"))

    def test_shot_list_empty_state_hint(self):
        self.assertEqual(
            self.run_ok("shot", "list").strip(), "add your first shot: brewbit shot add"
        )

    def test_shot_list_is_newest_first(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.write_store(
            {
                "version": 1,
                "lots": [
                    {
                        "name": "Kenya-AA-Nyeri",
                        "roast_date": days_ago(3),
                        "created_at": "2024-05-01T00:00:00.000000Z",
                    }
                ],
                "shots": [
                    {
                        "id": "older0000001",
                        "lot_name": "Kenya-AA-Nyeri",
                        "dose_g": 17,
                        "yield_g": 34,
                        "grind_note": "old",
                        "created_at": "2024-05-02T07:00:00.000000Z",
                    },
                    {
                        "id": "newer0000001",
                        "lot_name": "Kenya-AA-Nyeri",
                        "dose_g": 18,
                        "yield_g": 36,
                        "grind_note": "new",
                        "created_at": "2024-05-09T07:00:00.000000Z",
                    },
                ],
                "notes": [],
            }
        )
        out = self.run_ok("shot", "list")
        self.assertLess(out.index("new"), out.index("old"))

    def test_shot_add_rejects_non_numeric_dose_and_yield(self):
        self.add_lot("Kenya-AA-Nyeri")
        dose_message = self.run_fail(
            "shot", "add", "--lot", "Kenya-AA-Nyeri", "--dose", "abc", "--yield", "36"
        )
        self.assertIn("dose must be a positive number", dose_message)
        yield_message = self.run_fail(
            "shot", "add", "--lot", "Kenya-AA-Nyeri", "--dose", "18", "--yield", "abc"
        )
        self.assertIn("yield must be a positive number", yield_message)
        self.assertEqual(self.store()["shots"], [])

    def test_shot_add_rejects_zero_and_negative_grams(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.run_fail("shot", "add", "--lot", "Kenya-AA-Nyeri", "--dose", "0", "--yield", "36")
        self.run_fail("shot", "add", "--lot", "Kenya-AA-Nyeri", "--dose", "18", "--yield", "-1")
        self.assertEqual(self.store()["shots"], [])

    def test_shot_add_rejects_unknown_lot(self):
        self.add_lot("Kenya-AA-Nyeri")
        message = self.run_fail(
            "shot", "add", "--lot", "MissingLot", "--dose", "18", "--yield", "36"
        )
        self.assertIn("unknown lot 'MissingLot'", message)
        self.assertEqual(self.store()["shots"], [])

    def test_shot_add_defaults_to_the_single_lot(self):
        self.add_lot("Kenya-AA-Nyeri")
        stdout = self.run_ok("shot", "add", "--dose", "18", "--yield", "36")
        self.assertIn("Kenya-AA-Nyeri", stdout)
        self.assertEqual(self.store()["shots"][0]["lot_name"], "Kenya-AA-Nyeri")

    def test_shot_add_requires_lot_when_several_exist(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.add_lot("Brazil-Cerrado")
        message = self.run_fail("shot", "add", "--dose", "18", "--yield", "36")
        self.assertIn("--lot is required", message)
        self.assertIn("Brazil-Cerrado", message)
        self.assertEqual(self.store()["shots"], [])

    def test_shot_add_without_any_lot_points_at_lot_add(self):
        message = self.run_fail("shot", "add", "--dose", "18", "--yield", "36")
        self.assertIn("no lots yet: brewbit lot add", message)

    def test_shot_list_filters_by_lot(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.add_lot("Brazil-Cerrado")
        self.run_ok("shot", "add", "--lot", "Kenya-AA-Nyeri", "--dose", "18", "--yield", "36")
        self.run_ok("shot", "add", "--lot", "Brazil-Cerrado", "--dose", "20", "--yield", "40")

        out = self.run_ok("shot", "list", "--lot", "Kenya-AA-Nyeri")
        self.assertIn("Kenya-AA-Nyeri", out)
        self.assertNotIn("Brazil-Cerrado", out)

    def test_shot_list_filter_on_existing_lot_without_shots(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.add_lot("Brazil-Cerrado")
        out = self.run_ok("shot", "list", "--lot", "Brazil-Cerrado")
        self.assertIn("no shots yet for lot 'Brazil-Cerrado'", out)

    def test_shot_list_filter_unknown_lot_fails(self):
        self.add_lot("Kenya-AA-Nyeri")
        message = self.run_fail("shot", "list", "--lot", "MissingLot")
        self.assertIn("unknown lot 'MissingLot'", message)

    def test_shot_list_json(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.run_ok("shot", "add", "--lot", "Kenya-AA-Nyeri", "--dose", "18", "--yield", "36")
        payload = json.loads(self.run_ok("shot", "list", "--json"))
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["dose_g"], 18)


class TestNoteCommands(BrewBitTestCase):
    def test_note_add_appears_in_note_count(self):
        self.add_lot("Kenya-AA-Nyeri")
        stdout = self.run_ok(
            "note", "add", "--lot", "Kenya-AA-Nyeri", "--text", "bright, berry finish"
        )
        self.assertIn("Kenya-AA-Nyeri", stdout)
        self.assertEqual(len(stdout.strip().splitlines()), 1)

        stored = self.store()["notes"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["text"], "bright, berry finish")
        self.assertTrue(stored[0]["created_at"].endswith("Z"))

        listing = self.run_ok("lot", "list")
        kenya = next(line for line in listing.splitlines() if "Kenya-AA-Nyeri" in line)
        self.assertEqual(kenya.split()[-1], "1")

    def test_note_add_rejects_unknown_lot(self):
        self.add_lot("Kenya-AA-Nyeri")
        message = self.run_fail("note", "add", "--lot", "MissingLot", "--text", "x")
        self.assertIn("unknown lot 'MissingLot'", message)
        self.assertEqual(self.store()["notes"], [])

    def test_note_add_rejects_empty_text(self):
        self.add_lot("Kenya-AA-Nyeri")
        message = self.run_fail("note", "add", "--lot", "Kenya-AA-Nyeri", "--text", "")
        self.assertIn("note text must not be empty", message)
        self.assertEqual(self.store()["notes"], [])

    def test_note_add_rejects_whitespace_only_text(self):
        self.add_lot("Kenya-AA-Nyeri")
        message = self.run_fail("note", "add", "--lot", "Kenya-AA-Nyeri", "--text", "   ")
        self.assertIn("note text must not be empty", message)
        self.assertEqual(self.store()["notes"], [])

    def test_note_text_keeps_one_line_per_record(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.run_ok("note", "add", "--lot", "Kenya-AA-Nyeri", "--text", "line one\nline two")
        out = self.run_ok("lot", "show", "Kenya-AA-Nyeri")
        note_rows = [
            line for line in out.splitlines() if line.startswith("20") and "line" in line
        ]
        self.assertEqual(len(note_rows), 1)
        self.assertIn("line one line two", note_rows[0])


class TestStaleReport(BrewBitTestCase):
    def test_default_window_flags_only_lots_past_thirty_days(self):
        self.add_lot("Kenya-AA-Nyeri", 29)
        self.add_lot("Brazil-Cerrado", 31)
        out = self.run_ok("stale")
        rows = {line.split()[1]: line for line in out.splitlines() if line.startswith(("STALE", "FRESH"))}
        self.assertTrue(rows["Kenya-AA-Nyeri"].startswith("FRESH"))
        self.assertIn("29 days", rows["Kenya-AA-Nyeri"])
        self.assertTrue(rows["Brazil-Cerrado"].startswith("STALE"))
        self.assertIn("31 days", rows["Brazil-Cerrado"])

    def test_custom_window_narrower_and_wider(self):
        self.add_lot("Kenya-AA-Nyeri", 5)
        self.add_lot("Brazil-Cerrado", 45)

        narrow = self.run_ok("stale", "--days", "10")
        narrow_rows = {
            line.split()[1]: line.split()[0]
            for line in narrow.splitlines()
            if line.startswith(("STALE", "FRESH"))
        }
        self.assertEqual(narrow_rows["Kenya-AA-Nyeri"], "FRESH")
        self.assertEqual(narrow_rows["Brazil-Cerrado"], "STALE")

        wide = self.run_ok("stale", "--days", "60")
        wide_rows = {
            line.split()[1]: line.split()[0]
            for line in wide.splitlines()
            if line.startswith(("STALE", "FRESH"))
        }
        self.assertEqual(wide_rows["Kenya-AA-Nyeri"], "FRESH")
        self.assertEqual(wide_rows["Brazil-Cerrado"], "FRESH")

    def test_empty_state_hint(self):
        self.assertEqual(self.run_ok("stale").strip(), "no lots yet: brewbit lot add")

    def test_non_integer_days_fails(self):
        self.add_lot("Kenya-AA-Nyeri")
        message = self.run_fail("stale", "--days", "abc")
        self.assertIn("--days", message)

    def test_report_is_ordered_stalest_first(self):
        self.add_lot("Kenya-AA-Nyeri", 5)
        self.add_lot("Brazil-Cerrado", 45)
        out = self.run_ok("stale")
        self.assertLess(out.index("Brazil-Cerrado"), out.index("Kenya-AA-Nyeri"))

    def test_json_report(self):
        self.add_lot("Kenya-AA-Nyeri", 31)
        payload = json.loads(self.run_ok("stale", "--json"))
        self.assertEqual(payload[0]["status"], "STALE")
        self.assertEqual(payload[0]["age_days"], 31)


class TestStoreSafety(BrewBitTestCase):
    def test_store_is_pretty_printed_json_with_version(self):
        self.add_lot("Kenya-AA-Nyeri")
        with open(brewbit.STORE_PATH, "r", encoding="utf-8") as handle:
            raw = handle.read()
        self.assertIn("\n", raw)
        self.assertEqual(json.loads(raw)["version"], 1)

    def test_no_temp_files_left_behind(self):
        self.add_lot("Kenya-AA-Nyeri")
        self.run_ok("shot", "add", "--dose", "18", "--yield", "36")
        leftovers = [name for name in os.listdir(self.tmpdir) if name != "brewbit.json"]
        self.assertEqual(leftovers, [])

    def test_corrupt_store_reports_one_line_instead_of_a_traceback(self):
        with open(brewbit.STORE_PATH, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        message = self.run_fail("lot", "list", exit_code=1)
        self.assertIn("not valid JSON", message)

    def test_hand_edited_bad_roast_date_reports_one_line(self):
        self.write_store(
            {
                "version": 1,
                "lots": [
                    {"name": "Kenya-AA-Nyeri", "roast_date": "someday", "created_at": "x"}
                ],
                "shots": [],
                "notes": [],
            }
        )
        message = self.run_fail("stale", exit_code=1)
        self.assertIn("invalid roast date", message)

    def test_every_command_survives_an_empty_store(self):
        for argv in (
            ["lot", "list"],
            ["shot", "list"],
            ["stale"],
            ["lot", "list", "--json"],
            ["shot", "list", "--json"],
            ["stale", "--json"],
        ):
            self.run_ok(*argv)


class TestEndToEnd(unittest.TestCase):
    """Runs a real subprocess against a copy of the script in a temp dir."""

    def test_fresh_checkout_round_trip_writes_the_store_beside_the_script(self):
        tmpdir = tempfile.mkdtemp(prefix="brewbit-e2e-")
        try:
            script = os.path.join(tmpdir, "brewbit.py")
            shutil.copy(str(PROJECT_ROOT / "brewbit.py"), script)

            def run(*argv):
                return subprocess.run(
                    [sys.executable, script, *argv],
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=tmpdir,
                )

            added = run("lot", "add", "--name", "Kenya-AA-Nyeri", "--roast-date", "2024-05-01")
            self.assertEqual(added.returncode, 0, added.stderr)
            store_path = os.path.join(tmpdir, "brewbit.json")
            self.assertTrue(os.path.exists(store_path))

            shot = run("shot", "add", "--dose", "18", "--yield", "36")
            self.assertEqual(shot.returncode, 0, shot.stderr)
            self.assertIn("Kenya-AA-Nyeri", shot.stdout)

            note = run("note", "add", "--lot", "Kenya-AA-Nyeri", "--text", "syrupy body")
            self.assertEqual(note.returncode, 0, note.stderr)

            listing = run("lot", "list")
            self.assertEqual(listing.returncode, 0)
            self.assertIn("Kenya-AA-Nyeri", listing.stdout)

            failure = run("shot", "add", "--dose", "heavy", "--yield", "36")
            self.assertEqual(failure.returncode, 2)
            self.assertTrue(failure.stderr.startswith("error: "))
            self.assertEqual(failure.stdout, "")

            with open(store_path, "r", encoding="utf-8") as handle:
                document = json.loads(handle.read())
            self.assertEqual(len(document["lots"]), 1)
            self.assertEqual(len(document["shots"]), 1)
            self.assertEqual(len(document["notes"]), 1)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
