"""Vertical video rendering engine with split-screen (top streamer camera box, bottom gameplay) or blurred background."""

import os
import time
import shutil
import logging
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image
from live_clipper.config import ClipperConfig
from live_clipper.transcriber import SubtitleCue, Transcriber

logger = logging.getLogger("live_clipper.video_processor")


class VideoProcessor:
    """
    Renders 1080x1920 vertical videos with:
      Option A ("split" - default):
        1. Top half (1080x960): Streamer camera box (the facecam box cropped & scaled to fill top half).
        2. Bottom half (1080x960): The rest of the stream showing gameplay (streamer camera box cropped out).
        3. Clean visual separator line between the two halves.
      Option B ("fit_blur"):
        Blurred background with centered gameplay.
      Common elements:
        4. Dynamic top hook banner.
        5. Synchronized burned subtitles timed to audio.
    """
    def __init__(self, config: ClipperConfig):
        self.config = config

    def get_layout_crops(self, raw_clip_path: Path) -> Tuple[str, str]:
        """
        Calculates FFmpeg crop filters for:
          - Top half: Streamer camera box (isolated from corner and scaled to 1080x960)
          - Bottom half: Gameplay screen (the rest of the stream, with camera box excluded)
        Returns: (top_crop_filter, bottom_crop_filter)
        """
        pos = (getattr(self.config, "facecam_position", "auto") or "auto").lower()

        if pos in ("speed", "mid-left", "speed-layout", "ishowspeed"):
            # Streamer facecam box is on mid-left (Speed layout: x~0..21%, y~41..64%)
            # Top: Streamer camera box cleanly framed and isolated
            top_filter = "crop=iw*0.21:ih*0.23:0:ih*0.41"
            # Bottom: Gameplay zoomed and shifted to the right so facecam, viewer count and minimap are hidden
            bottom_filter = "crop=iw*0.76:ih:iw*0.22:0"
            return top_filter, bottom_filter
        elif pos == "top-right":
            # Streamer camera box is in top-right corner (~30% width, ~40% height)
            top_filter = "crop=iw*0.30:ih*0.40:iw*0.70:0"
            # Gameplay is left/center of screen, excluding top-right camera
            bottom_filter = "crop=iw*0.72:ih:0:0"
            return top_filter, bottom_filter
        elif pos == "bottom-right":
            # Streamer camera box is in bottom-right corner
            top_filter = "crop=iw*0.30:ih*0.40:iw*0.70:ih*0.60"
            bottom_filter = "crop=iw*0.72:ih:0:0"
            return top_filter, bottom_filter
        elif pos == "top-left":
            # Streamer camera box is in top-left corner
            top_filter = "crop=iw*0.30:ih*0.40:0:0"
            bottom_filter = "crop=iw*0.72:ih:iw*0.28:0"
            return top_filter, bottom_filter
        elif pos == "bottom-left":
            # Streamer camera box is in bottom-left corner
            top_filter = "crop=iw*0.30:ih*0.40:0:ih*0.60"
            bottom_filter = "crop=iw*0.72:ih:iw*0.28:0"
            return top_filter, bottom_filter
        elif pos == "center":
            # Streamer is full-screen or centered (e.g. Just Chatting / Reaction)
            top_filter = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0"
            bottom_filter = "crop=iw*0.75:ih*0.75:(iw-iw*0.75)/2:(ih-ih*0.75)/2"
            return top_filter, bottom_filter

        # If streamer channel is ishowspeed, default to mid-left Speed layout directly
        channel_name = getattr(self.config, "channel", "").lower()
        if "speed" in channel_name:
            logger.info("Using IShowSpeed mid-left facecam layout preset")
            return "crop=iw*0.21:ih*0.23:0:ih*0.41", "crop=iw*0.76:ih:iw*0.22:0"

        # "auto" detection: Sample a frame and locate the streamer camera box via skin density
        try:
            import numpy as np
            sample_frame = self.config.temp_dir / f"sample_{int(time.time()*1000)}.jpg"
            cmd = ["ffmpeg", "-y", "-ss", "2", "-i", str(raw_clip_path), "-vframes", "1", str(sample_frame)]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)

            if sample_frame.exists():
                im = Image.open(sample_frame).convert("YCbCr")
                arr = np.array(im)
                cb, cr = arr[:, :, 1], arr[:, :, 2]
                skin_mask = (cb >= 77) & (cb <= 127) & (cr >= 133) & (cr <= 173)
                h, w = skin_mask.shape

                # Check regions where stream camera boxes live
                scores = {
                    "mid-left": int(skin_mask[int(h*0.35):int(h*0.68), 0:int(w*0.25)].sum()),
                    "top-right": int(skin_mask[0:int(h*0.45), int(w*0.65):w].sum()),
                    "bottom-right": int(skin_mask[int(h*0.55):h, int(w*0.65):w].sum()),
                    "top-left": int(skin_mask[0:int(h*0.45), 0:int(w*0.35)].sum()),
                    "bottom-left": int(skin_mask[int(h*0.55):h, 0:int(w*0.35)].sum()),
                    "center": int(skin_mask[h//4:3*h//4, w//4:3*w//4].sum()),
                }
                sample_frame.unlink(missing_ok=True)
                
                best_loc = max(scores.keys(), key=lambda k: scores[k])
                logger.info(f"Auto-detected streamer camera box position: {best_loc}")

                if best_loc == "mid-left":
                    return "crop=iw*0.21:ih*0.23:0:ih*0.41", "crop=iw*0.76:ih:iw*0.22:0"
                elif best_loc == "top-right":
                    return "crop=iw*0.30:ih*0.40:iw*0.70:0", "crop=iw*0.72:ih:0:0"
                elif best_loc == "bottom-right":
                    return "crop=iw*0.30:ih*0.40:iw*0.70:ih*0.60", "crop=iw*0.72:ih:0:0"
                elif best_loc == "top-left":
                    return "crop=iw*0.30:ih*0.40:0:0", "crop=iw*0.72:ih:iw*0.28:0"
                elif best_loc == "bottom-left":
                    return "crop=iw*0.30:ih*0.40:0:ih*0.60", "crop=iw*0.72:ih:iw*0.28:0"
                else:
                    return "crop=iw*0.21:ih*0.23:0:ih*0.41", "crop=iw*0.76:ih:iw*0.22:0"
        except Exception as e:
            logger.debug(f"Auto camera detection exception, defaulting to mid-left camera box: {e}")

        # Standard gaming stream default
        return "crop=iw*0.21:ih*0.23:0:ih*0.41", "crop=iw*0.76:ih:iw*0.22:0"

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
            cmd_inputs = ["-i", str(raw_clip_path)]

            # Check chosen layout mode
            if getattr(self.config, "video_layout", "split") == "split":
                # SPLIT LAYOUT:
                # Top half (1080x960): Streamer camera box (isolated & scaled to fill top half)
                # Bottom half (1080x960): Gameplay (the rest of the screen with camera box cropped out)
                top_crop, bottom_crop = self.get_layout_crops(raw_clip_path)
                divider_col = getattr(self.config, "divider_color", "yellow")
                divider_filter = (
                    f",drawbox=y=958:h=4:color={divider_col}@0.95:t=fill"
                    if divider_col != "none" else ""
                )

                filter_chains = [
                    f"[0:v]{top_crop},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,eq=contrast=1.05:saturation=1.1[top]",
                    f"[0:v]{bottom_crop},scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960[bottom]",
                    f"[top][bottom]vstack=inputs=2{divider_filter}[base_v]"
                ]
            else:
                # FIT_BLUR LAYOUT: Blurred background + centered gameplay
                filter_chains = [
                    "[0:v]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,boxblur=8:1,scale=1080:1920:flags=fast_bilinear,eq=brightness=-0.15[bg]",
                    "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease[fg]",
                    "[bg][fg]overlay=(W-w)/2:(H-h)/2[base_v]"
                ]

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

            logger.info(f"Rendering vertical video clip ({getattr(self.config, 'video_layout', 'split')} layout) to: {output_clip_path.name}")
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
