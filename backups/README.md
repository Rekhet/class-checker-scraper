# Recovery artifacts

Files in this directory are private recovery material, not scheduled-worker
inputs.

## June trend sample archive

`class-checker-june-samples-20260804.jsonl.gz` was created during the June trend
cleanup work on 2026-08-04. It is a gzip-compressed JSONL archive containing
177,120 samples dated 2026-06-21 through 2026-06-23. Keep it for audit or an
explicit rollback investigation; the crawler does not read it automatically.
