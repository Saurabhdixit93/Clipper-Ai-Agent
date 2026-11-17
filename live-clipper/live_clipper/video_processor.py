"""Vertical video rendering engine with 1080x1920 blurred background and burned captions."""

import os
import time
import shutil
import logging
import subprocess
from pathlib import Path
from typing import List, Optional

from live_clipper.config import ClipperConfig
from live_clipper.transcriber import SubtitleCue, Transcriber

logger = logging.getLogger("live_clipper.video_processor")


class VideoProcessor:
    """
    Renders 1080x1920 vertical videos with:
      1. Blurred, slightly darkened background using the source video.
      2. Centered gameplay preserving aspect ratio without distortion.
      3. Synchronized AAC audio.
      4. Large, bold, centered burned subtitles (3-4 words/group) timed to audio.
    """
    def __init__(self, config: ClipperConfig):
        self.config = config

    def render_vertical_clip(
        self,
        raw_clip_path: Path,
        output_clip_path: Path,
        subtitle_cues: Optional[List[SubtitleCue]] = None,
        transcriber: Optional[Transcriber] = None,
        hook_text: Optional[str] = None,
        on_progress: Optional[object] = None
    ) -> bool:
        raw_clip_path = Path(raw_clip_path)
        output_clip_path = Path(output_clip_path)
        output_clip_path.parent.mkdir(parents=True, exist_ok=True)

        if not raw_clip_path.exists() or raw_clip_path.stat().st_size == 0:
            logger.error(f"Input clip missing or empty: {raw_clip_path}")
            return False

        temp_sub_dir = self.config.temp_dir / f"subtitles_{int(time.time()*1000)}"
        temp_sub_dir.mkdir(parents=True, exist_ok=True)
        created_sub_imgs: List[Path] = []

        try:
            # 1. Base vertical filter: Blurred darkened background + centered gameplay
            filter_chains = [
                "[0:v]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,boxblur=8:1,scale=1080:1920:flags=fast_bilinear,eq=brightness=-0.15[bg]",
                "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease[fg]",
                "[bg][fg]overlay=(W-w)/2:(H-h)/2[base_v]"
            ]

            cmd_inputs = ["-i", str(raw_clip_path)]
            last_stream = "[base_v]"

            # 2. Add dynamic top hook banner overlay if available
            if hook_text and transcriber:
                hook_img_path = temp_sub_dir / "hook_banner.png"
                if transcriber.render_hook_banner(hook_text, hook_img_path):
                    input_index = len(cmd_inputs) // 2
                    cmd_inputs.extend(["-i", str(hook_img_path)])
                    filter_chains.append(f"{last_stream}[{input_index}:v]overlay=0:0[v_hook]")
                    last_stream = "[v_hook]"

            # 3. Add timed subtitle overlays if available
            valid_cues = [c for c in (subtitle_cues or []) if c.text.strip()]
            if valid_cues and transcriber:
                for idx, cue in enumerate(valid_cues):
                    sub_img_path = temp_sub_dir / f"cue_{idx:03d}.png"
                    if transcriber.render_subtitle_image(cue.text, sub_img_path):
                        created_sub_imgs.append(sub_img_path)
                        input_index = len(cmd_inputs) // 2
                        cmd_inputs.extend(["-i", str(sub_img_path)])
                        
                        next_stream = f"[v_sub_{idx}]" if idx < len(valid_cues) - 1 else "[final_v]"
                        # Timing filter with enable
                        start_t = max(0.0, cue.start_sec)
                        end_t = max(start_t + 0.3, cue.end_sec)
                        filter_chains.append(
                            f"{last_stream}[{input_index}:v]overlay=0:0:enable='between(t,{start_t:.3f},{end_t:.3f})'{next_stream}"
                        )
                        last_stream = next_stream

            final_video_label = last_stream if last_stream != "[base_v]" else "[base_v]"
            full_filter = ";".join(filter_chains)

            ffmpeg_cmd = [
                "ffmpeg", "-y", "-v", "error",
                *cmd_inputs,
                "-filter_complex", full_filter,
                "-map", final_video_label,
                "-map", "0:a?",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                "-progress", "pipe:1",
                str(output_clip_path)
            ]

            logger.info(f"Rendering vertical video clip with hook to: {output_clip_path.name}")
            proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            target_us = self.config.clip_duration_sec * 1_000_000
            if proc.stdout:
                try:
                    for line in proc.stdout:
                        line = line.strip()
                        if line.startswith("out_time_us="):
                            try:
                                us = int(line.split("=")[1])
                                pct = min(100.0, (us / target_us) * 100.0)
                                if callable(on_progress):
                                    on_progress(pct)
                            except Exception:
                                pass
                finally:
                    proc.stdout.close()

            proc.wait(timeout=180)
            err = ""
            if proc.stderr:
                try:
                    err = proc.stderr.read()
                finally:
                    proc.stderr.close()

            if proc.returncode != 0:
                logger.error(f"FFmpeg vertical render failed: {err}")
                return False

            return output_clip_path.exists() and output_clip_path.stat().st_size > 0

        except Exception as e:
            logger.error(f"Exception during vertical clip rendering: {e}")
            return False
        finally:
            if temp_sub_dir.exists():
                try:
                    shutil.rmtree(temp_sub_dir, ignore_errors=True)
                except Exception:
                    pass

