import unittest
from unittest.mock import patch

from psychology_evidence_agent import cli


class CliTests(unittest.TestCase):
    def test_required_commands_are_registered(self):
        self.assertTrue(
            {"doctor", "web", "search", "evidence", "synthesize", "draft", "agent"}
            <= cli.COMMANDS.keys()
        )

    def test_dispatches_command_and_forwards_arguments(self):
        with patch.dict(cli.COMMANDS, {"doctor": lambda: 7}):
            self.assertEqual(cli.main(["doctor"]), 7)

    def test_agent_command_forwards_to_agent_cli(self):
        with patch.dict(cli.COMMANDS, {"agent": lambda: 9}):
            self.assertEqual(cli.main(["agent"]), 9)


if __name__ == "__main__":
    unittest.main()
