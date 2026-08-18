import os
import sys
import time
import math
import logging
import argparse
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("reanalyze")

project_root = r"d:\PY\printosky"
if project_root not in sys.path:
    sys.path.insert(0, project_root)

load_dotenv(os.path.join(project_root, ".env"))

from supabase import create_client
url = os.environ["SUPABASE_URL"]
key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
sb = create_client(url, key)

from google import genai
from google.genai import types
from PIL import Image
import fitz

api_key = os.environ.get("GEMINI_API_KEY")
if not api_key:
    log.error("GEMINI_API_KEY missing in environment!")
    sys.exit(1)

client = genai.Client(api_key=api_key)

def reanalyze_file(filename_query):
    log.info(f"Querying Supabase for file matching '{filename_query}'...")
    res = sb.table("manuscript_transcripts").select("id,filename,total_pages,content,mode").ilike("filename", f"%{filename_query}%").execute()
    
    if not res.data:
        log.error(f"No manuscript record found in Supabase matching '{filename_query}'")
        return

    job = res.data[0]
    job_id = job["id"]
    filename = job["filename"]
    mode = job.get("mode", "standard")
    
    log.info(f"Found record: {filename} (ID: {job_id}) — Mode: {mode}")

    # Download PDF
    import urllib.parse
    import httpx
    encoded = urllib.parse.quote(filename)
    public_url = f"{url}/storage/v1/object/public/manuscripts/{encoded}"
    
    temp_pdf = os.path.join(project_root, "_tmp_watcher", f"reanalyze_{filename}")
    os.makedirs(os.path.dirname(temp_pdf), exist_ok=True)
    
    log.info(f"Downloading PDF scan for {filename}...")
    try:
        with httpx.Client(http2=True) as http_c:
            r = http_c.get(public_url)
            if r.status_code == 200:
                with open(temp_pdf, "wb") as f:
                    f.write(r.content)
            else:
                pdf_bytes = sb.storage.from_("manuscripts").download(filename)
                with open(temp_pdf, "wb") as f:
                    f.write(pdf_bytes)
    except Exception as e:
        log.error(f"Download failed: {e}")
        return

    doc = fitz.open(temp_pdf)
    total_pages = len(doc)
    log.info(f"PDF opened successfully: {total_pages} total pages.")

    model_name = "models/gemini-3.5-flash" if mode == "urgent" else "models/gemini-3.1-flash-lite"
    
    from tools.cloud_transcription_worker import PROMPT_TEMPLATE

    full_content = ""

    for idx in range(total_pages):
        log.info(f"Re-analyzing page {idx + 1}/{total_pages}...")
        page = doc[idx]
        pix = page.get_pixmap(dpi=150)
        page_img_path = os.path.join(project_root, "_tmp_watcher", f"re_page_{idx}.png")
        pix.save(page_img_path)

        img_target = Image.open(page_img_path)
        img_target.thumbnail((768, 768))

        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[img_target, PROMPT_TEMPLATE],
                config=types.GenerateContentConfig(response_logprobs=True)
            )
            raw_text = (response.text or "").strip()
            
            page_words = []
            cand = response.candidates[0]
            logprob_result = getattr(cand, "logprobs_result", None)
            if logprob_result and hasattr(logprob_result, "chosen_candidates"):
                curr_word = ""
                curr_probs = []
                for token_info in logprob_result.chosen_candidates:
                    tok = getattr(token_info, "token", "")
                    lp = getattr(token_info, "logprob", 0.0)
                    p = math.exp(lp) * 100.0 if lp <= 0 else 99.0
                    if tok.startswith(" ") or tok.startswith("\n") or not curr_word:
                        if curr_word.strip():
                            avg_p = sum(curr_probs) / len(curr_probs) if curr_probs else 95.0
                            page_words.append({
                                "word": curr_word.strip(),
                                "confidence": round(avg_p, 1),
                                "flagged": avg_p < 75.0,
                                "page": idx + 1
                            })
                        curr_word = tok
                        curr_probs = [p]
                    else:
                        curr_word += tok
                        curr_probs.append(p)
                if curr_word.strip():
                    avg_p = sum(curr_probs) / len(curr_probs) if curr_probs else 95.0
                    page_words.append({
                        "word": curr_word.strip(),
                        "confidence": round(avg_p, 1),
                        "flagged": avg_p < 75.0,
                        "page": idx + 1
                    })

            # Embed inline [low:pct%] tags into text for flagged words
            page_text = raw_text
            for item in page_words:
                if item["flagged"]:
                    w = item["word"]
                    if w in page_text and "[low:" not in page_text:
                        page_text = page_text.replace(w, f"[low:{int(item['confidence'])}%]{w}[/low]", 1)

            page_header = f"\n\n=== PAGE {idx + 1} ===\n\n"
            full_content += page_header + page_text

            log.info(f"Page {idx + 1} complete. {len([w for w in page_words if w['flagged']])} low confidence word(s) flagged.")

        except Exception as e:
            log.error(f"Error on page {idx + 1}: {e}")
        finally:
            try:
                os.remove(page_img_path)
            except Exception:
                pass

    # Save to Supabase
    log.info(f"Updating Supabase record for {filename}...")
    sb.table("manuscript_transcripts").update({
        "content": full_content.strip()
    }).eq("id", job_id).execute()

    log.info(f"SUCCESS! {filename} re-analyzed with confidence scores and inline tags.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-analyze existing manuscript files for confidence scoring.")
    parser.add_argument("--filename", default="BIO-VISION", help="Filename or query string matching file in Supabase")
    args = parser.parse_args()
    reanalyze_file(args.filename)
