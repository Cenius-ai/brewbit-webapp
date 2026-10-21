# Installing BrewBit

BrewBit is a single Python file with no third-party dependencies. "Installing" it means
making sure Python 3.8+ is available and optionally putting the `brewbit` command on
your `PATH`. Nothing here needs root, a virtualenv, or a network connection: no step
below contacts a package index, at build time or at run time.

## Prerequisites (system, installed by you — never by this repo's scripts)

| Requirement | Why | Check |
| --- | --- | --- |
| Python 3.8 or newer | the interpreter BrewBit runs on | `python3 --version` |
| `pip` (bundled with Python) | only for the optional offline console-script install | `python3 -m pip --version` |

No compilers, database servers, or system libraries are required.

## Step 1 — install

```bash
bash install.sh
```

`install.sh` verifies the interpreter, imports the module, runs the `--help` smoke check
and the test suite, prints a summary, and **exits**. It installs nothing and downloads
nothing — no package index is contacted, because there is nothing to fetch. It never
starts a server or a long-running process, and it is safe to re-run.

The tool is then ready to use straight from the checkout:

```bash
python3 brewbit.py --version
```

### Step 1b (optional) — the `brewbit` console script

If you would rather type `brewbit` than `python3 brewbit.py`, install the console script
from the local tree. `--no-index` keeps this offline; `--no-build-isolation` uses the
setuptools you already have:

```bash
python3 -m pip install --no-index --no-build-isolation -e .
brewbit --version
```

## Step 2 — see it working (demo data)

```bash
bash demo.sh
```

This is BrewBit's "seed data" step. It copies the script into a throwaway temp
directory and drives every command there, so it shows a fully populated lot list, shot
list, lot detail view, and freshness report — and then the rejection paths. Your own
`brewbit.json` is not created or modified by the demo.

## Step 3 — use it for real

```bash
python3 brewbit.py lot add --name Kenya-AA-Nyeri --roast-date 2024-05-01
python3 brewbit.py shot add --lot Kenya-AA-Nyeri --dose 18 --yield 36
python3 brewbit.py note add --lot Kenya-AA-Nyeri --text "blackcurrant, cane sugar"
python3 brewbit.py lot list
python3 brewbit.py shot list
python3 brewbit.py stale
```

(With the console script installed, replace `python3 brewbit.py` with `brewbit`.)

The first write creates `brewbit.json` beside `brewbit.py`. There is no config file, no
environment variable, and no secret to set.

## Step 4 — verify

```bash
python3 -m unittest discover -s tests -v
```

## Uninstall

Delete `brewbit.py`, `brewbit.json`, and — if you installed it — run
`python3 -m pip uninstall brewbit`. Nothing is written outside the project directory
except the console-script entry in your Python environment.
