# pyright: reportMissingImports=false
"""Faster-Whisper transcription and subtitle generation engine."""

import os
import sys
import logging
import platform
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
        self._init_model()

    def _find_font(self) -> Optional[str]:
        """Locate suitable bold font for TikTok/Shorts style captions."""
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Black.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial Bold.ttf",
            "/Library/Fonts/Arial.ttf"
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
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

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

    def render_hook_banner(
        self,
        hook_text: str,
        output_path: Path,
        width: int = 1080,
        height: int = 1920
    ) -> bool:
        """
        Renders an eye-catching viral hook banner at top of vertical video (y ≈ 200).
        Includes a stylish rounded pill background with glowing border and bold text.
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            font_size = 46
            if self.font_path:
                try:
                    font = ImageFont.truetype(self.font_path, font_size)
                except Exception:
                    font = ImageFont.load_default()
            else:
                font = ImageFont.load_default()

            clean_text = hook_text.strip().upper()
            if len(clean_text) > 42:
                clean_text = clean_text[:40] + "..."

            bbox = draw.textbbox((0, 0), clean_text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            pad_x = 34
            pad_y = 16
            box_w = min(width - 80, text_w + (pad_x * 2))
            box_h = text_h + (pad_y * 2)
            box_x0 = (width - box_w) // 2
            box_y0 = 210
            box_x1 = box_x0 + box_w
            box_y1 = box_y0 + box_h

            # Draw rounded pill box background (dark obsidian with gold accent border)
            draw.rounded_rectangle(
                [box_x0, box_y0, box_x1, box_y1],
                radius=16,
                fill=(12, 12, 18, 225),
                outline=(255, 215, 0, 255),
                width=3
            )

            # Center text inside the pill
            text_x = box_x0 + (box_w - text_w) // 2
            text_y = box_y0 + (box_h - text_h) // 2 - 2

            # Drop shadow
            draw.text(
                (text_x + 3, text_y + 3),
                clean_text,
                font=font,
                fill=(0, 0, 0, 220),
                stroke_width=2,
                stroke_fill=(0, 0, 0, 220)
            )

            # Main text
            draw.text(
                (text_x, text_y),
                clean_text,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=3,
                stroke_fill=(0, 0, 0, 255)
            )

            img.save(str(output_path), "PNG")
            return True

        except Exception as e:
            logger.error(f"Error rendering hook banner image: {e}")
            return False

