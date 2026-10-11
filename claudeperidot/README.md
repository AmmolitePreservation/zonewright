# claudeperidot

claudeperidot is Claude's own EQEmu test server in the owner's home lab: a faithful copy of Peridot (the same EQEmu build, akk-stack, quests, maps and database) on VM 143 on labradorite, at `claudeperidot.strata.unseencolor.com` (10.111.111.235), LAN only and never published. Claude has GM status there, a spare RoF2 client (`S:\Research\EverQuest\housingclient`) and a control link (`PhoenixCampfire/claudelink`), and experiments freely. The owner's client, install and MacroQuest setup stay the owner's.

This folder is the change ledger: every change to claudeperidot, and to Claude's side of the workstation, is written here and committed before it runs, so it can be checked, reverted, and carried to Peridot when it is meant for production. `plan.md` holds the goal, the ordered steps, the step in progress and the next one.

## Layout

```
claudeperidot/
  README.md       this file
  plan.md         goal, ordered steps, step in progress, next step; rewritten, never appended
  baseline.yaml   what the copy came from (written by seed --baseline at plan step 7)
  changes/
    template/     the starting point for a new change
    NNNN-<name>/
      change.yaml what the change is (fields below)
      apply.sql   db: the change; rows in one transaction, schema one DDL statement per step
      check.sql   db: one row with a column named applied: 1 or 0, or the steps done for a schema change
      revert.sql  db: written by stage from the saved rows; hand-written for a schema change
      saved.json  written by stage: the rows (or file hashes, or table definitions) the change touches, and their checksum
      applied.json written by apply: the checksums the rows and tables had after it ran
      showCreate/ written by stage for a schema change: SHOW CREATE TABLE of each table before it
      peridot/    written by promote: the change's Peridot form
  tools/ledger.py status | stage | apply | revert | drift | promote | seed
  tests/          the ledger's tests
```

## Running the ledger

From the zonewright repository root, on the `claude` branch, with Python 3.14:

```
python claudeperidot/tools/ledger.py status
python claudeperidot/tools/ledger.py stage 0006
python claudeperidot/tools/ledger.py apply 0006
python claudeperidot/tools/ledger.py revert 0006
python claudeperidot/tools/ledger.py drift
python claudeperidot/tools/ledger.py promote 0006 [--manifestVersion N]
python claudeperidot/tools/ledger.py seed --copyScript <path>
python claudeperidot/tools/ledger.py seed --baseline
```

A change is named by its number or its folder name. The ledger needs PyYAML (`requirements.txt`). For anything that reaches claudeperidot it also needs facet's client (`pip install -e` of `rbraunm/mcps/facet`) and the `facetInventory` environment variable set to facet's inventory, as the facet MCP server has it; it connects as Claude to the inventory's `claudeperidot` host. It logs to `%LOCALAPPDATA%\claudeperidot\logs\ledger.log` (rotated daily, 90 days).

- **status** reads every change's state live and stores none: each db change's `check.sql` (all in one query), each quest or config change's file hashes on the host, each client change's checks on the workstation. It prints each change as applied, not applied or partly applied, with its kind and disposition, beside the step `plan.md` names as in progress. It flags a step whose changes are applied but which is not ticked (an apply stopped before its commit), a ticked step whose changes are not all applied, and an In progress section that does not name the first unticked step. It ends with the close-of-session list.
- **stage** saves what the change will touch and commits and pushes before anything runs (write-ahead). For a row change it saves the rows the scope matches in `saved.json`, writes `revert.sql` from them (delete what the scope matches, insert the saved rows back), and stores their checksum. For a schema change it saves `SHOW CREATE TABLE` and the table checksums; `revert.sql` is hand-written. For a quest or config change it saves each file's hash on the host now and at the newest commit. A client change's record is its `change.yaml`, with its steps, undo and checks.
- **apply** refuses unless the change folder and `plan.md` are committed, pushes, and refuses unless the change is staged and not applied and its rows are as staged. A row change runs in one transaction: a guard that the rows are still as staged, `apply.sql`, and a guard that `check.sql` gives 1, so it is applied whole or not at all. A schema change runs its remaining steps; when one fails, fixing the cause and running apply again resumes from the next step. A quest or config change fast-forwards the host checkout to its newest commit; a client change runs its steps. Then apply checks the change, records the checksums in `applied.json`, ticks the plan steps whose changes are all applied, rewrites In progress and Next, commits and pushes, and prints the change's activation.
- **revert** refuses when what the change touched changed since apply (the rows' checksum differs from `applied.json`, a client check fails). A row change's revert runs in one transaction between guards: the rows are as apply left them, and afterwards they are exactly the rows saved at stage and the check gives 0. A schema change runs the inverse of each step done, in reverse order. A quest or config change is undone with `git revert` in the host checkout, a client change with its undo. Then revert unticks the plan steps that name the change, commits and pushes.
- **drift** compares `CHECKSUM TABLE` for every table except the player, state, login and bot tables with the baseline plus each applied change's `applied.json`, and reads the binary log since the baseline's position. Every write the ledger makes carries GTID domain 7, so every other write to a watched table (GM commands, Spire, the PEQ editor, rule injection at boot, a hand-run query) is listed. It prints `drift: clean` when there is nothing to list.
- **promote** writes a production change's Peridot form into its `peridot/` folder and commits it: `content.sql` (the change in one transaction, between a guard that Peridot still holds the rows the change was made against and a guard on its check) and `check.sql` for a row change; a `ManifestEntry` for `database_update_manifest_custom.cpp` for a schema change (`--manifestVersion` is the next custom version there); `cherryPick.sh` for a quest change (fetch its commits into Peridot's quests checkout first); `edit.md` for a config change, to carry into the runbook or the provisioner. It refuses while any significant production change has `ownerClearance: pending`, and for a change that is not production or not applied.
- **seed --copyScript** runs the operations repository's database copy script (`.ps1` or `.py`), which streams Peridot's database into claudeperidot. Then boot world once (its migrations and rule injection write to the database), and run **seed --baseline**: it records the dump's SHA-256 and time, the `db_version` row, the binary-log position, the deployed akk-stack, code, quests and maps commits, and every watched table's checksum in `baseline.yaml`, and commits it. Both refuse once `baseline.yaml` exists.

The ledger reaches the database only through facet's `SSHConnection`, piping SQL into `sudo -n docker exec -i akk-stack-mariadb-1 sh -c '...mysql...'`; the root password is expanded from `MYSQL_ROOT_PASSWORD` inside the container, so it never leaves the host or reaches a command line.

## Changes

Copy `changes/template` to `changes/NNNN-<name>` with the next free number and a drinkingCamelCase name, fill in `change.yaml` (the template's comments describe each form), and stage it.

| Field | Meaning |
|---|---|
| `title` | One line, stating the change |
| `why` | Why the change is made |
| `kind` | `db`, `quest`, `config` or `client` |
| `scope` | `db`: tables and a `where` predicate matching the touched rows both before and after apply (a schema change: tables only); `quest`, `config`: files in the repository; `client`: workstation paths |
| `disposition` | `fixture`, `exploration` or `production` |
| `significance` | `minor` or `significant` |
| `ownerClearance` | `pending` or `cleared` |
| `activation` | What makes the server see the change: `#reload rules`, a world restart, a full restart |
| `production` | `none` for fixtures and explorations; for production, `contentSQL` (rows), `customMigration` (schema), `questCommit`, `runbook` or `provisioner` |
| `commits` | `quest` and `config`: the commit SHAs, oldest first; empty otherwise |
| `schemaSteps` | A schema change: the number of DDL statements in `apply.sql` |
| `repository` | `quest` and `config`: the checkout's absolute path on claudeperidot |
| `steps`, `undo`, `checks` | `client`: PowerShell 5.1 scripts to apply and undo it, and checks of existence (file, directory, junction and its target), SHA-256, or an ini key's value |

| Kind | Covers | Apply and revert | Check |
|---|---|---|---|
| `db` | database rows and schema | SQL, guarded | `check.sql` |
| `quest`, `config` | quest scripts, server settings; settings are templates whose secrets are filled in on the host and never committed | a commit in the host checkout; `git revert` | file hashes on the host |
| `client` | the spare client folder, Claude's files in the owner's MacroQuest tree, the link DLL, Credential Manager entries | the recorded steps and undo | existence, hash, or ini value |

| Disposition | Meaning | At session close |
|---|---|---|
| `fixture` | Part of claudeperidot's approved setup | Kept; never promoted |
| `exploration` | An experiment | Reverted |
| `production` | Meant for Peridot | Cleared by the owner; promoted |

## Resuming after an interruption

1. `git pull` on `claude`.
2. Read `plan.md`: the goal, the step in progress and the next one.
3. `python claudeperidot/tools/ledger.py status`. Every change on the server was committed before it ran, so status shows each one; the only gap is an apply or revert that ran but stopped before its commit, which status reports as a step whose changes are applied but which is not ticked (or the reverse). Check that change, and record it with its own command's commit or by hand.
4. `python claudeperidot/tools/ledger.py drift`, for anything written outside the ledger.

A wreck is rolled back to the Proxmox `baseline` snapshot, then the ledger's fixtures are applied again; or the binary log is replayed to a point in time.

## Closing a session

- Every exploration change is reverted.
- Every production change is cleared by the owner.
- The fixtures are listed (status prints them).
- `drift` is clean.

## Tests

`python -m pytest claudeperidot/tests` from the repository root. They use real git repositories, real files and real PowerShell in temporary folders; the SSH connection is replaced by a stand-in for claudeperidot's MariaDB and shell, and pushing by a recorder, so the tests never reach a host or a remote. zonewright's own suite (`pytest` or `pytest tests`) does not collect them: zonewright's `pytest.ini` limits its test paths to `tests/`, and this folder has its own `pytest.ini`.
