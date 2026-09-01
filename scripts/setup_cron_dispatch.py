#!/usr/bin/env python3
"""Wire cron-job.org to trigger the GitHub Actions collector every 10 minutes.

GitHub's own `schedule` trigger proved unreliable for this repo (one firing in
ten hours), so an external cron-job.org job POSTs a workflow_dispatch to the
collect-counts workflow instead. One run of this script completes the setup:

    scripts/setup-cron-dispatch <github-pat> <cronjob-api-key>

  1. verifies the PAT can see the workflow (Actions read),
  2. fires one test workflow_dispatch and confirms GitHub accepted it,
  3. creates — or updates, if it already exists — the cron-job.org job that
     dispatches every 10 minutes with the PAT in its Authorization header.

The PAT needs Actions read+write on Rekhet/class-checker-scraper only. Neither
secret is stored anywhere locally; the PAT lives inside the cron-job.org job.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

OWNER_REPO = "Rekhet/class-checker-scraper"
WORKFLOW = "collect-counts.yml"
WORKFLOW_URL = f"https://api.github.com/repos/{OWNER_REPO}/actions/workflows/{WORKFLOW}"
DISPATCH_URL = f"{WORKFLOW_URL}/dispatches"
CRONJOB_API = "https://api.cron-job.org"
JOB_TITLE = "class-checker collect-counts dispatch"
MINUTES = [3, 13, 23, 33, 43, 53]


def build_job_payload(github_pat: str) -> dict:
    return {
        "job": {
            "url": DISPATCH_URL,
            "title": JOB_TITLE,
            "enabled": True,
            "saveResponses": True,
            "requestMethod": 1,          # POST
            "requestTimeout": 30,
            "schedule": {
                "timezone": "Asia/Seoul",
                "minutes": MINUTES,
                "hours": [-1], "mdays": [-1], "months": [-1], "wdays": [-1],
            },
            "extendedData": {
                "headers": {
                    "Authorization": f"Bearer {github_pat}",
                    "Accept": "application/vnd.github+json",
                },
                "body": json.dumps({"ref": "main"}),
            },
        }
    }


def _request(method: str, url: str, token: str, payload: dict | None = None):
    req = urllib.request.Request(
        url,
        method=method,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
            if "github" in url else "application/json",
            "Content-Type": "application/json",
            "User-Agent": "class-checker-setup",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode() or "{}"
        return resp.status, json.loads(body) if body.strip() else {}


def _fail(step: str, exc: urllib.error.HTTPError) -> int:
    detail = exc.read().decode(errors="replace")[:300]
    print(f"error: {step} failed: HTTP {exc.code} {detail}", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    github_pat, cronjob_key = argv[1].strip(), argv[2].strip()

    # 1. PAT sees the workflow?
    try:
        _, wf = _request("GET", WORKFLOW_URL, github_pat)
        print(f"PAT ok: workflow '{wf.get('name')}' state={wf.get('state')}")
    except urllib.error.HTTPError as exc:
        return _fail("PAT check (Actions read on the repo missing?)", exc)

    # 2. one test dispatch — proves Actions write
    try:
        status, _ = _request("POST", DISPATCH_URL, github_pat, {"ref": "main"})
        print(f"test dispatch accepted (HTTP {status})")
    except urllib.error.HTTPError as exc:
        return _fail("test dispatch (Actions read+write on the repo missing?)", exc)

    # 3. create or update the cron-job.org job
    try:
        _, listing = _request("GET", f"{CRONJOB_API}/jobs", cronjob_key)
    except urllib.error.HTTPError as exc:
        return _fail("cron-job.org listing (bad API key?)", exc)
    existing = [j for j in listing.get("jobs", []) if j.get("title") == JOB_TITLE]
    payload = build_job_payload(github_pat)
    try:
        if existing:
            job_id = existing[0]["jobId"]
            _request("PATCH", f"{CRONJOB_API}/jobs/{job_id}", cronjob_key, payload)
            print(f"cron-job.org job updated (jobId={job_id})")
        else:
            _, created = _request("PUT", f"{CRONJOB_API}/jobs", cronjob_key, payload)
            print(f"cron-job.org job created (jobId={created.get('jobId')})")
    except urllib.error.HTTPError as exc:
        return _fail("cron-job.org job create/update", exc)

    print("done: dispatches every 10 minutes at",
          ",".join(f":{m:02d}" for m in MINUTES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
