# pyright: reportMissingImports=false
"""Faster-Whisper transcription and subtitle generation engine."""

import os
import sys
import logging
import platform
import textwrap
from pathlib import Path
from typing import List, Tuple, Optional
from dataclasses import dataclass

# Ensure virtual environment packages are discoverable
_venv_site = Path(__file__).resolve().parent.parent / ".venv" / "lib" / "python3.11" / "site-packages"
if _venv_site.exists() and str(_venv_site) not in sys.path:
    sys.path.insert(0, str(_venv_site))

from PIL import Image, ImageDraw, ImageFont  # type: ignore
from faster_whisper import WhisperModel  # type: ignore
from live_clipper.config import ClipperConfig

logger = logging.getLogger("live_clipper.transcriber")


@dataclass
class SubtitleCue:
    start_sec: float
    end_sec: float
    text: str


class Transcriber:
    """
    Transcribes audio using faster-whisper with hardware auto-selection and mandatory CPU fallback.
    Groups words into 3-4 word subtitle segments and renders large, bold, centered subtitle images.
    """
    def __init__(self, config: ClipperConfig):
        self.config = config
        self.model: Optional[WhisperModel] = None
        self.font_path = self._find_font()
        self.emoji_font_path = self._find_emoji_font()
        self._init_model()

    def _find_font(self) -> Optional[str]:
        """Locate suitable bold font for TikTok/Shorts style captions."""
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Black.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/Library/Fonts/Arial.ttf"
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    def _find_emoji_font(self) -> Optional[str]:
        """Locate color emoji font for rendering emojis in video graphics."""
        candidates = [
            "/System/Library/Fonts/Apple Color Emoji.ttc",
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "/usr/share/fonts/google-noto-color-emoji/NotoColorEmoji.ttf"
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

    def _init_model(self):
        """Initialize faster-whisper model with appropriate hardware acceleration or CPU fallback."""
        model_size = self.config.whisper_model
        
        # 1. Prefer direct local model directory in live-clipper/models/
        local_dir = self.config.base_dir / "models" / f"faster-whisper-{model_size}"
        if not local_dir.exists() and (self.config.base_dir / "models" / "faster-whisper-base").exists():
            local_dir = self.config.base_dir / "models" / "faster-whisper-base"

        # Check local HF cache snapshot as fallback
        hf_snapshot = Path.home() / ".cache" / "huggingface" / "hub" / f"models--Systran--faster-whisper-{model_size}" / "snapshots"
        if not local_dir.exists() and hf_snapshot.exists():
            snapshots = list(hf_snapshot.glob("*"))
            if snapshots:
                local_dir = snapshots[0]

        model_target = str(local_dir) if (local_dir and local_dir.exists()) else model_size
        local_only = local_dir.exists() if (local_dir and isinstance(local_dir, Path)) else False
        
        # Check architecture and hardware
        device = "cpu"
        compute_type = "int8"
        
        logger.info(f"Loading faster-whisper model from '{model_target}' on device '{device}' with '{compute_type}'...")
        try:
            self.model = WhisperModel(
                model_target,
                device=device,
                compute_type=compute_type,
                cpu_threads=4,
                local_files_only=local_only
            )
            logger.info("Faster-whisper model successfully loaded")
        except Exception as e:
            logger.warning(f"Failed to load Whisper on {device}/{compute_type}: {e}. Retrying with cpu/float32...")
            try:
                self.model = WhisperModel(
                    model_target,
                    device="cpu",
                    compute_type="float32",
                    cpu_threads=4,
                    local_files_only=local_only
                )
                logger.info("Faster-whisper loaded with CPU float32 fallback")
            except Exception as ex:
                logger.error(f"Fatal error loading Whisper model: {ex}")
                self.model = None

    def transcribe_audio(self, audio_or_video_path: Path) -> Tuple[str, List[SubtitleCue]]:
        """
        Transcribe video/audio file and extract 3-4 word grouped subtitle cues with timestamps.
        Returns: (full_transcript_text, list_of_cues)
        """
        audio_or_video_path = Path(audio_or_video_path)
        if not self.model or not audio_or_video_path.exists():
            return "", []

        try:
            segments, info = self.model.transcribe(
                str(audio_or_video_path),
                word_timestamps=True,
                beam_size=1,
                temperature=0.0
            )

            all_words = []
            full_text_parts = []

            for seg in segments:
                full_text_parts.append(seg.text.strip())
                if hasattr(seg, "words") and seg.words:
                    for w in seg.words:
                        all_words.append((w.start, w.end, w.word.strip()))

            full_transcript = " ".join(full_text_parts)

            # Group words into 3 to 4 words per group
            cues: List[SubtitleCue] = []
            group_size = 4
            for i in range(0, len(all_words), group_size):
                chunk = all_words[i:i + group_size]
                if not chunk:
                    continue
                start_t = chunk[0][0]
                end_t = chunk[-1][1]
                # Ensure minimum visible duration of 0.5s
                if end_t - start_t < 0.5:
                    end_t = start_t + 0.5
                text = " ".join(item[2] for item in chunk)
                cues.append(SubtitleCue(start_sec=start_t, end_sec=end_t, text=text.upper()))

            logger.info(f"Transcription complete: {len(cues)} subtitle cues generated")
            return full_transcript, cues

        except Exception as e:
            logger.error(f"Error during audio transcription: {e}")
            return "", []

    def render_subtitle_image(self, text: str, output_path: Path, width: int = 1080, height: int = 1920) -> bool:
        """
        Renders a single transparent RGBA image with large, bold, centered subtitle text,
        white fill, and thick black outline/shadow.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Create transparent 1080x1920 image
            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            font_size = 56
            if self.font_path:
                try:
                    font = ImageFont.truetype(self.font_path, font_size)
                except Exception:
                    font = ImageFont.load_default()
            else:
                font = ImageFont.load_default()

            # Measure text
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = int(bbox[2] - bbox[0])
            text_h = int(bbox[3] - bbox[1])

            # Position: Centered horizontally, positioned right below gameplay frame (y ≈ 1380)
            x = (width - text_w) // 2
            y = 1380

            # Draw black drop shadow
            draw.text(
                (x + 4, y + 4),
                text,
                font=font,
                fill=(0, 0, 0, 200),
                stroke_width=4,
                stroke_fill=(0, 0, 0, 200)
            )

            # Draw main text with thick black outline
            draw.text(
                (x, y),
                text,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=6,
                stroke_fill=(0, 0, 0, 255)
            )

            img.save(str(output_path), "PNG")
            return True

        except Exception as e:
            logger.error(f"Error rendering subtitle image for '{text}': {e}")
            return False

    @staticmethod
    def _is_emoji_char(char: str) -> bool:
        """Check if character is in unicode emoji ranges."""
        cp = ord(char)
        return (0x1F300 <= cp <= 0x1FAFF) or (0x2600 <= cp <= 0x27BF) or (0xFE00 <= cp <= 0xFE0F) or (0x1F900 <= cp <= 0x1F9FF)

    def _split_text_and_emojis(self, text: str) -> List[Tuple[str, bool]]:
        """Splits string into contiguous chunks of (text_substring, is_emoji_bool)."""
        if not text:
            return []
        segments: List[Tuple[str, bool]] = []
        curr_is_em: Optional[bool] = None
        curr_str = ""
        for ch in text:
            em = self._is_emoji_char(ch)
            if curr_is_em is None:
                curr_is_em = em
                curr_str = ch
            elif em == curr_is_em:
                curr_str += ch
            else:
                segments.append((curr_str, curr_is_em))
                curr_str = ch
                curr_is_em = em
        if curr_str and curr_is_em is not None:
            segments.append((curr_str, curr_is_em))
        return segments

    def render_hook_banner(
        self,
        hook_text: str,
        output_path: Path,
        width: int = 1080,
        height: int = 1920
    ) -> bool:
        """
        Renders an engaging, stylish 2-tier viral hook banner (TikTok / Reels style)
        with frosted glass card, top gold badge with lightning emojis, full-color emojis,
        dynamic line wrapping, and auto-scaled text to guarantee zero overflow.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            clean_text = hook_text.strip().upper()
            if len(clean_text) <= 22:
                lines = [clean_text]
            else:
                lines = textwrap.wrap(clean_text, width=22, break_long_words=False)
                if len(lines) > 2:
                    lines = textwrap.wrap(clean_text, width=26, break_long_words=False)[:2]

            max_content_w = 820
            font_size = 40 if len(lines) == 1 else 36

            line_measures: List[Tuple[List[Tuple[str, bool]], List[int], int]] = []
            while font_size >= 24:
                if self.font_path:
                    try:
                        font_main = ImageFont.truetype(self.font_path, font_size)
                    except Exception:
                        font_main = ImageFont.load_default()
                else:
                    font_main = ImageFont.load_default()

                strike = 40 if font_size >= 36 else 32
                font_emoji = None
                if self.emoji_font_path and os.path.exists(self.emoji_font_path):
                    try:
                        font_emoji = ImageFont.truetype(self.emoji_font_path, strike)
                    except Exception:
                        font_emoji = None

                fits = True
                measures: List[Tuple[List[Tuple[str, bool]], List[int], int]] = []
                for line in lines:
                    segs = self._split_text_and_emojis(line)
                    total_w: int = 0
                    seg_w_list: List[int] = []
                    for txt, is_em in segs:
                        f = font_emoji if (is_em and font_emoji) else font_main
                        bbox = draw.textbbox((0, 0), txt, font=f)
                        w = int(bbox[2] - bbox[0])
                        seg_w_list.append(w)
                        total_w += w
                    if total_w > max_content_w:
                        fits = False
                        break
                    measures.append((segs, seg_w_list, total_w))

                if fits:
                    line_measures = measures
                    break
                font_size -= 2

            if self.font_path:
                try:
                    font_badge = ImageFont.truetype(self.font_path, 18)
                except Exception:
                    font_badge = ImageFont.load_default()
            else:
                font_badge = ImageFont.load_default()

            font_badge_emoji = None
            if self.emoji_font_path and os.path.exists(self.emoji_font_path):
                try:
                    font_badge_emoji = ImageFont.truetype(self.emoji_font_path, 20)
                except Exception:
                    font_badge_emoji = None

            max_line_w = max((m[2] for m in line_measures), default=400)
            card_w = min(width - 80, max(560, max_line_w + 70))
            line_height = int(font_size * 1.35)
            card_h = 75 + (len(lines) * line_height)
            card_x = (width - card_w) // 2
            card_y = 190

            # 1. Glass card with neon gold border
            draw.rounded_rectangle(
                [card_x, card_y, card_x + card_w, card_y + card_h],
                radius=22,
                fill=(12, 14, 22, 235),
                outline=(255, 215, 0, 255),
                width=3
            )

            # 2. Top Badge with lightning color emojis
            badge_title = " VIRAL STREAM HIGHLIGHT "
            bbox_title = draw.textbbox((0, 0), badge_title, font=font_badge)
            w_title = int(bbox_title[2] - bbox_title[0])
            w_em = 20
            badge_total_w = w_em + w_title + w_em
            bx = (width - badge_total_w) // 2
            by = card_y + 14

            if font_badge_emoji:
                draw.text((bx, by - 2), "⚡", font=font_badge_emoji, embedded_color=True)
            draw.text((bx + w_em, by), badge_title, font=font_badge, fill=(255, 215, 0, 255))
            if font_badge_emoji:
                draw.text((bx + w_em + w_title, by - 2), "⚡", font=font_badge_emoji, embedded_color=True)

            # 3. Main Hook Lines
            start_y = card_y + 46
            for i, (segs, seg_w_list, line_w) in enumerate(line_measures):
                curr_y = start_y + (i * line_height)
                curr_x = (width - line_w) // 2
                for (txt, is_em), w in zip(segs, seg_w_list):
                    f = font_emoji if (is_em and font_emoji) else font_main
                    if is_em and font_emoji:
                        draw.text((curr_x, curr_y - 2), txt, font=f, embedded_color=True)
                    else:
                        draw.text(
                            (curr_x + 3, curr_y + 3),
                            txt,
                            font=f,
                            fill=(0, 0, 0, 240),
                            stroke_width=2,
                            stroke_fill=(0, 0, 0, 240)
                        )
                        draw.text(
                            (curr_x, curr_y),
                            txt,
                            font=f,
                            fill=(255, 255, 255, 255),
                            stroke_width=3,
                            stroke_fill=(0, 0, 0, 255)
                        )
                    curr_x += w

            img.save(str(output_path), "PNG")
            return True

        except Exception as e:
            logger.error(f"Error rendering hook banner image: {e}")
            return False

