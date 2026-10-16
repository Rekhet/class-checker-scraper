# JSON Boundary Validation and Frontend Data Safety

## Status

Implemented in the public frontend repository on 2026-08-05. The root
repository records the design and verification; `web/` remains its own Git
repository and must be published separately.

## Decision

Validate JSON at the boundary where the frontend first consumes it. Keep the
checks dependency-free and type-oriented. Do not impose payload-size or
array-length limits, because valid future terms and larger catalogs must remain
usable.

## Covered boundaries

- `data/classes/index.json`: term identities, counts, safe filenames, and
  vocabulary arrays.
- Per-term class files: required strings and integers, nullable count fields,
  classification arrays, and meeting-slot shapes. Invalid rows are skipped;
  an invalid top-level payload makes only that term empty.
- Trend files: valid timestamps, `a/c/e/q` integer-or-null arrays, and exact
  alignment with `ts`. An invalid trend payload disables the trend feature for
  that term.
- `data/explore-index.json`: interned string tables, professor and department
  references, term indexes, and offering-row indexes.
- Graduation index/specification/rules files and code-equivalence data:
  required top-level types, safe file references, and the nested fields used by
  the audit.
- Imported timetable/wishlist JSON and local-storage sheets, wishlist, undo
  snapshots, and metadata. Manual timetable slots are allowed to omit the
  catalog-only `class_id` field.
- Optional `/api/status` and `/api/timestats` responses used by the admin UI.

## Failure behavior

Collection-level failures are logged and fall back to an empty or disabled
feature state. Invalid catalog rows and stored entries are ignored individually.
Imported files are rejected before catalog lookup so malformed data cannot be
partially applied. No user-controlled value is interpolated into trend
tooltip HTML; the tooltip is assembled with DOM text nodes.

## Verification

On 2026-08-05:

- `node --check web/app.js` passed.
- `python3 -m unittest web.tests.test_json_validation` passed six tests,
  including malformed catalog/trend payloads, a non-numeric trend metric with
  an XSS probe, feature-local explorer/graduation failures, and real
  catalog/trend/explorer/graduation data.
- `python3 -m unittest web.tests.test_json_validation web.tests.test_search_cart`
  passed all seven validation/cart tests.
- The checked-in class, trend, explorer, and graduation JSON files were parsed
  and their observed field types were checked before browser verification.

## Deferred

Payload-size/array limits remain intentionally out of scope. CSP and
third-party script integrity protection are separate deployment-hardening work,
not part of this data-boundary change.
