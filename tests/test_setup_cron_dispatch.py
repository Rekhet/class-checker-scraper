from __future__ import annotations

import json
import unittest

from scripts.setup_cron_dispatch import DISPATCH_URL, JOB_TITLE, build_job_payload


class BuildJobPayloadTests(unittest.TestCase):
    def test_payload_dispatches_workflow_every_ten_minutes(self) -> None:
        payload = build_job_payload("github_pat_x")

        job = payload["job"]
        self.assertEqual(job["url"], DISPATCH_URL)
        self.assertEqual(job["title"], JOB_TITLE)
        self.assertTrue(job["enabled"])
        self.assertEqual(job["requestMethod"], 1)  # POST
        self.assertEqual(job["schedule"]["minutes"], [3, 13, 23, 33, 43, 53])
        self.assertEqual(job["schedule"]["hours"], [-1])
        self.assertEqual(job["schedule"]["wdays"], [-1])
        self.assertEqual(
            job["extendedData"]["headers"]["Authorization"],
            "Bearer github_pat_x",
        )
        self.assertEqual(
            job["extendedData"]["headers"]["Accept"],
            "application/vnd.github+json",
        )
        self.assertEqual(json.loads(job["extendedData"]["body"]), {"ref": "main"})


if __name__ == "__main__":
    unittest.main()
