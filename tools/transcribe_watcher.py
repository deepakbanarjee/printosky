# -*- coding: utf-8 -*-
"""
Manuscript Transcription Watcher Service.
Monitors D:\\Divya teacher\\Preeksha sahayi for new PDF files.
When a new PDF is detected (one without a corresponding _transcript.txt file),
it transcribes it page-by-page using Gemini 3.1 Pro, visual few-shot learning, and a glossary.
Supports resuming if interrupted.
"""
import os
import sys
import time
import re
import fitz  # PyMuPDF
from PIL import Image
from dotenv import load_dotenv
from google import genai
from google.genai.errors import APIError

# Ensure UTF-8 output encoding for console
sys.stdout.reconfigure(encoding="utf-8")

# Load environment variables, overriding any existing ones
load_dotenv("d:/PY/printosky/.env", override=True)

WATCH_DIR = r"D:\Divya teacher\Preeksha sahayi"
TEMP_DIR = r"d:\PY\printosky\_tmp_watcher"
REF_PAGE_IDX = 9

REF_TEXT = u'''(3)
ഖണ്ഡിക 1 - ചോദ്യങ്ങൾ

1- അസമത്വത്തിന്റെ ആഘാതം കൂടുതൽ
ഏറ്റുവാങ്ങുന്നത് സ്ത്രീകളാണ് എന്ന്
പറയാനുള്ള കാരണമെന്ത്? (1)

2- അഭ്യസ്തവിദ്യരുടെ തൊഴിലില്ലായ്മ സ്ത്രീകളെ
എങ്ങനെ ബാധിക്കുന്നു? കരിയർ
ബ്രേക്കിന്റെ കാരണങ്ങളെന്തെല്ലാം? (2)

3- സ്ത്രീകളും സാമൂഹികവികസനവും
തമ്മിൽ ബന്ധപ്പെട്ടിരിക്കുന്നതെങ്ങനെ? (2)

Prepared By DM, TSR'''

GLOSSARY = u'''
Use this vocabulary glossary of common terms in this manuscript to resolve handwriting ambiguities:
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

PROMPT = u'''The first image is a sample page of the same handwritten manuscript.
The exact transcription of the text written in the first image is:
"""
%s
"""

Here is a GLOSSARY of terms known to appear in this manuscript. Use it to verify spelling and resolve hard-to-read handwriting:
"""
%s
"""

Your task is to transcribe the second image (the target image) line-by-line.
Use the first image and its transcription as a reference to understand the author's handwriting style.

Follow these rules:
1. Do not translate the Malayalam text. Transcribe it exactly in Malayalam script.
2. Transcribe any English text in English.
3. Preserve the layout, paragraph breaks, and line breaks as closely as possible.
4. If a word or character is completely illegible, write '[illegible]' instead of guessing.
5. Output ONLY the transcribed text of the second image. Do not include any intro, outro, explanations, or meta-comments.
''' % (REF_TEXT, GLOSSARY)

def find_reference_pdf(folder):
    # Searches for a PDF that has 'By DM' and 'പരീക്ഷാ' in its name to use as a handwriting reference
    for f in os.listdir(folder):
        if 'By DM' in f and 'പരീക്ഷാ' in f and f.lower().endswith('.pdf'):
            return os.path.join(folder, f)
    return None

def get_last_transcribed_page(out_path):
    if not os.path.exists(out_path):
        return -1
    
    last_page = -1
    with open(out_path, "r", encoding="utf-8") as f:
        content = f.read()
        markers = re.findall(r"=== PAGE (\d+) ===", content)
        if markers:
            last_page = max(int(m) for m in markers)
    return last_page

def transcribe_pdf(pdf_path, ref_img_path, client):
    pdf_name = os.path.basename(pdf_path)
    base_name = os.path.splitext(pdf_name)[0]
    out_path = os.path.join(WATCH_DIR, f"{base_name}_transcript.txt")
    
    print(f"\n==================================================")
    print(f"STARTING TRANSCRIPTION: {pdf_name}")
    print(f"Output path: {out_path}")
    print(f"==================================================")
    
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    print(f"Total pages: {total_pages}")
    
    img_ref = Image.open(ref_img_path)
    img_ref.thumbnail((768, 768))
    
    last_done = get_last_transcribed_page(out_path)
    start_page = last_done + 1
    print(f"Resuming from page {start_page + 1} (index {start_page})")
    
    mode = "a" if start_page > 0 else "w"
    with open(out_path, mode, encoding="utf-8") as out_file:
        for idx in range(start_page, total_pages):
            print(f"\n[{base_name}] Page {idx + 1} of {total_pages}...", flush=True)
            
            # Extract page as image
            page = doc[idx]
            pix = page.get_pixmap(dpi=150)
            img_path = os.path.join(TEMP_DIR, f"temp_page_{idx}.png")
            pix.save(img_path)
            
            # Load as PIL Image
            img_target = Image.open(img_path)
            img_target.thumbnail((768, 768))
            
            # Call Gemini with retry logic
            success = False
            retries = 5
            backoff = 10
            
            while not success and retries > 0:
                try:
                    response = client.models.generate_content(
                        model="models/gemini-3.5-flash",
                        contents=[img_ref, img_target, PROMPT]
                    )
                    text = response.text
                    success = True
                except APIError as e:
                    if e.code == 429:
                        print(f"Rate limit hit. Retrying in {backoff}s... (Retries left: {retries})")
                        time.sleep(backoff)
                        backoff *= 2
                        retries -= 1
                    else:
                        print(f"API Error: {e}. Retrying in {backoff}s...")
                        time.sleep(backoff)
                        retries -= 1
                except Exception as e:
                    print(f"Unexpected error: {e}. Retrying in {backoff}s...")
                    time.sleep(backoff)
                    retries -= 1

            if not success:
                print(f"CRITICAL: Failed to transcribe page {idx + 1}. Skipping remaining pages for now.")
                return False

            text = text.strip()
            
            # Write to output file
            out_file.write(f"\n\n=== PAGE {idx} ===\n\n")
            out_file.write(text)
            out_file.write("\n")
            out_file.flush()

            print(f"Page {idx + 1} transcribed successfully. ({len(text)} chars)")
            
            # Clean up temp image
            try:
                os.remove(img_path)
            except:
                pass
            
            time.sleep(5) # rate limit delay
            
    print(f"SUCCESS: Transcription completed for {pdf_name}")
    return True

def is_transcription_complete(pdf_path, transcript_path):
    if not os.path.exists(transcript_path) or os.path.getsize(transcript_path) < 100:
        return False
    try:
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        doc.close()
    except Exception as e:
        print(f"Error checking PDF pages: {e}")
        return False
    
    last_marker = f"=== PAGE {total_pages - 1} ==="
    with open(transcript_path, "r", encoding="utf-8") as f:
        content = f.read()
        return last_marker in content

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        print("ERROR: GEMINI_API_KEY is missing or empty in .env.")
        sys.exit(1)

    os.makedirs(TEMP_DIR, exist_ok=True)
    client = genai.Client(api_key=api_key)

    print("--------------------------------------------------")
    print(f"WATCHER STARTED: Monitoring {WATCH_DIR}")
    print("--------------------------------------------------")

    while True:
        try:
            # 1. Ensure we have a reference PDF to extract handwriting visual sample
            ref_pdf = find_reference_pdf(WATCH_DIR)
            if not ref_pdf:
                print("Warning: Reference PDF (containing 'By DM') not found in watch folder. Waiting...", flush=True)
                time.sleep(10)
                continue
                
            # Extract reference image once
            ref_img_path = os.path.join(TEMP_DIR, "ref_page.png")
            if not os.path.exists(ref_img_path):
                print(f"Extracting visual reference page from {os.path.basename(ref_pdf)}...")
                ref_doc = fitz.open(ref_pdf)
                ref_page = ref_doc[REF_PAGE_IDX]
                ref_pix = ref_page.get_pixmap(dpi=150)
                ref_pix.save(ref_img_path)
                ref_doc.close()

            # 2. Scan watch folder for new PDF files
            for f in os.listdir(WATCH_DIR):
                if f.lower().endswith(".pdf"):
                    pdf_path = os.path.join(WATCH_DIR, f)
                    base_name = os.path.splitext(f)[0]
                    transcript_path = os.path.join(WATCH_DIR, f"{base_name}_transcript.txt")
                    
                    # If transcript is not complete, we transcribe (or resume) it!
                    if not is_transcription_complete(pdf_path, transcript_path):
                        # Transcribe the PDF
                        transcribe_pdf(pdf_path, ref_img_path, client)
                        
        except Exception as e:
            print(f"Watcher loop error: {e}", flush=True)
            
        time.sleep(10) # check folder every 10 seconds

if __name__ == "__main__":
    main()
