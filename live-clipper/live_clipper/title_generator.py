"""Title generation engine supporting Gemini CLI with local fallback."""

import re
import shutil
import logging
import subprocess
from typing import List, Tuple, Optional
from collections import Counter

from live_clipper.config import ClipperConfig
from live_clipper.chat_monitor import ChatMessage

logger = logging.getLogger("live_clipper.title_generator")


class TitleGenerator:
    """
    Generates video titles (< 60 characters) and exactly 3 hashtags.
    Uses Gemini CLI if available; otherwise uses an intelligent local heuristic fallback.
    Never uses Claude or Anthropic.
    """
    def __init__(self, config: ClipperConfig):
        self.config = config
        self.gemini_bin = shutil.which("gemini")

    def generate_title_and_tags(
        self,
        transcript: str,
        chat_messages: List[ChatMessage],
        channel_name: str = "ishowspeed"
    ) -> Tuple[str, List[str]]:
        """
        Generates (title, [tag1, tag2, tag3]).
        Title is guaranteed to be under 60 characters.
        """
        # Try Gemini CLI if installed
        if self.gemini_bin:
            res = self._try_gemini_cli(transcript, chat_messages, channel_name)
            if res:
                return res

        # Fallback to local rule-based generator
        return self._local_fallback(transcript, chat_messages, channel_name)

    def _try_gemini_cli(
        self,
        transcript: str,
        chat_messages: List[ChatMessage],
        channel_name: str
    ) -> Optional[Tuple[str, List[str]]]:
        """Query local Gemini CLI."""
        if not self.gemini_bin:
            return None
        gemini_bin = str(self.gemini_bin)

        chat_sample = " | ".join(f"{m.username}: {m.message}" for m in chat_messages[-15:])
        prompt = (
            f"You are a short-form video editor for streamer {channel_name}. "
            f"Create an engaging, viral title under 60 characters and exactly three hashtags for this clip.\n"
            f"Transcript: {transcript}\n"
            f"Twitch Chat Reactions: {chat_sample}\n"
            f"Output format:\n"
            f"TITLE: <one title under 60 chars>\n"
            f"TAGS: #tag1 #tag2 #tag3"
        )

        try:
            cmd = [gemini_bin, prompt]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=12, text=True)
            if proc.returncode == 0 and proc.stdout:
                output = proc.stdout.strip()
                title = None
                tags = []

                for line in output.splitlines():
                    if line.upper().startswith("TITLE:"):
                        title = line.split(":", 1)[1].strip()
                    elif line.upper().startswith("TAGS:"):
                        tag_parts = line.split(":", 1)[1].strip().split()
                        tags = [t if t.startswith("#") else f"#{t}" for t in tag_parts if t.startswith("#") or t.isalnum()]

                if title:
                    title = title[:59].strip()
                    if len(tags) < 3:
                        tags = [f"#{channel_name.lower()}", "#twitch", "#viral"]
                    return title, tags[:3]

        except Exception as e:
            logger.debug(f"Gemini CLI execution failed or timed out: {e}")

        return None

    def _local_fallback(
        self,
        transcript: str,
        chat_messages: List[ChatMessage],
        channel_name: str
    ) -> Tuple[str, List[str]]:
        """Smart local heuristic title generator based on chat hype words and transcript context."""
        chat_texts = [m.message.upper() for m in chat_messages]
        all_words = []
        for text in chat_texts:
            tokens = re.findall(r"\b[A-Z0-9_]{2,15}\b", text)
            all_words.extend(tokens)

        word_counts = Counter(all_words)
        
        # Identify hype sentiment keywords
        top_hype = None
        for word, count in word_counts.most_common(10):
            if word in {"W", "L", "OMG", "GOAT", "SIUUU", "SPEED", "WHAT", "NOOO", "CR7", "INSANE", "WTF", "HYPE"}:
                top_hype = word
                break

        title = ""
        # 1. If strong chat spike keyword found
        if top_hype == "W":
            title = f"{channel_name.capitalize()} Huge W Reaction!"
        elif top_hype == "L":
            title = f"{channel_name.capitalize()} Major L Moment"
        elif top_hype in {"SIUUU", "CR7", "GOAT"}:
            title = f"{channel_name.capitalize()} Hits The Iconic SIUUU!"
        elif top_hype in {"OMG", "INSANE", "WHAT"}:
            title = f"{channel_name.capitalize()} Goes Completely Insane!"
        elif transcript.strip():
            # Extract most energetic sentence or fragment
            cleaned = re.sub(r"[^\w\s]", "", transcript).strip()
            words = cleaned.split()
            if len(words) >= 3:
                snippet = " ".join(words[:6]).capitalize()
                title = f"{channel_name.capitalize()}: {snippet}"
            else:
                title = f"{channel_name.capitalize()} Crazy Stream Moment!"
        else:
            title = f"{channel_name.capitalize()} Unbelievable Live Reaction!"

        # Ensure strict constraint: under 60 characters
        if len(title) > 59:
            title = title[:56] + "..."

        tags = [f"#{channel_name.lower()}", "#twitchclips", "#viral"]
        return title, tags

    def generate_hook(
        self,
        transcript: str,
        chat_messages: List[ChatMessage],
        channel_name: str,
        title: str
    ) -> str:
        """
        Generates an engaging, viral top-hook headline in all-caps with emojis
        specifically formatted for the top safe-zone banner of TikTok/Shorts.
        """
        # Try Gemini CLI if available
        if self.gemini_bin:
            try:
                gemini_bin = str(self.gemini_bin)
                prompt = (
                    f"Create an ultra-engaging, viral 4 to 7 word hook banner in ALL CAPS with 2 emojis for a TikTok/Reels vertical clip of {channel_name}.\n"
                    f"Title: {title}\n"
                    f"Transcript snippet: {transcript[:180]}\n"
                    f"Respond with ONLY the hook line (e.g. '🔥 BRO CANNOT BE REAL 💀')."
                )
                proc = subprocess.run([gemini_bin, prompt], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=6, text=True)
                if proc.returncode == 0 and proc.stdout.strip():
                    hook = proc.stdout.strip().replace('"', '').replace("'", "")
                    first_line = hook.splitlines()[0].strip()
                    if 4 <= len(first_line) <= 45:
                        return first_line
            except Exception:
                pass

        # Intelligent local heuristic hook generator
        chat_texts = [m.message.upper() for m in chat_messages]
        all_words = []
        for text in chat_texts:
            tokens = re.findall(r"\b[A-Z0-9_]{2,15}\b", text)
            all_words.extend(tokens)
        word_counts = Counter(all_words)

        top_hype = None
        for word, _ in word_counts.most_common(10):
            if word in {"W", "L", "OMG", "GOAT", "SIUUU", "SPEED", "WHAT", "NOOO", "CR7", "INSANE", "WTF", "CLUTCH", "ACE"}:
                top_hype = word
                break

        if top_hype == "W":
            return "🔥 BRO JUST PULLED OFF A MASSIVE W 🐐"
        elif top_hype == "L":
            return "💀 BIGGEST L OF THE STREAM 😭"
        elif top_hype in {"SIUUU", "CR7", "GOAT"}:
            return "🐐 HE DROPPED THE ICONIC SIUUU 🔥"
        elif top_hype in {"OMG", "WHAT", "WTF", "NOOO"}:
            return "😱 WAIT FOR HIS REACTION 💀"
        elif top_hype in {"INSANE", "CLUTCH", "ACE"}:
            return "🤯 BRO WENT ABSOLUTELY INSANE 🔥"
        elif "WAIT" in title.upper():
            return "🔥 WAIT TILL THE VERY END 💀"
        else:
            return f"🔥 {channel_name.upper()} DID THIS LIVE ON STREAM 😱"

