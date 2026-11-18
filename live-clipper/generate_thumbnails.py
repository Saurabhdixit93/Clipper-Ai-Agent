#!/usr/bin/env python3
"""
Viral Thumbnail Generator for YouTube & YouTube Shorts clips.
Generates:
1. 1280x720 Standard 16:9 YouTube Thumbnail (YouTube API standard)
2. 1080x1920 Vertical 9:16 Shorts Thumbnail / Cover
"""

import os
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

FONT_IMPACT = "/System/Library/Fonts/Supplemental/Impact.ttf"
FONT_ARIAL_BLACK = "/System/Library/Fonts/Supplemental/Arial Black.ttf"

def draw_text_with_outline(draw, pos, text, font, fill_color=(255, 235, 0), outline_color=(0, 0, 0), outline_width=10, shadow_offset=(6, 8)):
    x, y = pos
    sx, sy = shadow_offset
    
    # Draw drop shadow
    if shadow_offset:
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                draw.text((x + sx + dx, y + sy + dy), text, font=font, fill=(0, 0, 0, 180))
                
    # Draw outline
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx * dx + dy * dy <= outline_width * outline_width:
                draw.text((x + dx, y + dy), text, font=font, fill=outline_color)
                
    # Draw main text
    draw.text((x, y), text, font=font, fill=fill_color)

def draw_badge(draw, box, text, font, bg_color=(230, 20, 20), text_color=(255, 255, 255), border_color=(255, 255, 255), radius=12):
    x1, y1, x2, y2 = box
    # Draw shadow
    draw.rounded_rectangle([x1+4, y1+4, x2+4, y2+4], radius=radius, fill=(0, 0, 0, 160))
    # Draw background
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=bg_color, outline=border_color, width=4)
    # Draw text centered
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = x1 + (x2 - x1 - tw) / 2 - bbox[0]
    ty = y1 + (y2 - y1 - th) / 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=text_color)

def draw_arrow(draw, start, end, color=(255, 30, 30), outline=(255, 255, 255), width=14):
    """Draw a dramatic curved or straight viral arrow with head."""
    x1, y1 = start
    x2, y2 = end
    # Draw outline line
    draw.line([start, end], fill=outline, width=width + 8)
    draw.line([start, end], fill=color, width=width)
    
    # Calculate arrowhead
    angle = math.atan2(y2 - y1, x2 - x1)
    head_len = 45
    head_angle = math.pi / 6  # 30 deg
    
    p1 = (x2 - head_len * math.cos(angle - head_angle), y2 - head_len * math.sin(angle - head_angle))
    p2 = (x2 - head_len * math.cos(angle + head_angle), y2 - head_len * math.sin(angle + head_angle))
    
    # Draw arrow head with outline
    draw.polygon([end, p1, p2], fill=color, outline=outline)

def add_vignette(img, intensity=0.45):
    """Adds a dark radial gradient vignette around the frame to make the center pop."""
    w, h = img.size
    vignette = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    v_draw = ImageDraw.Draw(vignette)
    
    cx, cy = w / 2, h / 2
    max_radius = math.sqrt(cx**2 + cy**2)
    
    # Draw radial gradient using steps
    num_steps = 30
    for i in range(num_steps):
        r = max_radius * (1.0 - i / num_steps)
        alpha = int(255 * intensity * (i / num_steps)**2)
        box = [cx - r, cy - r, cx + r, cy + r]
        v_draw.ellipse(box, fill=(0, 0, 0, alpha))
        
    vignette = vignette.filter(ImageFilter.GaussianBlur(30))
    img.paste(vignette, (0, 0), vignette)
    return img

def enhance_frame(img):
    """Boosts contrast, saturation and sharpness for YouTube thumbnail pop."""
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1.30)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.20)
    enhancer = ImageEnhance.Sharpness(img)
    img = enhancer.enhance(1.25)
    return img

def create_16_9_thumbnail(raw_frame_path, output_path, badge_text, line1, line2, arrow_coords=None):
    """Generates standard 1280x720 YouTube custom thumbnail."""
    im = Image.open(raw_frame_path).convert("RGBA")
    
    # Extract sharp center (1080x608) and scale to 1280x720
    sharp_crop = im.crop((0, 656, 1080, 1264)).resize((1280, 720), Image.Resampling.LANCZOS)
    sharp_crop = enhance_frame(sharp_crop)
    
    # Base canvas
    base = sharp_crop.convert("RGBA")
    
    # Add dark gradient on left side for text readability
    grad = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    g_draw = ImageDraw.Draw(grad)
    for x in range(750):
        alpha = int(220 * (1.0 - (x / 750.0)**1.5))
        g_draw.line([(x, 0), (x, 720)], fill=(0, 0, 0, alpha))
    base = Image.alpha_composite(base, grad)
    
    draw = ImageDraw.Draw(base)
    
    # Fonts
    font_badge = ImageFont.truetype(FONT_ARIAL_BLACK, 36)
    font_main = ImageFont.truetype(FONT_IMPACT, 96)
    font_accent = ImageFont.truetype(FONT_IMPACT, 102)
    
    # 1. Draw Badge
    badge_bbox = font_badge.getbbox(badge_text)
    bw = (badge_bbox[2] - badge_bbox[0]) + 40
    bh = (badge_bbox[3] - badge_bbox[1]) + 24
    draw_badge(draw, [40, 50, 40 + bw, 50 + bh], badge_text, font_badge, bg_color=(235, 15, 40), text_color=(255, 255, 255))
    
    # 2. Draw Text Lines
    draw_text_with_outline(draw, (40, 140), line1, font_main, fill_color=(255, 255, 255), outline_width=11)
    draw_text_with_outline(draw, (40, 260), line2, font_accent, fill_color=(255, 230, 0), outline_width=12)
    
    # 3. Draw Arrow if coordinates provided
    if arrow_coords:
        start_pt, end_pt = arrow_coords
        draw_arrow(draw, start_pt, end_pt, color=(255, 35, 35), outline=(255, 255, 255), width=16)
        
    # 4. Outer border
    draw.rectangle([0, 0, 1279, 719], outline=(255, 230, 0), width=6)
    
    # Save as high-quality JPEG
    rgb_thumb = base.convert("RGB")
    rgb_thumb.save(output_path, "JPEG", quality=95)
    print(f"Generated 16:9 thumbnail: {output_path}")
    return output_path

def create_vertical_thumbnail(raw_frame_path, output_path, badge_text, line1, line2):
    """Generates 1080x1920 vertical Shorts cover thumbnail."""
    im = Image.open(raw_frame_path).convert("RGBA")
    im = enhance_frame(im)
    
    # Add dark banner overlay on top and bottom
    overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    o_draw = ImageDraw.Draw(overlay)
    
    # Top overlay for badge & title
    for y in range(480):
        alpha = int(220 * (1.0 - (y / 480.0)**2))
        o_draw.line([(0, y), (1080, y)], fill=(0, 0, 0, alpha))
        
    base = Image.alpha_composite(im, overlay)
    draw = ImageDraw.Draw(base)
    
    font_badge = ImageFont.truetype(FONT_ARIAL_BLACK, 44)
    font_main = ImageFont.truetype(FONT_IMPACT, 108)
    font_accent = ImageFont.truetype(FONT_IMPACT, 114)
    
    # Draw badge
    badge_bbox = font_badge.getbbox(badge_text)
    bw = (badge_bbox[2] - badge_bbox[0]) + 50
    bh = (badge_bbox[3] - badge_bbox[1]) + 30
    bx = (1080 - bw) / 2
    draw_badge(draw, [bx, 100, bx + bw, 100 + bh], badge_text, font_badge, bg_color=(235, 15, 40), text_color=(255, 255, 255))
    
    # Draw Lines centered
    b1 = font_main.getbbox(line1)
    w1 = b1[2] - b1[0]
    draw_text_with_outline(draw, ((1080 - w1) / 2, 210), line1, font_main, fill_color=(255, 255, 255), outline_width=12)
    
    b2 = font_accent.getbbox(line2)
    w2 = b2[2] - b2[0]
    draw_text_with_outline(draw, ((1080 - w2) / 2, 340), line2, font_accent, fill_color=(255, 230, 0), outline_width=14)
    
    # Outer border
    draw.rectangle([0, 0, 1079, 1919], outline=(255, 230, 0), width=8)
    
    rgb_thumb = base.convert("RGB")
    rgb_thumb.save(output_path, "JPEG", quality=95)
    print(f"Generated 9:16 vertical thumbnail: {output_path}")
    return output_path

if __name__ == "__main__":
    out_dir = Path("/Users/saurabhdixit/Anti-Clips/live-clipper/clips/thumbnails")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    clips_config = [
        {
            "name": "2026-11-18_20-12-54_Ishowspeed-Bam-bam-bam-bam-bam-map",
            "frame": "/Users/saurabhdixit/Anti-Clips/live-clipper/clips/thumbnails/frames/2026-11-18_20-12-54_Ishowspeed-Bam-bam-bam-bam-bam-map/frame_15s.jpg",
            "badge": "🚨 100% SCRIPTED?!",
            "line1": "BRO'S AIM WAS",
            "line2": "HACKED?! 💀",
            "arrow": ((520, 420), (880, 400))
        },
        {
            "name": "2026-11-18_20-14-47_Ishowspeed-If-i-can-smell-your-knockoff",
            "frame": "/Users/saurabhdixit/Anti-Clips/live-clipper/clips/thumbnails/frames/2026-11-18_20-14-47_Ishowspeed-If-i-can-smell-your-knockoff/frame_25s.jpg",
            "badge": "💀 INSTANT REGRET",
            "line1": "HE REGRETTED",
            "line2": "EVERYTHING! 😭",
            "arrow": ((540, 430), (840, 380))
        },
        {
            "name": "2026-11-18_20-16-35_Ishowspeed-Oh-that-was-a-close-call",
            "frame": "/Users/saurabhdixit/Anti-Clips/live-clipper/clips/thumbnails/frames/2026-11-18_20-16-35_Ishowspeed-Oh-that-was-a-close-call/frame_10s.jpg",
            "badge": "😱 CLOSE CALL!",
            "line1": "ALMOST LOST",
            "line2": "IT ALL!! 💀",
            "arrow": ((520, 410), (820, 360))
        },
        {
            "name": "2026-11-18_20-18-24_Ishowspeed-Oh-damn-look-her-elbows-oh",
            "frame": "/Users/saurabhdixit/Anti-Clips/live-clipper/clips/thumbnails/frames/2026-11-18_20-18-24_Ishowspeed-Oh-damn-look-her-elbows-oh/frame_25s.jpg",
            "badge": "🥊 CAUGHT ON STREAM",
            "line1": "WAIT... THEY'RE",
            "line2": "FIGHTING?! 🚨",
            "arrow": ((530, 410), (850, 370))
        }
    ]
    
    for c in clips_config:
        out_16_9 = out_dir / f"{c['name']}_thumb_16x9.jpg"
        out_9_16 = out_dir / f"{c['name']}_thumb_9x16.jpg"
        create_16_9_thumbnail(c["frame"], out_16_9, c["badge"], c["line1"], c["line2"], c.get("arrow"))
        create_vertical_thumbnail(c["frame"], out_9_16, c["badge"], c["line1"], c["line2"])

