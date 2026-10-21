# Using BrewBit

Every walkthrough below is copy-pasteable and matches what the tool actually prints.
`brewbit` below means `python3 brewbit.py` (or the installed console script).

## 1. Register the beans you bought

```bash
brewbit lot add --name Kenya-AA-Nyeri --roast-date 2024-05-01
# lot added: Kenya-AA-Nyeri (roast date 2024-05-01)
```

The name is the handle every shot and note attaches to, so it must be unique and the
roast date must be a real ISO date:

```bash
brewbit lot add --name Kenya-AA-Nyeri --roast-date 2024-06-01
# error: lot 'Kenya-AA-Nyeri' already exists (choose another name)
# exit code 2, the original lot is untouched

brewbit lot add --name Brazil-Cerrado --roast-date 05/01/2024
# error: invalid roast date '05/01/2024' (expected YYYY-MM-DD, e.g. 2024-05-01)
# exit code 2, no lot is created
```

See what you have. The note count comes straight from the notes you logged:

```bash
brewbit lot list
# LOT             ROAST DATE  NOTES
# --------------  ----------  -----
# Brazil-Cerrado  2024-09-17  1
# Kenya-AA-Nyeri  2024-10-18  2
```

With no lots yet, the listing tells you the next command to run:

```
no lots yet: brewbit lot add
```

## 2. Log every shot

Dose and yield are grams and must be positive numbers:

```bash
brewbit shot add --lot Kenya-AA-Nyeri --dose 18 --yield 36 --grind "fine, 28s"
# shot recorded: Kenya-AA-Nyeri dose 18g yield 36g (fine, 28s)
```

`--grind` is optional free text for the grind setting or the shot time. When exactly one
lot exists you can drop `--lot`; with several lots BrewBit asks which one instead of
guessing.

Shots list newest first, with the timestamp that decided the order:

```bash
brewbit shot list
# WHEN                         LOT             DOSE   YIELD  GRIND
# ---------------------------  --------------  -----  -----  ----------
# 2024-10-21T07:14:02.881666Z  Kenya-AA-Nyeri  18.5g  41g    finer, 32s
# 2024-10-21T07:11:45.127003Z  Kenya-AA-Nyeri  18g    36g    fine, 28s
```

Filter to one lot, or read the raw records:

```bash
brewbit shot list --lot Kenya-AA-Nyeri
brewbit shot list --json
```

Rejections are one line each and store nothing:

```bash
brewbit shot add --lot Kenya-AA-Nyeri --dose heavy --yield 36
# error: dose must be a positive number of grams (got 'heavy')

brewbit shot add --lot Missing-Lot --dose 18 --yield 36
# error: unknown lot 'Missing-Lot' (see: brewbit lot list)

brewbit shot add --lot Kenya-AA-Nyeri --dose 0 --yield 36
# error: dose must be a positive number of grams (got '0')
```

## 3. Write down what it tasted like

```bash
brewbit note add --lot Kenya-AA-Nyeri --text "blackcurrant, cane sugar, long finish"
# note added: Kenya-AA-Nyeri "blackcurrant, cane sugar, long finish"
```

Blank notes are rejected (`error: note text must not be empty`), and so is a note for a
lot you never registered. Notes show up in `lot list` as a count and in `lot show` in
full.

## 4. Check what is getting old

```bash
brewbit stale
# STATUS  LOT             ROAST DATE  AGE
# ------  --------------  ----------  --------
# STALE   Brazil-Cerrado  2024-09-17  34 days
# FRESH   Kenya-AA-Nyeri  2024-10-18  3 days
#
# 1 of 2 lots past the 30-day window (change it with: brewbit stale --days N)
```

`--days` sets the window (a whole number of days):

```bash
brewbit stale --days 7      # tighter window for a fast-moving cafe
brewbit stale --days 60     # did a bag get forgotten at the back?
brewbit stale --days soon
# error: --days must be a whole number of days (got 'soon')
```

A lot is `STALE` only when it is *past* the window; a lot roasted exactly `--days` ago
is still `FRESH`. The report is ordered stalest first and spells its status out, so it
is readable in a log file or a pipe.

## 5. One screen per lot

```bash
brewbit lot show Kenya-AA-Nyeri
# LOT         Kenya-AA-Nyeri
# ROAST DATE  2024-10-18
# AGE         3 days
# SHOTS       2
# NOTES       2
#
# SHOTS (newest first)
# WHEN                         DOSE   YIELD  GRIND
# ---------------------------  -----  -----  ----------
# 2024-10-21T07:14:02.881666Z  18.5g  41g    finer, 32s
# ...
#
# NOTES (newest first)
# WHEN                         NOTE
# ---------------------------  -----------------------------------------
# 2024-10-21T07:20:11.004412Z  blackcurrant, cane sugar, long finish
```

With nothing logged for that lot yet, each section prints the command that fills it.

## 6. Script it

`--json` on `lot list`, `lot show`, `shot list` and `stale` prints the same records as
stable JSON on stdout, while errors keep going to stderr with a non-zero exit — so
`brewbit shot list --json | jq .` behaves the way you expect.

```bash
brewbit stale --json | jq -r '.[] | select(.status=="STALE") | .name'
```

## Getting out of trouble

| Symptom | Meaning | Fix |
| --- | --- | --- |
| `error: store '...' is not valid JSON` | the store file was hand-edited into invalid JSON | move it aside (or restore it) — BrewBit will not silently discard your log |
| `error: lot '...' has an invalid roast date` | the store contains a malformed date | fix that record in `brewbit.json` |
| `error: cannot write store '...'` | the file or its directory is not writable | check permissions on the directory holding `brewbit.py` |
| `error: unknown lot '...' (see: brewbit lot list)` | the lot name does not exist yet | `brewbit lot add` it first, or use the exact listed name |

All four print one line to stderr with a non-zero exit; none of them print a traceback,
and none of them leave a partially written store behind.
