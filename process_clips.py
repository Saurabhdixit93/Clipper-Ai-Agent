#!/usr/bin/env python3
"""
Single-command processor for all new video clips in live-clipper/clips.
Generates:
1. High-CTR Viral Title with emojis & #shorts
2. SEO-Optimized Description with Hook, CTA & Hashtags
3. Top-Ranking YouTube Tags
4. Viral Custom Thumbnails (1280x720 Standard 16:9 and 1080x1920 Vertical 9:16)
5. Registers clips into upload_metadata.json ready for YouTube upload.
"""

import os
import re
import sys
import glob
import json
import math
import time
import shutil
import logging
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("process_clips")

WORKSPACE_DIR = Path(__file__).resolve().parent
CLIPS_DIR = WORKSPACE_DIR / "live-clipper" / "clips"
THUMBS_DIR = CLIPS_DIR / "thumbnails"
METADATA_FILE = CLIPS_DIR / "upload_metadata.json"

FONT_IMPACT = "/System/Library/Fonts/Supplemental/Impact.ttf"
FONT_ARIAL_BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"

def draw_text_with_outline(draw, pos, text, font, fill_color=(255, 230, 0), outline_color=(0, 0, 0), outline_width=11, shadow_offset=(6, 8)):
    x, y = pos
    sx, sy = shadow_offset
    if shadow_offset:
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                draw.text((x + sx + dx, y + sy + dy), text, font=font, fill=(0, 0, 0, 180))
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx * dx + dy * dy <= outline_width * outline_width:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
    draw.text((x, y), text, font=font, fill=fill_color)

def draw_badge(draw, box, text, font, bg_color=(235, 15, 40), text_color=(255, 255, 255), border_color=(255, 255, 255), radius=12):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle([x1+4, y1+4, x2+4, y2+4], radius=radius, fill=(0, 0, 0, 160))
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=bg_color, outline=border_color, width=4)
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = x1 + (x2 - x1 - tw) / 2 - bbox[0]
    ty = y1 + (y2 - y1 - th) / 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=text_color)

def draw_arrow(draw, start, end, color=(255, 35, 35), outline=(255, 255, 255), width=16):
    draw.line([start, end], fill=outline, width=width + 8)
    draw.line([start, end], fill=color, width=width)
    x1, y1 = start
    x2, y2 = end
    angle = math.atan2(y2 - y1, x2 - x1)
    head_len = 45
    head_angle = math.pi / 6
    p1 = (x2 - head_len * math.cos(angle - head_angle), y2 - head_len * math.sin(angle - head_angle))
    p2 = (x2 - head_len * math.cos(angle + head_angle), y2 - head_len * math.sin(angle + head_angle))
    draw.polygon([end, p1, p2], fill=color, outline=outline)

def enhance_frame(img):
    img = ImageEnhance.Color(img).enhance(1.30)
    img = ImageEnhance.Contrast(img).enhance(1.20)
    img = ImageEnhance.Sharpness(img).enhance(1.25)
    return img

def create_16_9_thumbnail(raw_frame_path, output_path, badge_text, line1, line2, arrow=True):
    im = Image.open(raw_frame_path).convert("RGBA")
    sharp_crop = im.crop((0, 656, 1080, 1264)).resize((1280, 720), Image.Resampling.LANCZOS)
    sharp_crop = enhance_frame(sharp_crop)
    base = sharp_crop.convert("RGBA")
    
    grad = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(grad)
    for x in range(760):
        alpha = int(220 * (1.0 - (x / 760.0)**1.5))
        g_draw.line([(x, 0), (x, 720)], fill=(0, 0, 0, alpha))
    base = Image.alpha_composite(base, grad)
    draw = ImageDraw.Draw(base)
    
    font_badge = ImageFont.truetype(FONT_ARIAL_BLACK, 36)
    font_main = ImageFont.truetype(FONT_IMPACT, 96)
    font_accent = ImageFont.truetype(FONT_IMPACT, 102)
    
    bbox = font_badge.getbbox(badge_text)
    bw = (bbox[2] - bbox[0]) + 40
    bh = (bbox[3] - bbox[1]) + 24
    draw_badge(draw, [40, 50, 40 + bw, 50 + bh], badge_text, font_badge)
    
    draw_text_with_outline(draw, (40, 140), line1, font_main, fill_color=(255, 255, 255), outline_width=11)
    draw_text_with_outline(draw, (40, 260), line2, font_accent, fill_color=(255, 230, 0), outline_width=12)
    
    if arrow:
        draw_arrow(draw, (530, 420), (850, 380))
        
    draw.rectangle([0, 0, 1279, 719], outline=(255, 230, 0), width=6)
    rgb_thumb = base.convert("RGB")
    rgb_thumb.save(output_path, "JPEG", quality=95)
    return output_path

def create_9_16_thumbnail(raw_frame_path, output_path, badge_text, line1, line2):
    im = Image.open(raw_frame_path).convert("RGBA")
    im = enhance_frame(im)
    overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    o_draw = ImageDraw.Draw(overlay)
    for y in range(480):
        alpha = int(220 * (1.0 - (y / 480.0)**2))
        o_draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
        
    base = Image.alpha_composite(im, overlay)
    draw = ImageDraw.Draw(base)
    
    font_badge = ImageFont.truetype(FONT_ARIAL_BLACK, 44)
    font_main = ImageFont.truetype(FONT_IMPACT, 108)
    font_accent = ImageFont.truetype(FONT_IMPACT, 114)
    
    bbox = font_badge.getbbox(badge_text)
    bw = (bbox[2] - bbox[0]) + 50
    bh = (bbox[3] - bbox[1]) + 30
    bx = (1080 - bw) / 2
    draw_badge(draw, [bx, 100, bx + bw, 100 + bh], badge_text, font_badge)
    
    b1 = font_main.getbbox(line1)
    w1 = b1[2] - b1[0]
    draw_text_with_outline(draw, ((1080 - w1) / 2, 210), line1, font_main, fill_color=(255, 255, 255), outline_width=12)
    
    b2 = font_accent.getbbox(line2)
    w2 = b2[2] - b2[0]
    draw_text_with_outline(draw, ((1080 - w2) / 2, 340), line2, font_accent, fill_color=(255, 230, 0), outline_width=14)
    
    draw.rectangle([0, 0, 1079, 1919], outline=(255, 230, 0), width=8)
    rgb_thumb = base.convert("RGB")
    rgb_thumb.save(output_path, "JPEG", quality=95)
    return output_path

def is_file_ready(path, wait_sec=1):
    """Ensure file is completely written by checking size stability."""
    try:
        s1 = path.stat().st_size
        time.sleep(wait_sec)
        s2 = path.stat().st_size
        return s1 == s2 and s1 > 500_000  # at least 500KB
    except Exception:
        return False

def extract_best_frame(video_path, out_frame_path):
    for sec in [10, 5, 2]:
        cmd = [
            "ffmpeg", "-y", "-ss", str(sec),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(out_frame_path)
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if out_frame_path.exists() and out_frame_path.stat().st_size > 5000:
            return True
    return False

def parse_companion_text(txt_path):
    info = {"title": "", "hook": "", "transcript": ""}
    if not txt_path.exists():
        return info
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    for line in content.splitlines():
        if line.startswith("Title:"):
            info["title"] = line.replace("Title:", "").strip()
        elif line.startswith("Viral Hook:"):
            info["hook"] = line.replace("Viral Hook:", "").strip()
        elif line.startswith("Transcript:"):
            info["transcript"] = content.split("Transcript:", 1)[1].strip()
            break
    return info

def generate_viral_metadata(video_path, txt_path):
    info = parse_companion_text(txt_path)
    base_name = video_path.stem
    transcript = info["transcript"].upper()
    hook = info["hook"].upper()
    
    cleaned_name = re.sub(r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_", "", base_name).replace("-", " ")
    
    if "SIUUU" in base_name.upper() or "CR7" in transcript:
        badge = "🐐 ICONIC SIUUU!"
        line1 = "SPEED DROPPED"
        line2 = "THE SIUUU! 🔥"
        title = "IShowSpeed DROPPED THE ICONIC SIUUU! 🐐🔥 #shorts"
        desc = (
            "IShowSpeed hits the iconic Ronaldo SIUUU live on stream! 🐐🔥\n\n"
            "Drop a LIKE and SIUUU in the comments!\n\n"
            "🔔 Subscribe for daily IShowSpeed stream highlights and funny moments!\n\n"
            "#shorts #ishowspeed #speed #ronaldo #siuuu #cr7 #goat #viral"
        )
        tags = ["ishowspeed", "speed", "ishowspeed siuuu", "ronaldo", "cr7", "speed ronaldo", "shorts", "viral shorts"]
    elif "QUIET" in base_name.upper() or "SHUT UP" in transcript:
        badge = "🤫 BE QUIET!!"
        line1 = "EVERYBODY BE"
        line2 = "QUIET RIGHT NOW! 💀"
        title = "IShowSpeed TOLD EVERYONE TO BE QUIET 😭💀 #shorts"
        desc = (
            "Speed lost his mind shouting \"EVERYBODY BE QUIET!\" 💀😭 Nobody was listening!\n\n"
            "🔔 Subscribe for daily IShowSpeed moments!\n\n"
            "#shorts #ishowspeed #speed #funny #streamclips #viral"
        )
        tags = ["ishowspeed", "speed", "ishowspeed rage", "be quiet", "funny moments", "shorts", "viral"]
    elif "KILL" in base_name.upper():
        badge = "😱 HOW DO I KILL IT?!"
        line1 = "HOW DO I"
        line2 = "KILL THIS?! 💀"
        title = "IShowSpeed PANICKING \"HOW DO I KILL IT?!\" 😭💀 #shorts"
        desc = (
            "Speed started panicking mid-game trying to figure out how to defeat the enemy! 😭💀\n\n"
            "🔔 Subscribe for daily IShowSpeed gaming moments!\n\n"
            "#shorts #ishowspeed #speed #gaming #rage #funny #viral"
        )
        tags = ["ishowspeed", "speed", "speed gaming", "rage", "funny clips", "shorts", "viral shorts"]
    else:
        badge = "🔥 MUST WATCH"
        line1 = "SPEED DID NOT"
        line2 = "EXPECT THIS! 💀"
        title = f"IShowSpeed {cleaned_name.title()[:42]} 😱💀 #shorts"
        desc = (
            f"IShowSpeed wild stream moment: {cleaned_name}! Watch what happened live!\n\n"
            "🔔 Subscribe for daily IShowSpeed stream highlights and funny moments!\n\n"
            "#shorts #ishowspeed #speed #streamclips #viral #trending"
        )
        tags = ["ishowspeed", "speed", "speed live", "speed funny moments", "stream clips", "shorts", "viral shorts"]

    return {
        "badge": badge,
        "line1": line1,
        "line2": line2,
        "title": title,
        "description": desc,
        "tags": tags
    }

def process_all_clips():
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    frames_dir = THUMBS_DIR / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    
    existing_data = {"clips": []}
    if METADATA_FILE.exists():
        try:
            with open(METADATA_FILE, "r") as f:
                existing_data = json.load(f)
        except Exception:
            existing_data = {"clips": []}
            
    # Clean up entries for files that don't exist
    existing_data["clips"] = [c for c in existing_data.get("clips", []) if Path(c.get("video_path", "")).exists()]
    existing_paths = {c["video_path"] for c in existing_data["clips"]}
    
    mp4_files = sorted(glob.glob(str(CLIPS_DIR / "*.mp4")))
    if not mp4_files:
        logger.info(f"No active .mp4 video clips found in {CLIPS_DIR}.")
        with open(METADATA_FILE, "w") as f:
            json.dump(existing_data, f, indent=2)
        return

    logger.info(f"Found {len(mp4_files)} clip(s) in {CLIPS_DIR}.")
    
    next_id = max([c.get("id", 0) for c in existing_data["clips"]] + [0]) + 1
    
    processed_count = 0
    for video_str in mp4_files:
        vpath = Path(video_str)
        txt_path = vpath.with_suffix(".txt")
        
        # Verify file is not actively writing
        if not is_file_ready(vpath):
            logger.info(f"Clip {vpath.name} is currently writing, waiting for next run.")
            continue
            
        thumb_16x9 = THUMBS_DIR / f"{vpath.stem}_thumb_16x9.jpg"
        thumb_9x16 = THUMBS_DIR / f"{vpath.stem}_thumb_9x16.jpg"
        
        if str(vpath) in existing_paths and thumb_16x9.exists() and thumb_9x16.exists():
            continue
            
        logger.info(f"--> Processing new clip: {vpath.name}")
        meta = generate_viral_metadata(vpath, txt_path)
        
        frame_path = frames_dir / f"{vpath.stem}_frame.jpg"
        if not extract_best_frame(vpath, frame_path):
            logger.warning(f"Could not extract frame for {vpath.name}, skipping.")
            continue
            
        create_16_9_thumbnail(frame_path, thumb_16x9, meta["badge"], meta["line1"], meta["line2"])
        create_9_16_thumbnail(frame_path, thumb_9x16, meta["badge"], meta["line1"], meta["line2"])
        
        clip_entry = {
            "id": next_id,
            "video_filename": vpath.name,
            "video_path": str(vpath),
            "thumbnail_16x9": str(thumb_16x9),
            "thumbnail_9x16": str(thumb_9x16),
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "category_id": "24"
        }
        
        existing_data["clips"].append(clip_entry)
        next_id += 1
        processed_count += 1
        logger.info(f"Generated viral thumbnail, title, description, and tags for: {vpath.name}")

    with open(METADATA_FILE, "w") as f:
        json.dump(existing_data, f, indent=2)
        
    logger.info(f"Done! {processed_count} new clip(s) prepared. Total pending clips: {len(existing_data['clips'])}.")
    print("\n" + "="*70)
    print("READY FOR UPLOAD! Run ./upload_clips.sh to upload all clips to YouTube.")
    print("="*70)

if __name__ == "__main__":
    process_all_clips()
