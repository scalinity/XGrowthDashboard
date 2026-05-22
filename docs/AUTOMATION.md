# Backup automation — launchd and cron recipes

Phase 4 ships the [backup runner](../scripts/backup_db.py) and the "Back up
now" button in Settings, but **does not install** any scheduled job. This
file is a reference for when you (Daniel) decide you want a daily automated
backup running on your laptop.

The runner is safe to invoke from any scheduler: it is idempotent, it
captures its own timestamp, and it self-prunes per `settings.backup_retention_days`.

> Per `spec.md` §7.1 and §18 rule 10, `VACUUM INTO` is the **only** sanctioned
> backup mechanism. A scheduled `cp` of the live `.db` file is unsafe and is
> rejected by code review. Use this file's recipes verbatim.

---

## Option A — launchd (recommended on macOS)

`launchd` is the native scheduler on macOS, survives reboots, and runs even
if the laptop missed the trigger time while asleep.

### Plist template

Save the file below to:

```
~/Library/LaunchAgents/com.danny.xgrowth.backup.plist
```

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.danny.xgrowth.backup</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>-lc</string>
        <string>cd /Users/danny/Documents/Codez/Apps/XGrowthDashboard &amp;&amp; /Users/danny/.local/bin/uv run python -m scripts.backup_db &gt;&gt; data/backups/backup.log 2&gt;&amp;1</string>
    </array>

    <!--
      Daily at 03:00 local time.

      Note on omitted keys: launchd treats every absent `StartCalendarInterval`
      key as a wildcard. Omitting `Day` means *every day of the month*, not
      "the first" — and likewise for `Month`, `Weekday`. If you set `Day = 1`
      thinking it means "run every day", you'll get a once-a-month job
      instead. The four keys below (Hour, Minute, with Day/Month/Weekday
      implicitly "*") are the right shape for daily-at-03:00.
    -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <!-- If the laptop was asleep at 03:00, run as soon as it wakes. -->
    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>/Users/danny/Documents/Codez/Apps/XGrowthDashboard/data/backups/backup.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/danny/Documents/Codez/Apps/XGrowthDashboard/data/backups/backup.log</string>

    <key>WorkingDirectory</key>
    <string>/Users/danny/Documents/Codez/Apps/XGrowthDashboard</string>
</dict>
</plist>
```

Notes on the template:

- The shell wrapper (`/bin/zsh -lc "..."`) loads your login profile so `uv`
  finds the project's `.venv`. Without `-l`, launchd uses a stripped
  environment and `uv` can't resolve the project Python.
- The absolute path to `uv` (`/Users/danny/.local/bin/uv`) matches the
  current install location; if you ever move uv, edit this file.
- Output is appended to `data/backups/backup.log`. That directory is
  already `.gitignore`'d.

### Install / verify / uninstall

```bash
# Install (idempotent — re-running just reloads the job):
launchctl unload ~/Library/LaunchAgents/com.danny.xgrowth.backup.plist 2>/dev/null
launchctl load -w ~/Library/LaunchAgents/com.danny.xgrowth.backup.plist

# Verify the job is registered:
launchctl list | grep com.danny.xgrowth.backup

# Trigger it once manually (does not affect schedule):
launchctl start com.danny.xgrowth.backup

# Tail the log to watch the run:
tail -f data/backups/backup.log

# Uninstall:
launchctl unload -w ~/Library/LaunchAgents/com.danny.xgrowth.backup.plist
rm ~/Library/LaunchAgents/com.danny.xgrowth.backup.plist
```

---

## Option B — cron (fallback / portable)

Cron works fine but does **not** catch up after a missed trigger (laptop
asleep, lid closed). Prefer launchd unless you have a reason.

```cron
# Daily backup of the X Growth Dashboard SQLite DB at 03:00 local.
0 3 * * * cd /Users/danny/Documents/Codez/Apps/XGrowthDashboard && /Users/danny/.local/bin/uv run python -m scripts.backup_db >> data/backups/backup.log 2>&1
```

Install it via `crontab -e`. Verify with `crontab -l`. Remove the line
to uninstall.

---

## What gets created

Each successful run writes one file to `data/backups/`:

```
data/backups/x_growth_2026-05-21_030000.db
```

…and appends a JSON record to `data/backups/backup.log`:

```json
{
  "path": "/Users/danny/Documents/Codez/Apps/XGrowthDashboard/data/backups/x_growth_2026-05-21_030000.db",
  "size_bytes": 1234567,
  "duration_ms": 42,
  "integrity_check_passed": true,
  "pruned": [
    "/Users/danny/Documents/Codez/Apps/XGrowthDashboard/data/backups/x_growth_2026-04-21_030000.db"
  ]
}
```

Retention defaults to 30 days (`settings.backup_retention_days`). Files older
than that are deleted at the end of each run; the JSON `pruned` array lists
the paths that were removed.

---

## Restoring from a backup

> **Prerequisite — stop Streamlit first.** Restore renames the live
> `data/dashboard.db` to a sidecar inode while the Streamlit session may
> still hold an open connection to it. POSIX lets the rename succeed and
> subsequent writes go into the sidecar; those writes are silently lost
> if you later "roll back" by renaming the sidecar back. The restore
> command checks for `data/dashboard.db-wal`/`-shm` and refuses to run
> when either exists. To override the guard (advanced — you understand
> the risk), pass `--allow-open-db`.

The companion command is `scripts/restore_db.py`. Always start with a
dry-run:

```bash
uv run python -m scripts.restore_db --backup data/backups/x_growth_2026-05-21_030000.db
```

The dry-run prints what would happen and exits 0 without touching the
target DB. When you've confirmed you want to restore, re-run with
`--confirm`:

```bash
uv run python -m scripts.restore_db --backup data/backups/x_growth_2026-05-21_030000.db --confirm
```

The current `data/dashboard.db` is moved to a timestamped sidecar
(`dashboard.db.pre-restore.YYYY-MM-DD_HHMMSS`) before the backup is copied
into place. The sidecar is never auto-deleted — if the restore goes badly,
roll back manually with `mv`.

---

## What this does NOT do (per Phase 4 scope)

- **No encryption at rest.** Backup files are unencrypted SQLite. Use
  filesystem-level encryption (FileVault is on by default on modern macOS)
  if that matters.
- **No off-machine sync.** Backups stay on this laptop. The project is a
  single-user local tool (§7.1); cloud sync is out of scope for the MVP.
- **No automatic restore-on-corruption.** Restoration is always a manual
  decision — see `scripts/restore_db.py`.
- **The launchd plist is not installed by this phase.** Install it
  manually when you're ready.
