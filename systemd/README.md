# User timers

The full update service and the fast cart/trend service both use the same
host-local `data/.crawl.lock`. The lock is held across the database operation,
JSON export, and Git publication, so a counts pass cannot read a half-rebuilt
term or publish over another update.

Install or refresh the user units with:

```sh
install -Dm644 systemd/class-checker.update-counts.service \
  "$HOME/.config/systemd/user/class-checker.update-counts.service"
install -Dm644 systemd/class-checker.update-counts.timer \
  "$HOME/.config/systemd/user/class-checker.update-counts.timer"
systemctl --user daemon-reload
systemctl --user enable --now class-checker.update-counts.timer
```

The timer is deliberately broad on weekdays during 09:00–20:50. The
`--windowed` pass checks `collect.env` before opening a SNU session, so it is
inactive outside the configured cart/enrollment windows. Change `COUNT_MODE`
to `counts` in `collect.env` when the same cadence should collect both live count
metrics. `COUNT_YEAR`, `COUNT_SEM`, `COUNT_MODE`, `COLLECTION_TIMEZONE`, and the
`*_WINDOWS` values in `collect.env` are the canonical runtime configuration; edit
that file for a future semester, then run `systemctl --user daemon-reload`.

The full-update wrapper reads `COUNT_YEAR` and `COUNT_SEM` from the same file.
For an intentional one-off full refresh, `UPDATE_YEAR` and `UPDATE_SEM` may
override them without changing the canonical configuration.

## Bounded two-day cart collection

Use the launcher for a cart window instead of running the broad count timer
during the cart period:

```sh
./scripts/start-cart-window \
  --start-date 2026-08-04 \
  --year 2026 \
  --semester fall \
  --timezone Asia/Seoul \
  --disable-broad-timer
```

The launcher renders date-specific user timers and a per-window environment
file. The collector runs at ten-minute boundaries from 09:00 on the start date
through the final 16:00 run on the next date. The cleanup timer runs at 16:10,
stops future collector activations, waits for the collector and
`data/.crawl.lock` to become idle, then removes only that window's generated
timer files. The reusable `@.service` templates remain installed.

The old broad `class-checker.update-counts.timer` is intentionally not allowed
to run alongside a bounded window, because it would duplicate cart requests.
The launcher rejects that state unless `--disable-broad-timer` is supplied. It
also rejects a second overlapping bounded window and is safe to rerun for the
same start date. Use `--dry-run` to inspect a schedule without changing files
or systemd.
