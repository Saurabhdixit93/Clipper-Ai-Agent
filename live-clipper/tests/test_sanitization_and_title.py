"""Unit tests for filename sanitization and title generator."""

import unittest
from live_clipper.config import ClipperConfig
from live_clipper.worker import sanitize_filename
from live_clipper.title_generator import TitleGenerator
from live_clipper.chat_monitor import ChatMessage


class TestSanitizationAndTitle(unittest.TestCase):
    def setUp(self):
        self.config = ClipperConfig()
        self.generator = TitleGenerator(self.config)

    def test_sanitize_filename_windows_and_posix_chars(self):
        dirty = 'Speed: "INSANE" Play?! <100%> /win\\loss | test*'
        clean = sanitize_filename(dirty)
        for bad_char in '<>:"/\\|?*':
            self.assertNotIn(bad_char, clean)
        self.assertLessEqual(len(clean), 45)

    def test_title_under_60_chars(self):
        chat_msgs = [
            ChatMessage(timestamp=100.0, username="user1", message="W STREAM"),
            ChatMessage(timestamp=101.0, username="user2", message="W W W"),
            ChatMessage(timestamp=102.0, username="user3", message="SIUUU")
        ]
        title, tags = self.generator.generate_title_and_tags(
            transcript="I cannot believe this happened right now bro",
            chat_messages=chat_msgs,
            channel_name="ishowspeed"
        )
        self.assertLess(len(title), 60)
        self.assertEqual(len(tags), 3)
        for tag in tags:
            self.assertTrue(tag.startswith("#"))


if __name__ == "__main__":
    unittest.main()

