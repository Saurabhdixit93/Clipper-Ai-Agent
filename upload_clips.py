#!/usr/bin/env python3
"""
Single-command uploader for all prepared clips in Anti-Clips.
Features:
- Uploads video with resumable chunks.
- Waits for YouTube rendering & transcoding buffer.
- Attaches custom viral thumbnail.
- Publishes video to YouTube.
- AUTOMATIC CLEANUP: Removes uploaded .mp4 clip, .txt transcript, and generated thumbnails.
- Implements 5-7 minute publication gaps between sequential uploads.
"""

import os
import sys
import time
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("upload_clips")

WORKSPACE_DIR = Path(__file__).resolve().parent
TOKEN_FILE = WORKSPACE_DIR / "youtube_token.json"
CLIPS_DIR = WORKSPACE_DIR / "live-clipper" / "clips"
METADATA_FILE = CLIPS_DIR / "upload_metadata.json"
HISTORY_FILE = CLIPS_DIR / "upload_history.json"

def get_authenticated_service():
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(f"OAuth token file not found at: {TOKEN_FILE}")
        
    with open(TOKEN_FILE, "r") as f:
        token_data = json.load(f)
        
    creds = Credentials.from_authorized_user_info(token_data)
    
    if creds.expired or not creds.valid:
        logger.info("Refreshing YouTube credentials...")
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        logger.info("Credentials refreshed and saved.")
        
    youtube = build("youtube", "v3", credentials=creds)
    return youtube

def upload_video_file(youtube, clip_info, privacy_status="private", publish_at=None):
    video_path = Path(clip_info["video_path"])
    if not video_path.exists():
        raise FileNotFoundError(f"Video file missing: {video_path}")
        
    status_dict = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": False
    }
    if publish_at:
        status_dict["publishAt"] = publish_at
        status_dict["privacyStatus"] = "private"

    body = {
        "snippet": {
            "title": clip_info["title"],
            "description": clip_info["description"],
            "tags": clip_info.get("tags", []),
            "categoryId": str(clip_info.get("category_id", "24")),
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en"
        },
        "status": status_dict
    }
    
    logger.info(f"Initiating upload for: {video_path.name}")
    logger.info(f"Title: {clip_info['title']}")
    if publish_at:
        logger.info(f"Scheduled publish time: {publish_at}")
    
    media = MediaFileUpload(
        str(video_path),
        chunksize=1024 * 1024 * 4,
        resumable=True,
        mimetype="video/mp4"
    )
    
    insert_request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )
    
    response = None
    last_pct = -1
    while response is None:
        status, response = insert_request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct != last_pct and pct % 25 == 0:
                logger.info(f"Upload progress: {pct}%")
                last_pct = pct
                
    video_id = response.get("id")
    logger.info(f"Upload completed! Video ID: {video_id}")
    return video_id

def wait_for_video_render(youtube, video_id, max_wait_sec=60):
    logger.info(f"Waiting for video {video_id} transcoding/render buffer...")
    start_time = time.time()
    while time.time() - start_time < max_wait_sec:
        elapsed = int(time.time() - start_time)
        try:
            res = youtube.videos().list(part="status,processingDetails", id=video_id).execute()
            items = res.get("items", [])
            if items:
                proc_status = items[0].get("processingDetails", {}).get("processingStatus")
                upload_status = items[0].get("status", {}).get("uploadStatus")
                if proc_status == "succeeded" or upload_status == "processed":
                    logger.info(f"Video {video_id} rendering verified complete in {elapsed}s!")
                    return True
        except HttpError:
            pass
            
        time.sleep(10)
        if time.time() - start_time >= 35:
            logger.info(f"Render transcode buffer satisfied ({int(time.time() - start_time)}s elapsed).")
            return True
            
    return True

def set_custom_thumbnail(youtube, video_id, thumbnail_path, max_retries=3):
    thumb_path = Path(thumbnail_path)
    if not thumb_path.exists():
        logger.warning(f"Thumbnail not found at {thumb_path}, skipping.")
        return False
        
    logger.info(f"Setting custom thumbnail from {thumb_path.name}...")
    media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg", resumable=True)
    
    for attempt in range(1, max_retries + 1):
        try:
            req = youtube.thumbnails().set(videoId=video_id, media_body=media)
            res = req.execute()
            logger.info(f"Custom thumbnail attached successfully!")
            return True
        except HttpError as e:
            if "uploadDisabled" in str(e) or "The caller does not have permission" in str(e):
                logger.warning("Note: Custom thumbnail requires phone verification on channel.")
                return False
            time.sleep(5)
        except Exception:
            time.sleep(5)
            
    return False

def publish_video(youtube, video_id):
    try:
        req = youtube.videos().update(
            part="status",
            body={
                "id": video_id,
                "status": {
                    "privacyStatus": "public"
                }
            }
        )
        req.execute()
        logger.info(f"Video is now LIVE and PUBLIC: https://youtu.be/{video_id}")
        return True
    except HttpError:
        return False

def remove_uploaded_files(clip_info):
    """Clean up uploaded mp4, txt transcript, and thumbnails."""
    files_to_remove = [
        Path(clip_info.get("video_path", "")),
        Path(clip_info.get("video_path", "")).with_suffix(".txt"),
        Path(clip_info.get("thumbnail_16x9", "")),
        Path(clip_info.get("thumbnail_9x16", "")),
    ]
    for p in files_to_remove:
        try:
            if p.exists() and p.is_file():
                p.unlink()
                logger.info(f"Cleaned up file: {p.name}")
        except Exception as e:
            logger.warning(f"Could not delete {p}: {e}")

def countdown_gap(minutes):
    total_seconds = int(minutes * 60)
    logger.info(f"Starting {minutes}-minute publication gap before next clip ({total_seconds}s total)...")
    start = time.time()
    while True:
        elapsed = time.time() - start
        remaining = total_seconds - elapsed
        if remaining <= 0:
            break
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        if int(remaining) % 30 == 0 or remaining <= 10:
            logger.info(f"Next upload in: {mins:02d}m {secs:02d}s...")
        time.sleep(1)

def run_upload(gap_minutes=6.0, dry_run=False, keep_files=False):
    if not METADATA_FILE.exists():
        logger.info(f"No metadata file found at {METADATA_FILE}. Run ./process_clips.sh first.")
        return
        
    with open(METADATA_FILE, "r") as f:
        data = json.load(f)
        
    clips = data.get("clips", [])
    if not clips:
        logger.info("No clips pending upload in metadata.")
        return
        
    logger.info(f"Found {len(clips)} clip(s) ready for upload. Gap: {gap_minutes} mins.")
    
    if dry_run:
        logger.info("DRY RUN: Validating pending clips without uploading...")
        for c in clips:
            print(f"- [Clip #{c.get('id')}] {c.get('title')} (File: {Path(c.get('video_path')).name})")
        return

    youtube = get_authenticated_service()
    
    uploaded_history = []
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE, "r") as hf:
                uploaded_history = json.load(hf)
        except Exception:
            pass

    base_time = datetime.now(timezone.utc)
    remaining_clips = list(clips)
    
    total = len(clips)
    for idx, clip in enumerate(clips, 1):
        print("\n" + "="*70)
        logger.info(f"UPLOADING CLIP {idx}/{total}: {clip['title']}")
        print("="*70)
        
        # Staggered publishAt time
        sched_time = base_time + timedelta(minutes=2 + (idx - 1) * gap_minutes)
        sched_iso = sched_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        
        try:
            video_id = upload_video_file(youtube, clip, privacy_status="private", publish_at=sched_iso)
            wait_for_video_render(youtube, video_id, max_wait_sec=60)
            set_custom_thumbnail(youtube, video_id, clip["thumbnail_16x9"])
            publish_video(youtube, video_id)
            
            record = {
                "id": clip.get("id"),
                "video_id": video_id,
                "url": f"https://youtu.be/{video_id}",
                "title": clip["title"],
                "scheduled_for": sched_iso,
                "uploaded_at": datetime.now(timezone.utc).isoformat()
            }
            uploaded_history.append(record)
            with open(HISTORY_FILE, "w") as hf:
                json.dump(uploaded_history, hf, indent=2)
                
            logger.info(f"Clip #{clip.get('id')} published successfully! Link: https://youtu.be/{video_id}")
            
            # Clean up uploaded video & thumbnail files
            if not keep_files:
                logger.info("Removing uploaded clip files and thumbnails from disk...")
                remove_uploaded_files(clip)
                
            # Remove from pending queue
            remaining_clips = [c for c in remaining_clips if c.get("id") != clip.get("id")]
            with open(METADATA_FILE, "w") as mf:
                json.dump({"clips": remaining_clips}, mf, indent=2)

        except Exception as e:
            logger.error(f"Error processing clip #{clip.get('id')}: {e}")
            
        if idx < total:
            countdown_gap(gap_minutes)

    print("\n" + "="*70)
    logger.info("ALL PENDING CLIPS PROCESSED AND UPLOADED!")
    print("="*70)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload all prepared clips with thumbnail and staggered publication gaps.")
    parser.add_argument("--gap-minutes", type=float, default=6.0, help="Gap between uploads in minutes (default: 6.0)")
    parser.add_argument("--dry-run", action="store_true", help="Preview pending uploads without uploading")
    parser.add_argument("--keep-files", action="store_true", help="Keep local clip and thumbnail files after upload")
    args = parser.parse_args()
    
    run_upload(gap_minutes=args.gap_minutes, dry_run=args.dry_run, keep_files=args.keep_files)

