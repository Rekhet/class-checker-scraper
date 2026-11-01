# Winter rollover audit, 2026-11-01

Winter frontend defaults are deployed, but collection has not switched from
fall. `web/config.js` does not control scraper scope, windows, cloud roster,
or exported term status. This audit did not change operational configuration.

## Verified surfaces

| Surface | Observed state | Result |
| --- | --- | --- |
| Public config.js search defaults | 2026 / winter | Correct |
| Public config.js timetable defaults | 2026 / winter | Correct for fresh state |
| Public config.js ICS range | 2026-12-22 through 2027-01-25 | Matches official class period |
| Local and origin/main collect.env | COUNT_YEAR=2026, COUNT_SEM=fall | Not rolled over |
| Collection windows | Cart August 4–5; enrollment July–September; slow through October 20 | No winter windows |
| UPCOMING_TERMS | 2026:winter 2027:spring | Winter remains a catalog snapshot target |
| Active systemd update service | No COUNT_SEM / UPDATE_SEM override | Uses fall |
| Cloud classes roster | No 2026 winter rows | Winter counts roster not seeded |
| Cloud count_passes / count_latest | Only 2026 fall, latest October 20 23:51:55 KST | No winter counts history or baseline |
| Cloud winter catalog_snapshots | 262 classes, checked November 1 09:34:05 UTC | Catalog recovered; distinct from counts roster |
| Local catalog | Winter 260 classes | Latest snapshot not applied yet |
| Public index.json | Fall current; winter upcoming, 260 classes, no trend pointer | Published metadata still follows fall |
| Counts run 41298502689 | Main 38a6571; active=false | Successful skip, no samples |

The web origin/main config contains commit `7c64786` (winter defaults).
Public HTTP GETs returned 200 and confirmed the config and index values above.
The scraper origin/main is `38a6571`; its collect.env still names fall.
The current shell has no semester override. The systemd update service has
no semester override; its timer remains 06:00/12:00/18:00 KST.

Source tracing in web/app.js: applySearchDefaults reads the search defaults;
configuredSemester/defaultSemester pick winter for fresh state. An existing
saved semester is preserved by migrateSemesters. Existing users are not
forcibly moved to winter. The trend selector reads the search default, but
the winter index entry has no trend data pointer. ICS dates apply to the
configured semester and are cleared for a saved different semester.
These are source-level findings, not browser interaction verification.
User-owned in-progress web changes were not altered.

## Schedule and missing history

The official [SNU Industrial Engineering notice](https://ie.snu.ac.kr/notice/?mid=ent_under&mod=document&uid=6517)
and [Biological Sciences notice](https://biosci.snu.ac.kr/board/notice?bbsidx=25596&bm=v&page=1v)
agree: preliminary registration October 20–23, registration October 27–November 3
(excluding weekends), changes November 11–13, cancellation December 11–January 7,
and classes December 22–January 25. January dates are in 2027.

Neither cloud nor local count_passes contains winter observations. The
October 27–30 registration-day history is therefore absent from this pipeline.
Current counts can be sampled after rollover; prior changes and exact times
cannot be reconstructed from a current snapshot. Independent external
archives were not audited.

The earlier conclusion that no additional counts gap arose was based on the
remaining fall windows. It does not apply to the intended winter collection.

## Rollover work still required

Canonical runtime configuration is collect.env; frontend defaults are separate.
Follow systemd/README.md Semester rollover: set winter scope and official
windows, refresh the local winter catalog, seed the cloud roster from it,
remove winter from UPCOMING_TERMS, deploy collect.env, verify an actual
in-window runner pass and cloud samples, and publish current catalog/status/trend
metadata. None of these rollover operations was executed in this audit.

Cart collection uses separate bounded local units if applicable. The old
August cart environment file names fall; no active cart timer appeared in
the inspected timer list. This does not affect the separate cloud counts gate.

## Evidence and limits

- `.tmp/winter-rollover-cloud-audit.json`: read-only cloud aggregate queries.
- `.tmp/winter-rollover-live-index.json`: public index HTTP response.
- [Counts run 41298502689](https://github.com/Rekhet/class-checker-scraper/actions/runs/41298502689): checked main commit and active=false.
- Local/origin config, source consumers, local SQLite queries, and active
  systemd service/timer reads. No database initialization or writes.
- Site-wide browser behavior and archives/databases outside this pipeline were not audited.

Prose document reviewed independently; operational fixes remain outside this
verification-only request.
