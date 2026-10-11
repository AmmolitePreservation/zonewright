# claudeperidot plan

## Goal

claudeperidot is Claude's own EQEmu server: a faithful copy of Peridot on a LAN-only VM, where Claude has GM status, a spare RoF2 client and a control link, and can experiment freely. Every change to it, and to Claude's side of the workstation, goes through the ledger, so it can be checked, reverted, and carried to Peridot when it is meant for production.

## In progress

10. Measure the spare's first and second zone load on the HDD before relying on it; at first launch confirm Logs\dbg.txt reads Total Virtual: 4095, and watch 30 or more zone loads

## Next

13. First launch through claudelink, without activating the window: injection, focus never stolen, MQ2EQBC behaviour, affinity and CPU; create the account and a character with Pariator's race and gender; GM fixture: that account's status 255 (a row change on account, whose saved rows stay on claudeperidot); the login.db persona row as a client change checked by its existence

## Steps

Each step is ticked when it is done. A step that names ledger changes in brackets is ticked and unticked by `ledger.py apply` and `revert`, from the changes' checks; Claude ticks the others by hand and keeps In progress and Next on the first two unticked steps. Lab changes (made through facet or the sonicwall MCP) are named in a step's text, since the ledger cannot check them.

- [x] 0. Owner: answer the 15 questions (2026-10-05), reconnect facet, allow the read-only Peridot reads
- [x] 1. Read Peridot read-only (2026-10-11); the facts are in Baseline below. On the owner's word, the six empty stray files in its akk-stack root and the [safe] section of /root/.gitconfig are gone (lab changes 0006 and 0007), and its running commit is kept as a local bundle [0008]
- [x] 2. The ledger in zonewright's claudeperidot folder (owner's answer 2): README, this plan, tools/ledger.py, the template change, and the workstation changes already made
- [x] 3. The provisioner (operations services/eqemu/claudeperidot-provision.ps1) and the guest bootstrap (guest-bootstrap-claudeperidot.sh), reviewed and fixed, on operations claude (7e19e3e); labradorite had 194.5 GB available against 72 GB needed, and alpha is directory storage, so Proxmox snapshots work
- [x] 4. DHCP reservation 10.111.111.235 for BC:24:11:AC:01:43 (lab change 0009) and the DNS A record claudeperidot.strata.unseencolor.com on olivine (lab change 0010)
- [x] 5. VM 143 ClaudePeridot provisioned on labradorite (8 cores, 16 GB unballooned on NUMA node 0, 100 GB on alpha, Ubuntu 24.04, not started at boot; its boot disk imported as root through facet, as claude@pve cannot import a path) and bootstrapped: Docker; akk-stack EQEmu/akk-stack 13f442d; image akkadius/eqemu-server:v16 by digest; fresh secrets; EQEmu ae4bd2833 fetched by its SHA and built in 6.3 minutes with no errors; quests c327bed; maps fbd3b19128; world claudeperidot on 10.111.111.235 with its own login server; the binary log on in ROW format with annotated rows and a 90-day expiry. Two bootstrap fixes on the way: Spire's assets before its binary (38fcbd9) and the running-server check by command line (ae496ff)
- [x] 6. Enrolled in facet: inventory entry (operations 4e18740), Claude's account with its credential claude/claudeperidot.strata.unseencolor.com; the temporary ssh/10.111.111.235 credential deleted after
- [x] 7. Seeded: Peridot's two untracked quest files committed on the quests checkout's local claude branch (39f68e9); Peridot's whole peq streamed (peq-20261011T180850Z.sql.gz, 35.7 MB, live secrets included as the owner chose) and loaded: 273 tables, 5 views, db_version 9325/0/0; world booted once (no migrations, 1,001 rules, expansion 4) and stopped; the Proxmox snapshot baseline taken with the server stopped; `ledger.py seed --baseline` recorded 151 watched tables at mysql-bin.000007:121167
- [x] 8. Expansion fixture: Expansion:CurrentExpansion 19 (Rain of Fear, owner's answer 1) in ruleset 1 default, set with world and zones stopped, then started; its revert puts back Peridot's 4 [0011]
- [x] 9. Spare client S:\Research\EverQuest\housingclient: the copy, eqhost.txt, the large-address flag and eqclient.ini (owner's answers 9 to 11) [0001, 0002, 0003, 0004]
- [ ] 10. Measure the spare's first and second zone load on the HDD before relying on it; at first launch confirm Logs\dbg.txt reads Total Virtual: 4095, and watch 30 or more zone loads
- [x] 11. MQClaudeLink junctioned into the owner's MacroQuest tree and built into its plugins folder (owner's answers 12 and 13) [0005]
- [x] 12. Credential claudeperidot/eqLogin (a client change: its step generates the password and stores it with CredWrite, never on a command line; its check is the credential's existence), and Claude's MacroQuest cfg: MQ2EQBC and mq2netbots unloaded and MQClaudeLink loaded, each with noauto [0012, 0013]
- [ ] 13. First launch through claudelink, without activating the window: injection, focus never stolen, MQ2EQBC behaviour, affinity and CPU; create the account and a character with Pariator's race and gender; GM fixture: that account's status 255 (a row change on account, whose saved rows stay on claudeperidot); the login.db persona row as a client change checked by its existence
- [ ] 14. The rest of the claudelink commands and its MCP tools, with claudelink managing the frame limiter (owner's answer 14); the first calibration shot
- [ ] 15. Retire the EQBCS route for Claude's work once a claudelink shot matches one of the owner's screenshots
- [ ] 16. Experiments through the ledger; close every session as the README says

## Baseline

What claudeperidot copies, as read from Peridot on 2026-10-11 (read-only, through facet). `baseline.yaml` records what the copy actually came from once step 7 runs.

| Piece | Peridot | claudeperidot |
|---|---|---|
| EQEmu | Binaries built 2026-09-25 from `ae4bd2833c0e6ff00b97e26789fc806341180260`: `main` `6fedcae` plus a commit stamp in world, zone and cmake, the housing docs, README and .gitignore. `version.h` 9325, custom 0. World has run since 2026-10-03. The `code/` checkout has since moved to local `claude` `142d48e` without a rebuild | Built from `ae4bd2833`, fetched by its full SHA (GitHub reaches it no other way, and the owner chose no tag); `S:\Research\EverQuest\claudeperidot\peridot-ae4bd2833.bundle` (branch `peridotRunning`; change 0008) is the local copy |
| akk-stack | `Akkadius/akk-stack` `master` `13f442d` (not the PhoenixCampfire fork); file-mode changes only, no content edits; image `akkadius/eqemu-server:v16` | The same commit, cloned on the guest |
| Quests | `ProjectEQ/projecteqquests` `master` `c327bed` (2025-06-28); no content edits, plus 2 untracked files, `global/spells/17779.lua` and `global/spells/9177.pl.bazaarbounce` | The same commit plus the 2 files as one quest commit |
| Maps | `EQEmu/maps` `fbd3b19128`, byte-identical | The same commit |
| Database | MariaDB 10.5.4; `db_version` 9325, bots 0, custom 0; 273 InnoDB tables and 5 views, 2,210 MB; no MyISAM, so `--single-transaction` is consistent; `log_bin` off | The whole database, streamed, live secrets included (owner, 2026-10-11); binary log on in ROW format |
| Expansion | Ruleset 1 `default`: `Expansion:CurrentExpansion` 4, `AutoGrantAAExpansion` -1, `UseCurrentExpansionAAOnly` true; `World:ExpansionSettings` 524287; 40 zones bypass the expansion check | `Expansion:CurrentExpansion` 19 (step 8) |
| Settings | `.env` keys match `.env.example`; `login.json` has `account.auto_create_accounts` and `client_configuration.max_expansions_mask`. No values were read | Fresh secrets; never Peridot's `.env`, `eqemu_config.json` or `login.json` |
| Containers | eqemu-server, mariadb, peq-editor and its proxy, two fail2ban, pok web, EQBCS | eqemu-server, mariadb, peq-editor and its proxy, two fail2ban; no pok, EQBCS, ammolite link, public address or SonicWall forwards |

## Open with the owner

- Peridot has no binary log and no backups (no cron job, no backup container): a production risk outside this plan.
