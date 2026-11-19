#!/usr/bin/env python3
"""
Manual Clipper for Anti-Clips.
Creates a 40s+ vertical split-screen clip from recorded chunks in temp/.
Options:
  --last N            Use the last N completed chunks (default: 5 chunks ~ 45-50s)
  --duration D        Target clip duration in seconds (default: 45.0, min: 40.0)
  --chunks C1 C2 ...  Specific chunk filenames to stitch
  --title "Custom"    Optional custom title (or auto-generated via Faster-Whisper)
  --facecam POS       Facecam position (auto, top-right, bottom-right, top-left, bottom-left, center)
"""

import os
import sys
import glob
import time
import argparse
import logging
from pathlib import Path
from typing import List, Optional

# Add live-clipper package to path
PROJECT_DIR = Path(__file__).resolve().parent
LIVE_CLIPPER_DIR = PROJECT_DIR / "live-clipper"
sys.path.insert(0, str(LIVE_CLIPPER_DIR))

from live_clipper.config import ClipperConfig
from live_clipper.buffer_manager import BufferManager, Chunk
from live_clipper.transcriber import Transcriber
from live_clipper.video_processor import VideoProcessor
from live_clipper.title_generator import TitleGenerator
from live_clipper.worker import sanitize_filename

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("manual_clip")

def create_manual_clip(
    duration_sec: float = 45.0,
    last_n_chunks: int = 5,
    custom_chunks: Optional[List[str]] = None,
    custom_title: str = "",
    facecam_pos: str = "auto"
) -> bool:
    duration_sec = max(40.0, duration_sec)
    temp_dir = LIVE_CLIPPER_DIR / "temp"
    clips_dir = LIVE_CLIPPER_DIR / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    config = ClipperConfig(
        video_layout="split",
        facecam_position=facecam_pos,
        clip_duration_sec=duration_sec
    )

    buffer_mgr = BufferManager(temp_dir=temp_dir)
    transcriber = Transcriber(config)
    video_proc = VideoProcessor(config)
    title_gen = TitleGenerator(config)

    # 1. Select Chunks
    selected_chunk_objs = []
    if custom_chunks:
        for c in custom_chunks:
            p = temp_dir / c if not Path(c).is_absolute() else Path(c)
            if p.exists() and p.stat().st_size > 500_000:
                selected_chunk_objs.append(buffer_mgr.register_chunk(p))
            else:
                logger.warning(f"Chunk file not found or empty: {c}")
    else:
        # Get sorted chunks from temp
        all_chunk_files = sorted(glob.glob(str(temp_dir / "chunk_*.mp4")))
        # Filter out files currently being written (under 1MB)
        valid_files = [Path(f) for f in all_chunk_files if Path(f).stat().st_size > 1_000_000]
        if not valid_files:
            logger.error(f"No completed chunks found in {temp_dir}.")
            return False

        # Pick the last N chunks
        num_chunks = max(4, last_n_chunks)
        chosen_files = valid_files[-num_chunks:]
        for f in chosen_files:
            selected_chunk_objs.append(buffer_mgr.register_chunk(f))

    if not selected_chunk_objs:
        logger.error("No valid chunks available to construct clip.")
        return False

    logger.info(f"Selected {len(selected_chunk_objs)} chunks:")
    for c in selected_chunk_objs:
        logger.info(f"  • {c.path.name} ({c.path.stat().st_size // 1024} KB)")

    # 2. Stitch and Trim to target duration (40s+)
    raw_assembly = temp_dir / f"manual_assembly_{int(time.time())}.mp4"
    earliest_start = selected_chunk_objs[0].start_time
    logger.info(f"Stitching chunks and slicing {duration_sec:.1f}s raw clip (40s+)...")

    success = buffer_mgr.reconstruct_clip(
        chunks=selected_chunk_objs,
        target_start=earliest_start,
        target_duration=duration_sec,
        output_path=raw_assembly
    )

    if not success or not raw_assembly.exists():
        logger.error("Failed to stitch and trim raw assembly clip.")
        return False

    # 3. Transcribe audio with Faster-Whisper
    logger.info("Transcribing speech with Faster-Whisper...")
    transcript, cues = transcriber.transcribe_audio(raw_assembly)
    logger.info(f"Transcript generated ({len(transcript)} chars, {len(cues)} subtitle cues).")

    # 4. Generate Title & Viral Hook
    if custom_title:
        title = custom_title
        hashtags = ["#ishowspeed", "#shorts", "#viral"]
    else:
        title, hashtags = title_gen.generate_title_and_tags(
            transcript=transcript,
            chat_messages=[],
            channel_name=config.channel
        )
    
    hook_text = title_gen.generate_hook(
        transcript=transcript,
        chat_messages=[],
        channel_name=config.channel,
        title=title
    )
    logger.info(f"Title: {title}")
    logger.info(f"Viral Hook: {hook_text}")

    # 5. Render Split-Screen Vertical Video
    timestamp_prefix = time.strftime("%Y-%m-%d_%H-%M-%S")
    slug = sanitize_filename(title)
    final_mp4 = clips_dir / f"{timestamp_prefix}_{slug}.mp4"
    final_txt = clips_dir / f"{timestamp_prefix}_{slug}.txt"

    def _on_progress(pct: float):
        if int(pct) % 20 == 0:
            logger.info(f"Rendering split vertical video: {int(pct)}%...")

    logger.info(f"Rendering 1080x1920 split vertical video (Top: Streamer Box, Bottom: Game)...")
    ok = video_proc.render_vertical_clip(
        raw_clip_path=raw_assembly,
        output_clip_path=final_mp4,
        subtitle_cues=cues,
        transcriber=transcriber,
        hook_text=hook_text,
        on_progress=_on_progress
    )

    if raw_assembly.exists():
        raw_assembly.unlink(missing_ok=True)

    if not ok or not final_mp4.exists():
        logger.error("Failed to render final vertical clip.")
        return False

    # 6. Write companion metadata TXT
    with open(final_txt, "w", encoding="utf-8") as f:
        f.write(f"Title: {title}\n")
        f.write(f"Viral Hook: {hook_text}\n")
        f.write(f"Hashtags: {' '.join(hashtags)}\n")
        f.write(f"Event Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')} (epoch: {time.time():.2f})\n")
        f.write(f"Transcript:\n{transcript}\n")

    print("\n" + "="*70)
    print(f"🎉 40s+ CLIP SUCCESSFULLY CREATED!")
    print("="*70)
    print(f"📁 Video: {final_mp4}")
    print(f"📄 Metadata: {final_txt}")
    print(f"🏷️  Title: {title}")
    print(f"🔥 Hook: {hook_text}")
    print(f"\nNext steps:")
    print(f"1. Generate viral thumbnail, SEO tags & description: ./process_clips.sh")
    print(f"2. Upload to YouTube channel with 6-min gap:        ./upload_clips.sh")
    print("="*70)
    return True

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manually create 40s+ vertical split clip from recorded chunks.")
    parser.add_argument("--duration", type=float, default=45.0, help="Duration in seconds (default: 45.0, minimum: 40.0)")
    parser.add_argument("--last", type=int, default=5, help="Number of recent chunks to stitch (default: 5 chunks)")
    parser.add_argument("--chunks", nargs="+", help="Specific chunk filenames (e.g. chunk_123.mp4 chunk_124.mp4)")
    parser.add_argument("--title", type=str, default="", help="Custom title (optional)")
    parser.add_argument("--facecam", type=str, choices=["auto", "speed", "mid-left", "top-right", "bottom-right", "top-left", "bottom-left", "center"], default="auto", help="Streamer facecam position (default: auto, 'speed' or 'mid-left' for IShowSpeed layout)")
    args = parser.parse_args()

    create_manual_clip(
        duration_sec=args.duration,
        last_n_chunks=args.last,
        custom_chunks=args.chunks,
        custom_title=args.title,
        facecam_pos=args.facecam
    )
