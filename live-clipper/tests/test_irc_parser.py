"""Unit tests for IRC parsing and message extraction."""

import unittest
from live_clipper.config import ClipperConfig
from live_clipper.chat_monitor import TwitchChatMonitor


class TestIRCParser(unittest.TestCase):
    def setUp(self):
        self.config = ClipperConfig()
        self.monitor = TwitchChatMonitor(self.config)

    def test_standard_privmsg(self):
        line = ":speedfan123!speedfan123@speedfan123.tmi.twitch.tv PRIVMSG #ishowspeed :W STREAM W STREAM"
        res = self.monitor._parse_line(line)
        self.assertIsNotNone(res)
        username, text = res
        self.assertEqual(username, "speedfan123")
        self.assertEqual(text, "W STREAM W STREAM")

    def test_privmsg_with_colons_in_text(self):
        line = ":viewer!viewer@viewer.tmi.twitch.tv PRIVMSG #ishowspeed :LOL: this is crazy: SIUUU"
        res = self.monitor._parse_line(line)
        self.assertIsNotNone(res)
        username, text = res
        self.assertEqual(username, "viewer")
        self.assertEqual(text, "LOL: this is crazy: SIUUU")

    def test_non_privmsg_ignored(self):
        self.assertIsNone(self.monitor._parse_line(":tmi.twitch.tv 001 justinfan :Welcome, GLHF!"))
        self.assertIsNone(self.monitor._parse_line("PING :tmi.twitch.tv"))
        self.assertIsNone(self.monitor._parse_line(""))


if __name__ == "__main__":
    unittest.main()

