import os
import re
import time
import logging
import fitz  # PyMuPDF
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai.errors import APIError

# Initialize logging to console and file
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

log_file = os.path.join(os.path.dirname(__file__), "cloud_worker.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, mode="a", encoding="utf-8")
    ]
)
log = logging.getLogger("cloud_worker")

# Load environment and configurations
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv(r"d:\PY\printosky\.env")
from store_config import get_store_config

try:
    from supabase import create_client
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
    sb = create_client(url, key)
    log.info("Supabase client initialized.")
except Exception as e:
    log.error(f"Failed to initialize Supabase client: {e}")
    sb = None

# Gemini Client
api_key = os.environ.get("GEMINI_API_KEY")
if api_key:
    client = genai.Client(api_key=api_key)
    log.info("Gemini client initialized.")
else:
    client = None
    log.error("GEMINI_API_KEY not found in environment!")

# Configuration defaults
cfg = get_store_config()

def download_pdf_from_storage(filename):
    """Download PDF from storage, supporting unicode/special characters in URLs."""
    import urllib.parse
    import httpx
    
    encoded_filename = urllib.parse.quote(filename)
    public_url = f"{url}/storage/v1/object/public/manuscripts/{encoded_filename}"
    
    try:
        with httpx.Client(http2=True) as client_http:
            resp = client_http.get(public_url)
            if resp.status_code == 200:
                return resp.content
            else:
                log.warning(f"Public URL download returned status {resp.status_code}, falling back to supabase client")
    except Exception as e:
        log.warning(f"Public URL download failed: {e}, falling back to supabase client")
        
    return sb.storage.from_("manuscripts").download(filename)
MY_STORE_ID = cfg.store_id
WATCH_DIR = cfg.hot_folder  # Or use a fallback path like r"D:\Divya teacher\Preeksha sahayi"
TEMP_DIR = r"d:\PY\printosky\_tmp_watcher"

if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

GLOSSARY = u'''
Use this vocabulary glossary of common terms in this manuscript to verify spelling and resolve hard-to-read handwriting:
- "ഹൈസ്കൂൾ" (High School) - often written in flowy cursive.
- "മലയാളം" (Malayalam)
- "പരീക്ഷാസഹായി" (Exam Helper)
- "ഖണ്ഡികയും ചോദ്യോത്തരങ്ങളും" (Paragraph and Question-Answers)
- "മൂന്നിലൊന്നായി സംഗ്രഹിക്കുക" (Summarize into one-third)
- "ആസ്വാദനക്കുറിപ്പ്" (Appreciation note)
- "ഉപന്യാസം" (Essay)
- "പ്രസംഗം" (Speech)
- "താരതമ്യക്കുറിപ്പ്" (Comparison note)
- "വിശദീകരണങ്ങൾ" (Explanations)
- "കവിതാശകലങ്ങളും പഴഞ്ചൊല്ലുകളും" (Poetic lines and proverbs)
- "പ്രതീക്ഷിക്കാവുന്ന മാതൃകാ ചോദ്യങ്ങൾ" (Expected model questions)
- "ദിവ്യനാദം" (Divyanadham)
- "തൃശ്ശൂർ" / "TSR" (Thrissur)
- "വിവേകോദയം" / "VBHSS" (Vivekodayam School)
'''

PROMPT_TEMPLATE = u'''You are an expert Malayalam manuscript transcriber.
Your task is to transcribe the handwritten text in the image line-by-line.

Here is a GLOSSARY of terms known to appear in this manuscript. Use it to verify spelling and resolve hard-to-read handwriting:
"""
%s
"""

Follow these rules:
1. Do not translate the Malayalam text. Transcribe it exactly in Malayalam script.
2. Transcribe any English text in English.
3. Do NOT create a new line for every physical line of text written on the page. Instead, flow the sentences continuously. Only create a new line/paragraph when there is a clear paragraph break (separated by a blank line) or a change in question section. Use full stops (.), question marks (?), exclamation marks (!), and other common elements to determine sentence completion and continue writing on the same line.
4. If a word or character is completely illegible, write '[illegible]' instead of guessing.
5. Output ONLY the transcribed text of the image. Do not include any intro, outro, explanations, or meta-comments.
''' % GLOSSARY

CHILLU_MAP = {
    "\u0d7b": "\u0d23\u0d4d\u200d", # ൺ
    "\u0d7c": "\u0d33\u0d4d\u200d", # ൾ
    "\u0d7d": "\u0d30\u0d4d\u200d", # ർ
    "\u0d7e": "\u0d28\u0d4d\u200d", # ൻ
    "\u0d7f": "\u0d32\u0d4d\u200d", # ൽ
    "\u0d7a": "\u0d23\u0d4d\u200d", # ൺ
}

def replace_chillus(text):
    for chillu, replacement in CHILLU_MAP.items():
        text = text.replace(chillu, replacement)
    return text

def process_transcription_job(job):
    job_id = job["id"]
    filename = job["filename"]
    mode = job.get("mode", "standard")
    
    log.info(f"Starting job {job_id} for file {filename} in {mode.upper()} mode")
    
    # 1. Download PDF from storage
    pdf_temp_path = os.path.join(TEMP_DIR, filename)
    try:
        log.info(f"Downloading {filename} from Supabase Storage...")
        res = download_pdf_from_storage(filename)
        with open(pdf_temp_path, "wb") as f:
            f.write(res)
        log.info("Download completed.")
    except Exception as e:
        log.error(f"Failed to download PDF: {e}")
        sb.table("manuscript_transcripts").update({"status": "failed"}).eq("id", job_id).execute()
        return

    # 2. Open PDF
    try:
        doc = fitz.open(pdf_temp_path)
        total_pages = len(doc)
    except Exception as e:
        log.error(f"Failed to open PDF: {e}")
        sb.table("manuscript_transcripts").update({"status": "failed"}).eq("id", job_id).execute()
        return

    # 3. Transcribe page by page
    # Urgent = gemini-3.5-flash
    # Standard = gemini-3.1-flash-lite
    model_name = "models/gemini-3.5-flash" if mode == "urgent" else "models/gemini-3.1-flash-lite"
    
    # Fetch current state from DB
    current_job = sb.table("manuscript_transcripts").select("*").eq("id", job_id).execute().data[0]
    start_page = current_job.get("transcribed_pages", 0)
    current_content = current_job.get("content") or ""
    
    log.info(f"Resuming/starting transcription from page {start_page + 1} of {total_pages}...")
    
    try:
        for idx in range(start_page, total_pages):
            log.info(f"Transcribing page {idx + 1}/{total_pages}...")
            
            # Extract target page image
            page = doc[idx]
            pix = page.get_pixmap(dpi=150)
            target_img_path = os.path.join(TEMP_DIR, f"temp_page_{idx}.png")
            pix.save(target_img_path)
            
            img_target = Image.open(target_img_path)
            img_target.thumbnail((768, 768))
            
            success = False
            retries = 3
            backoff = 10
            text = ""
            
            while not success and retries > 0:
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[img_target, PROMPT_TEMPLATE]
                    )
                    text = response.text
                    success = True
                except APIError as e:
                    if e.code == 429:
                        log.warning(f"Rate limit hit. Retrying in {backoff}s...")
                        time.sleep(backoff)
                        backoff *= 2
                        retries -= 1
                    else:
                        log.error(f"API Error: {e}")
                        retries -= 1
                except Exception as e:
                    log.error(f"Unexpected error: {e}")
                    retries -= 1
            
            if not success:
                log.error(f"Failed to transcribe page {idx + 1} after retries. Saving placeholder.")
                text = "[safety blocked / illegible]"
            
            # Format and save updates back to DB
            text = text.strip()
            page_header = f"\n\n=== PAGE {idx + 1} ===\n\n"
            current_content += page_header + text
            
            # Push live update to Supabase
            sb.table("manuscript_transcripts").update({
                "transcribed_pages": idx + 1,
                "content": current_content,
                "total_pages": total_pages
            }).eq("id", job_id).execute()
            
            # Cleanup temp page image
            try:
                os.remove(target_img_path)
            except:
                pass
                
            time.sleep(4)  # Anti rate-limit delay
            
        # Complete job
        sb.table("manuscript_transcripts").update({"status": "completed"}).eq("id", job_id).execute()
        log.info(f"Job {job_id} successfully completed.")
        
    except Exception as e:
        log.error(f"Error during transcription loop: {e}")
        sb.table("manuscript_transcripts").update({"status": "failed"}).eq("id", job_id).execute()
    finally:
        doc.close()
        try:
            os.remove(pdf_temp_path)
        except:
            pass

def split_malayalam_english(text):
    # Regex to find blocks of Malayalam characters (Unicode block 0D00-0D7F and ZWJ 200D)
    pattern = re.compile(r"([\u0d00-\u0d7f\u200d]+)")
    parts = pattern.split(text)
    
    segments = []
    for part in parts:
        if not part:
            continue
        is_mal = any(("\u0d00" <= char <= "\u0d7f") or (char == "\u200d") for char in part)
        segments.append((part, is_mal))
    return segments

def sync_completed_jobs():
    # Sync files that were completed and belong to this store
    try:
        res = sb.table("manuscript_transcripts").select("*").eq("status", "completed").eq("uploaded_by_store", MY_STORE_ID).execute()
        jobs = res.data or []
        
        for job in jobs:
            filename = job["filename"]
            base_name = os.path.splitext(filename)[0]
            local_txt_path = os.path.join(WATCH_DIR, f"{base_name}_transcript.txt")
            local_docx_path = os.path.join(WATCH_DIR, f"{base_name}_transcript.docx")
            local_pdf_path = os.path.join(WATCH_DIR, filename)
            
            # 1. Download/Write the completed transcript locally
            if not os.path.exists(local_txt_path):
                log.info(f"Downloading finished transcript locally: {local_txt_path}")
                with open(local_txt_path, "w", encoding="utf-8") as f:
                    f.write(job["content"])
                    
            # 2. Generate and write the Word Docx locally
            if not os.path.exists(local_docx_path):
                log.info(f"Generating Word document locally: {local_docx_path}")
                try:
                    import docx
                    from docx.oxml import OxmlElement
                    from docx.oxml.ns import qn
                    from docx.shared import Pt
                    
                    doc = docx.Document()
                    lines = job["content"].split("\n")
                    first_page = True
                    p = None
                    
                    for line in lines:
                        line_strip = line.strip()
                        if not line_strip:
                            p = doc.add_paragraph()
                            continue
                            
                        if line_strip.startswith("==="):
                            if not first_page:
                                doc.add_page_break()
                            else:
                                first_page = False
                            p = doc.add_paragraph()
                            run = p.add_run(line_strip)
                            run.bold = True
                            continue
                            
                        p = doc.add_paragraph()
                        processed_line = replace_chillus(line_strip)
                        segments = split_malayalam_english(processed_line)
                        
                        for part_text, is_mal in segments:
                            run = p.add_run(part_text)
                            rPr = run._r.get_or_add_rPr()
                            rFonts = OxmlElement('w:rFonts')
                            
                            if is_mal:
                                rFonts.set(qn('w:ascii'), 'AnjaliOldLipi')
                                rFonts.set(qn('w:hAnsi'), 'AnjaliOldLipi')
                                rFonts.set(qn('w:cs'), 'AnjaliOldLipi')
                                rPr.append(rFonts)
                                
                                sz = OxmlElement('w:sz')
                                sz.set(qn('w:val'), '26') # 13 pt
                                rPr.append(sz)
                                
                                szCs = OxmlElement('w:szCs')
                                szCs.set(qn('w:val'), '26') # 13 pt
                                rPr.append(szCs)
                            else:
                                rFonts.set(qn('w:ascii'), 'Times New Roman')
                                rFonts.set(qn('w:hAnsi'), 'Times New Roman')
                                rFonts.set(qn('w:cs'), 'Times New Roman')
                                rPr.append(rFonts)
                                
                                sz = OxmlElement('w:sz')
                                sz.set(qn('w:val'), '22') # 11 pt
                                rPr.append(sz)
                                
                                szCs = OxmlElement('w:szCs')
                                szCs.set(qn('w:val'), '22') # 11 pt
                                rPr.append(szCs)
                                
                    doc.save(local_docx_path)
                    log.info("Word document saved successfully.")
                except Exception as ex:
                    log.error(f"Failed to generate Word document during sync: {ex}")
                    
            # 3. Download the original PDF locally if missing
            if not os.path.exists(local_pdf_path):
                log.info(f"Downloading completed PDF locally: {local_pdf_path}")
                try:
                    pdf_bytes = download_pdf_from_storage(filename)
                    with open(local_pdf_path, "wb") as f:
                        f.write(pdf_bytes)
                except Exception as e:
                    log.error(f"Failed to sync completed PDF: {e}")
    except Exception as e:
        log.error(f"Error syncing completed jobs: {e}")

def main_loop():
    log.info(f"Starting Printosky Cloud Worker for Store ID: {MY_STORE_ID}")
    
    while True:
        try:
            # 1. Check for pending jobs to execute
            res = sb.table("manuscript_transcripts").select("*").eq("status", "pending").limit(1).execute()
            if res.data:
                job = res.data[0]
                # Claim the job to avoid race conditions
                claim = sb.table("manuscript_transcripts").update({"status": "transcribing"}).eq("id", job["id"]).eq("status", "pending").execute()
                if claim.data:
                    process_transcription_job(job)
                    
            # 2. Check for transcribing jobs that might have crashed/interrupted
            res = sb.table("manuscript_transcripts").select("*").eq("status", "transcribing").limit(1).execute()
            if res.data:
                job = res.data[0]
                # If we are the worker that crashed, or we want to resume
                # For simplicity, we can resume any job that is currently transcribing
                # Let's claim it by updating the timestamp or just continue processing it
                process_transcription_job(job)
                
            # 3. Synchronize completed jobs to this store's folder
            sync_completed_jobs()
            
        except Exception as e:
            log.error(f"Error in main worker loop: {e}")
            
        time.sleep(10)

if __name__ == "__main__":
    if sb and client:
        main_loop()
    else:
        log.critical("Missing configurations or credentials. Cloud worker cannot start.")
