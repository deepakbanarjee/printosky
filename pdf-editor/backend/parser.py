import fitz  # PyMuPDF
import base64
import io
from typing import List, Dict, Any, Optional
from PIL import Image
import fonts

try:
    import pytesseract
    _HAS_TESSERACT = True
except ImportError:
    pytesseract = None
    _HAS_TESSERACT = False

# Initialize pytesseract configuration if needed. 
# Attempt to find tesseract in common locations if not in PATH.
# For now, we assume it's in PATH or the user will configure it.

def parse_pdf(file_bytes: bytes, languages: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Parses PDF bytes and returns a hierarchical structure with pages and blocks.
    
    Args:
        file_bytes: PDF file as bytes
        languages: List of ISO language codes for OCR (e.g., ['en', 'hi', 'ta'])
                  If None, uses auto-detection or defaults to common Indic languages
    
    Structure:
    {
      "pages": [
        {
           "index": int,
           "width": float,
           "height": float,
           "blocks": [ ... ],
           "detected_language": str
        }
      ]
    }
    """
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    output = {"pages": []}
    
    for page_index, page in enumerate(doc):
        page_data = {
            "index": page_index,
            "width": page.rect.width,
            "height": page.rect.height,
            "blocks": []
        }
        
        # Try extracting text directly first
        text_blocks = extract_text_blocks(page, page_index)
        
        # If very little text is found, try OCR
        # Heuristic: less than 5 text characters could suggest an image scan
        total_text_len = sum(len(b['content']) for b in text_blocks if b['type'] == 'text')
        
        detected_lang = 'en'
        if total_text_len < 5:
            # Fallback to OCR with multi-language support
            ocr_blocks = perform_ocr(page, page_index, languages)
            page_data["blocks"] = ocr_blocks
            # Try to detect language from OCR text
            if ocr_blocks:
                sample_text = ' '.join([b.get('content', '') for b in ocr_blocks[:3] if b.get('type') == 'text'])
                if sample_text:
                    detected_lang = fonts.detect_language_from_text(sample_text)
        else:
            page_data["blocks"] = text_blocks
            # Detect language from extracted text
            sample_text = ' '.join([b.get('content', '') for b in text_blocks[:3] if b.get('type') == 'text'])
            if sample_text:
                detected_lang = fonts.detect_language_from_text(sample_text)
        
        page_data["detected_language"] = detected_lang
        page_data["detected_script"] = fonts.LANGUAGE_SCRIPT_MAP.get(detected_lang, 'latin')
            
        output["pages"].append(page_data)
                
    return output

def extract_text_blocks(page, page_index) -> List[Dict[str, Any]]:
    """Extracts text blocks using PyMuPDF's get_text('dict')."""
    blocks = page.get_text("dict")["blocks"]
    output_blocks = []
    block_id_counter = 0
    
    for b in blocks:
        block_id = f"p{page_index}_b{block_id_counter}"
        block_id_counter += 1

        if b['type'] == 0: # Text Block
            # We want to aggregate lines but ideally keep them somewhat separate for layout
            # For simplicity in this editor, we'll try to group by 'block' which usually represents a paragraph.
            # However, for accurate "in-place" editing, individual lines might be better, 
            # but blocks are easier to manage for a demo. Let's stick to PyMuPDF blocks.
            
            text_content = ""
            font_sizes = []
            font_names = []
            
            # Helper to flat extraction for the whole block
            for line in b["lines"]:
                for span in line["spans"]:
                    text_content += span["text"] + " "
                    font_sizes.append(span["size"])
                    font_names.append(span["font"])
            
            text_content = text_content.strip()
            if not text_content:
                continue

            avg_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12
            raw_font = max(set(font_names), key=font_names.count) if font_names else "Arial"
            
            # Map raw PDF fonts to web safe fonts
            font_family = "system-ui, -apple-system, sans-serif"
            lower_font = raw_font.lower()
            if "times" in lower_font or "serif" in lower_font:
                font_family = "Times New Roman, serif"
            elif "courier" in lower_font or "mono" in lower_font:
                font_family = "Courier New, monospace"
            elif "arial" in lower_font or "helvetica" in lower_font:
                font_family = "Arial, sans-serif"
            
            # Use the block's bbox
            bbox = b["bbox"] # [x0, y0, x1, y1]
            
            # Detect script from content
            script = fonts.detect_script(text_content)
            
            output_blocks.append({
                "id": block_id,
                "type": "text",
                "content": text_content,
                "bbox": bbox,
                "script": script,
                "style": {
                    "fontSize": avg_size,
                    "fontFamily": font_family,
                    # "color": ... (would need to extract from spans, defaulting for now)
                }
            })

        elif b['type'] == 1: # Image Block
            # We might simply process images as non-editable blocks
            bbox = b["bbox"]
            output_blocks.append({
                "id": block_id,
                "type": "image",
                "bbox": bbox,
                # We don't necessarily need the content if we render the background,
                # but might be useful if we want to move it.
                # For this iteration, we focus on Text editing.
            })
            
    return output_blocks

def perform_ocr(page, page_index, languages: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Renders the page to an image and runs Tesseract OCR with multi-language support.
    
    Args:
        page: PyMuPDF page object
        page_index: Page number
        languages: List of ISO language codes (e.g., ['en', 'hi', 'ta'])
    """
    if not _HAS_TESSERACT:
        # OCR is opt-in and requires the tesseract binary; skip cleanly when unavailable
        # (Vercel serverless runtime has no tesseract, and v1 scope excludes OCR).
        return []

    # Render page to image
    pix = page.get_pixmap(dpi=150)
    img_data = pix.tobytes("png")
    image = Image.open(io.BytesIO(img_data))

    # Configure Tesseract for multi-language OCR
    tesseract_lang = fonts.get_multi_lang_tesseract_config(languages)

    # Run PyTesseract to get data
    # output_type=Output.DICT returns a dictionary with lists of results
    try:
        data = pytesseract.image_to_data(image, lang=tesseract_lang, output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError:
        print("Tesseract not found. Skipping OCR.")
        return []
    except SystemExit as e:
        # pytesseract raises SystemExit on version-check failure (BaseException, not Exception)
        print(f"OCR unavailable (tesseract version issue): {e}")
        return []
    except Exception as e:
        print(f"OCR Error: {e}")
        return []
    
    output_blocks = []
    
    # Iterate through the data
    # pytesseract returns words. We might want to group them into lines or blocks.
    # Grouping logic:
    # 'block_num' identifies the block.
    
    current_block_num = -1
    current_block_text = []
    current_bbox = [None, None, None, None] # min_left, min_top, max_right, max_bottom
    
    n_boxes = len(data['text'])
    
    for i in range(n_boxes):
        text = data['text'][i].strip()
        if not text:
            continue
            
        block_num = data['block_num'][i]
        
        # New block detection
        if block_num != current_block_num:
            # Save previous block
            if current_block_num != -1 and current_block_text:
                output_blocks.append(create_ocr_block(page_index, current_block_num, current_block_text, current_bbox))
            
            # Reset
            current_block_num = block_num
            current_block_text = []
            current_bbox = [data['left'][i], data['top'][i], data['left'][i] + data['width'][i], data['top'][i] + data['height'][i]]
        
        # Accumulate text
        current_block_text.append(text)
        
        # Update bbox
        left = data['left'][i]
        top = data['top'][i]
        right = left + data['width'][i]
        bottom = top + data['height'][i]
        
        current_bbox[0] = min(current_bbox[0], left)
        current_bbox[1] = min(current_bbox[1], top)
        current_bbox[2] = max(current_bbox[2], right)
        current_bbox[3] = max(current_bbox[3], bottom)
        
    # Append last block
    if current_block_num != -1 and current_block_text:
        output_blocks.append(create_ocr_block(page_index, current_block_num, current_block_text, current_bbox))

    return output_blocks

def create_ocr_block(page_index, block_num, text_list, bbox):
    # Convert pytesseract flow back to PyMuPDF coordinate space?
    # PyMuPDF get_pixmap and Tesseract should ideally align if DPI is handled.
    # We used default rendering.
    # Note: Tesseract coordinates are pixels. We need to check if scaling is required.
    # For now, let's assume 1:1 if we don't scale the pixmap too much, but dpi=72 is standard PDF.
    # We used dpi=150. So we need to scale down by 150/72 = 2.08333
    
    scale_factor = 72 / 150
    
    x0 = bbox[0] * scale_factor
    y0 = bbox[1] * scale_factor
    x1 = bbox[2] * scale_factor
    y1 = bbox[3] * scale_factor
    
    full_text = " ".join(text_list)
    
    # Estimate font size from bbox height? (Roughly)
    # Average line height... Tesseract gives line info but we grouped by block.
    # Let's approximate from block height / num lines (if we knew lines).
    # For now, simplistic approximation:
    height = y1 - y0
    # Assuming the block is one simplified line for demo or just taking a standard size
    estimated_size = 12 # Default
    
    # Detect script from OCR text
    script = fonts.detect_script(full_text)
    
    return {
        "id": f"p{page_index}_ocr_{block_num}",
        "type": "text",
        "content": full_text,
        "bbox": [x0, y0, x1, y1],
        "script": script,
        "style": {
            "fontSize": estimated_size,
            "fontFamily": "Arial", # generic
        }
    }
