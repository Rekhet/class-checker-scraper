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
