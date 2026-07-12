# -*- coding: utf-8 -*-
"""
Transcribe a handwritten Malayalam PDF document page-by-page using Gemini 3.1 Pro.
Uses visual few-shot prompting and a contextual glossary for maximum accuracy.
Supports resuming from the last transcribed page.
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

PDF_PATH = r"C:\Users\user\Downloads\🌺ഹൈസ്കൂൾ മലയാളം പരീക്ഷാസഹായി By DM🌺.pdf.pdf"
OUT_PATH = r"C:\Users\user\Downloads\exam_helper_transcript.txt"
TEMP_DIR = r"d:\PY\printosky\_tmp_pages"

# Reference page index for few-shot learning (Page 9 is index 9)
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

def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        print("ERROR: GEMINI_API_KEY is missing or empty in .env.")
        sys.exit(1)

    if not os.path.exists(PDF_PATH):
        print(f"ERROR: PDF file not found at: {PDF_PATH}")
        sys.exit(1)

    os.makedirs(TEMP_DIR, exist_ok=True)

    print("Opening PDF...")
    doc = fitz.open(PDF_PATH)
    total_pages = len(doc)
    print(f"Total pages: {total_pages}")

    # Generate reference image if not already done
    ref_img_path = os.path.join(TEMP_DIR, f"ref_page_{REF_PAGE_IDX}.png")
    if not os.path.exists(ref_img_path):
        print(f"Extracting reference page {REF_PAGE_IDX + 1}...")
        ref_page = doc[REF_PAGE_IDX]
        ref_pix = ref_page.get_pixmap(dpi=150)
        ref_pix.save(ref_img_path)
    
    img_ref = Image.open(ref_img_path)

    last_done = get_last_transcribed_page(OUT_PATH)
    start_page = last_done + 1
    print(f"Resuming from page {start_page + 1} (index {start_page})")

    client = genai.Client(api_key=api_key)

    mode = "a" if start_page > 0 else "w"
    with open(OUT_PATH, mode, encoding="utf-8") as out_file:
        for idx in range(start_page, total_pages):
            print(f"\n--- Processing Page {idx + 1} of {total_pages} ---", flush=True)
            
            # If it's the reference page itself, write the reference text directly
            if idx == REF_PAGE_IDX:
                out_file.write(f"\n\n=== PAGE {idx} ===\n\n")
                out_file.write(REF_TEXT.strip())
                out_file.write("\n")
                out_file.flush()
                print(f"Page {idx + 1} (Reference Page) written from memory.")
                continue

            # Extract page as image
            page = doc[idx]
            pix = page.get_pixmap(dpi=150)
            img_path = os.path.join(TEMP_DIR, f"page_{idx}.png")
            pix.save(img_path)
            
            # Load as PIL Image
            img_target = Image.open(img_path)

            # Call Gemini with retry logic
            success = False
            retries = 5
            backoff = 10
            
            while not success and retries > 0:
                try:
                    response = client.models.generate_content(
                        model="models/gemini-3.1-pro-preview",
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
                print(f"CRITICAL: Failed to transcribe page {idx + 1} after multiple retries. Exiting.")
                sys.exit(1)

            text = text.strip()
            
            # Write to output file
            out_file.write(f"\n\n=== PAGE {idx} ===\n\n")
            out_file.write(text)
            out_file.write("\n")
            out_file.flush()

            print(f"Page {idx + 1} transcribed successfully. Length: {len(text)} characters.")
            
            # Clean up temp image
            try:
                os.remove(img_path)
            except:
                pass
            
            # Delay to avoid hitting rate limits
            time.sleep(5)

    print(f"\nSUCCESS: Full transcription saved to: {OUT_PATH}")

if __name__ == "__main__":
    main()
