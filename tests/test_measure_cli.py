from __future__ import annotations

import unittest

from tools import measure


class MeasureCliTests(unittest.TestCase):
    def test_every_command_parses(self) -> None:
        for name in measure.COMMANDS:
            with self.subTest(command=name):
                args = measure.build_parser().parse_args([name])
                self.assertEqual(args.command, name)

    def test_web_commands_take_a_site(self) -> None:
        args = measure.build_parser().parse_args(
            ["feed", "--site", "https://example.test/", "--sequential", "--rounds", "1"])
        self.assertEqual((args.site, args.sequential, args.rounds),
                         ("https://example.test/", True, 1))


if __name__ == "__main__":
    unittest.main()
