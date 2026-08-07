# -*- coding: utf-8 -*-
"""
sync_local_to_supabase.py — Synchronizes all existing local manuscripts and transcripts
from D:\\Divya teacher\\Preeksha sahayi to Supabase storage and the database table.
"""

import os
import sys
import logging
from pathlib import Path
from dotenv import load_dotenv
import fitz  # PyMuPDF

# Ensure UTF-8 console output
sys.stdout.reconfigure(encoding="utf-8")

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("sync_transcripts")

# Load environment variables
_here = Path(__file__).parent
sys.path.insert(0, str(_here.parent))
load_dotenv(str(_here.parent / ".env"), override=True)

from store_config import get_store_config

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
cfg = get_store_config()
import re
import time

def sanitize_filename(filename):
    base, ext = os.path.splitext(filename)
    # Replace non-ASCII and special characters with underscore
    clean_base = re.sub(r'[^\w\.\-]', '_', base)
    # Collapse multiple underscores
    clean_base = re.sub(r'_+', '_', clean_base).strip('_')
    if not clean_base:
        clean_base = f"manuscript_{int(time.time())}"
    return clean_base + ext

store_id = cfg.store_id

def sync():
    if not os.path.exists(WATCH_DIR):
        log.error(f"Watch directory {WATCH_DIR} not found.")
        return

    log.info(f"Scanning directory: {WATCH_DIR} for store ID: {store_id}...")
    files = os.listdir(WATCH_DIR)
    
    for f in files:
        if f.lower().endswith(".pdf"):
            sanitized_f = sanitize_filename(f)
            pdf_path = os.path.join(WATCH_DIR, f)
            base_name = os.path.splitext(f)[0]
            txt_path = os.path.join(WATCH_DIR, f"{base_name}_transcript.txt")
            
            if sanitized_f != f:
                log.info(f"Renaming local files to sanitize: '{f}' -> '{sanitized_f}'")
                new_pdf_path = os.path.join(WATCH_DIR, sanitized_f)
                new_base_name = os.path.splitext(sanitized_f)[0]
                new_txt_path = os.path.join(WATCH_DIR, f"{new_base_name}_transcript.txt")
                
                try:
                    os.rename(pdf_path, new_pdf_path)
                    pdf_path = new_pdf_path
                    f = sanitized_f
                    base_name = new_base_name
                    
                    if os.path.exists(txt_path):
                        os.rename(txt_path, new_txt_path)
                        txt_path = new_txt_path
                except Exception as rename_err:
                    log.error(f"Failed to rename local files: {rename_err}")
                    continue
                    
            log.info(f"Processing: {f}")
            
            # Get pages
            try:
                doc = fitz.open(pdf_path)
                pages = len(doc)
                doc.close()
            except Exception as e:
                log.error(f"Failed to read PDF {f}: {e}")
                continue

            # Read local transcript content
            content = ""
            transcribed_pages = 0
            if os.path.exists(txt_path):
                try:
                    with open(txt_path, "r", encoding="utf-8") as tf:
                        content = tf.read()
                    transcribed_pages = pages
                except Exception as e:
                    log.error(f"Failed to read transcript for {f}: {e}")

            # Check if exists in Supabase
            try:
                res = sb.table("manuscript_transcripts").select("*").eq("filename", f).execute()
                db_rows = res.data or []
            except Exception as e:
                log.error(f"Failed to query database for {f}: {e}")
                continue

            status = "completed" if transcribed_pages > 0 else "pending"

            if db_rows:
                db_row = db_rows[0]
                # Update content if local transcript exists but Supabase does not have it yet
                if not db_row.get("content") and content:
                    log.info(f"Updating content for {f} in Supabase...")
                    try:
                        sb.table("manuscript_transcripts").update({
                            "status": "completed",
                            "content": content,
                            "transcribed_pages": transcribed_pages
                        }).eq("id", db_row["id"]).execute()
                    except Exception as e:
                        log.error(f"Failed to update database record for {f}: {e}")
                else:
                    log.info(f"Manuscript {f} already synchronized in Supabase.")
            else:
                # Upload PDF file to Supabase storage bucket
                log.info(f"Uploading PDF {f} to Supabase storage...")
                try:
                    with open(pdf_path, "rb") as pdf_file:
                        sb.storage.from_("manuscripts").upload(
                            path=f,
                            file=pdf_file,
                            file_options={"x-upsert": "true", "content-type": "application/pdf"}
                        )
                except Exception as e:
                    log.warning(f"Storage upload warning for {f}: {e}")

                # Insert metadata record into manuscript_transcripts
                log.info(f"Inserting {f} metadata record into Supabase...")
                try:
                    sb.table("manuscript_transcripts").insert({
                        "filename": f,
                        "pdf_url": f"{url}/storage/v1/object/public/manuscripts/{f}",
                        "total_pages": pages,
                        "transcribed_pages": transcribed_pages,
                        "status": status,
                        "content": content,
                        "uploaded_by_store": store_id,
                        "mode": "standard"
                    }).execute()
                    log.info(f"Successfully synchronized {f} to Supabase.")
                except Exception as e:
                    log.error(f"Failed to insert database record for {f}: {e}")

if __name__ == "__main__":
    sync()
