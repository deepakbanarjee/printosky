# -*- coding: utf-8 -*-
"""
fix_old_transcripts.py — Fixes historical manuscript database rows and local filenames.
Sanitizes filenames (removes spaces, emojis, special characters), renames local files,
updates Supabase database records, and uploads missing PDFs to the manuscripts bucket.
"""

import os
import sys
import re
import time
import logging
from pathlib import Path
from dotenv import load_dotenv

# Ensure UTF-8 output encoding for console
sys.stdout.reconfigure(encoding="utf-8")

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("fix_transcripts")

_here = Path(__file__).parent
sys.path.insert(0, str(_here.parent))
load_dotenv(str(_here.parent / ".env"), override=True)

try:
    from supabase import create_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
    sb = create_client(url, key)
    log.info("Supabase client initialized.")
except Exception as e:
    log.error(f"Failed to initialize Supabase client: {e}")
    sys.exit(1)

WATCH_DIR = r"D:\Divya teacher\Preeksha sahayi"

def sanitize_filename(filename):
    base, ext = os.path.splitext(filename)
    # Replace non-ASCII-alphanumeric, non-dot, non-hyphen with underscore
    clean_base = re.sub(r'[^a-zA-Z0-9\.\-]', '_', base)
    # Collapse multiple underscores
    clean_base = re.sub(r'_+', '_', clean_base).strip('_')
    if not clean_base:
        clean_base = f"manuscript_{int(time.time())}"
    return clean_base + ext

def run_fix():
    if not os.path.exists(WATCH_DIR):
        log.error(f"Watch directory {WATCH_DIR} not found.")
        return

    log.info("Fetching all manuscript records from Supabase...")
    try:
        res = sb.table("manuscript_transcripts").select("*").execute()
        rows = res.data or []
    except Exception as e:
        log.error(f"Failed to fetch records: {e}")
        return

    log.info(f"Found {len(rows)} records. Processing...")
    
    for row in rows:
        old_name = row["filename"]
        new_name = sanitize_filename(old_name)
        
        if old_name == new_name:
            continue
            
        log.info(f"--- Fixing record: '{old_name}' -> '{new_name}' ---")
        
        # 1. Check if the sanitized name already exists in the database
        try:
            exists_res = sb.table("manuscript_transcripts").select("id").eq("filename", new_name).execute()
            exists = exists_res.data or []
        except Exception as e:
            log.error(f"Failed to check duplicate for {new_name}: {e}")
            continue

        row_id = row["id"]
        
        if exists:
            # Duplicate exists, delete this old unsanitized row to clean up
            log.info(f"Sanitized record already exists. Deleting duplicate unsanitized row '{old_name}'...")
            try:
                sb.table("manuscript_transcripts").delete().eq("id", row_id).execute()
            except Exception as e:
                log.error(f"Failed to delete duplicate row {row_id}: {e}")
        else:
            # Update the filename and pdf_url in Supabase
            new_pdf_url = f"{url}/storage/v1/object/public/manuscripts/{new_name}"
            log.info(f"Updating database row {row_id} with sanitized filename...")
            try:
                sb.table("manuscript_transcripts").update({
                    "filename": new_name,
                    "pdf_url": new_pdf_url
                }).eq("id", row_id).execute()
            except Exception as e:
                log.error(f"Failed to update database row: {e}")
                continue

        # 2. Rename local files if they exist
        old_pdf_path = os.path.join(WATCH_DIR, old_name)
        new_pdf_path = os.path.join(WATCH_DIR, new_name)
        
        old_base = os.path.splitext(old_name)[0]
        new_base = os.path.splitext(new_name)[0]
        
        old_txt_path = os.path.join(WATCH_DIR, f"{old_base}_transcript.txt")
        new_txt_path = os.path.join(WATCH_DIR, f"{new_base}_transcript.txt")
        
        # Rename PDF
        if os.path.exists(old_pdf_path):
            log.info(f"Renaming local PDF: '{old_name}' -> '{new_name}'")
            try:
                os.rename(old_pdf_path, new_pdf_path)
            except Exception as e:
                log.error(f"Failed to rename local PDF: {e}")
                
        # Rename Transcript Text
        if os.path.exists(old_txt_path):
            log.info(f"Renaming local transcript: '{old_base}_transcript.txt' -> '{new_base}_transcript.txt'")
            try:
                os.rename(old_txt_path, new_txt_path)
            except Exception as e:
                log.error(f"Failed to rename local transcript: {e}")

        # 3. Upload the PDF to storage under the new sanitized name if it exists locally
        active_pdf_path = new_pdf_path if os.path.exists(new_pdf_path) else old_pdf_path
        if os.path.exists(active_pdf_path):
            log.info(f"Uploading sanitized PDF to storage as '{new_name}'...")
            try:
                with open(active_pdf_path, "rb") as f_obj:
                    sb.storage.from_("manuscripts").upload(
                        path=new_name,
                        file=f_obj,
                        file_options={"x-upsert": "true", "content-type": "application/pdf"}
                    )
                log.info("Storage upload successful.")
            except Exception as e:
                log.error(f"Failed to upload sanitized PDF: {e}")

    log.info("Fix run completed successfully.")

if __name__ == "__main__":
    run_fix()
