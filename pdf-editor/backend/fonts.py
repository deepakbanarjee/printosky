"""
Font management for Indic languages in PDF Editor.
Handles font detection, mapping, and selection for various Indic scripts.
"""

import os
from typing import Dict, Optional, List

# Font mapping for different scripts/languages
SCRIPT_FONTS = {
    # Devanagari (Hindi, Marathi, Nepali, Sanskrit)
    'devanagari': {
        'primary': 'NotoSansDevanagari',
        'fallbacks': ['Mangal', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-devanagari'
    },
    # Tamil
    'tamil': {
        'primary': 'NotoSansTamil',
        'fallbacks': ['Lohit Tamil', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-tamil'
    },
    # Telugu
    'telugu': {
        'primary': 'NotoSansTelugu',
        'fallbacks': ['Pothana2000', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-telugu'
    },
    # Bengali
    'bengali': {
        'primary': 'NotoSansBengali',
        'fallbacks': ['Vrinda', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-bengali'
    },
    # Gujarati
    'gujarati': {
        'primary': 'NotoSansGujarati',
        'fallbacks': ['Shruti', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-gujarati'
    },
    # Kannada
    'kannada': {
        'primary': 'NotoSansKannada',
        'fallbacks': ['Tunga', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-kannada'
    },
    # Malayalam
    'malayalam': {
        'primary': 'NotoSansMalayalam',
        'fallbacks': ['Kartika', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-malayalam'
    },
    # Gurmukhi (Punjabi)
    'gurmukhi': {
        'primary': 'NotoSansGurmukhi',
        'fallbacks': ['Raavi', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-gurmukhi'
    },
    # Odia
    'odia': {
        'primary': 'NotoSansOriya',
        'fallbacks': ['Kalinga', 'Nirmala UI', 'Arial Unicode MS'],
        'pymupdf_name': 'noto-sans-oriya'
    },
    # Default/Latin
    'latin': {
        'primary': 'Helvetica',
        'fallbacks': ['Arial', 'Liberation Sans'],
        'pymupdf_name': 'helv'
    }
}

# Language to script mapping
LANGUAGE_SCRIPT_MAP = {
    'hi': 'devanagari',  # Hindi
    'mr': 'devanagari',  # Marathi
    'ne': 'devanagari',  # Nepali
    'sa': 'devanagari',  # Sanskrit
    'ta': 'tamil',       # Tamil
    'te': 'telugu',      # Telugu
    'bn': 'bengali',     # Bengali
    'gu': 'gujarati',    # Gujarati
    'kn': 'kannada',     # Kannada
    'ml': 'malayalam',   # Malayalam
    'pa': 'gurmukhi',    # Punjabi
    'or': 'odia',        # Odia
    'en': 'latin',       # English
}

# Tesseract language codes
TESSERACT_LANG_CODES = {
    'hi': 'hin',
    'mr': 'mar',
    'ta': 'tam',
    'te': 'tel',
    'bn': 'ben',
    'gu': 'guj',
    'kn': 'kan',
    'ml': 'mal',
    'pa': 'pan',
    'or': 'ori',
    'en': 'eng',
}

def detect_script(text: str) -> str:
    """
    Detect the script used in the text based on Unicode ranges.
    Returns script name (e.g., 'devanagari', 'tamil', etc.)
    """
    if not text:
        return 'latin'
    
    # Unicode ranges for Indic scripts
    script_ranges = {
        'devanagari': (0x0900, 0x097F),
        'bengali': (0x0980, 0x09FF),
        'gurmukhi': (0x0A00, 0x0A7F),
        'gujarati': (0x0A80, 0x0AFF),
        'odia': (0x0B00, 0x0B7F),
        'tamil': (0x0B80, 0x0BFF),
        'telugu': (0x0C00, 0x0C7F),
        'kannada': (0x0C80, 0x0CFF),
        'malayalam': (0x0D00, 0x0D7F),
    }
    
    # Count characters in each script
    script_counts = {script: 0 for script in script_ranges}
    latin_count = 0
    
    for char in text:
        code_point = ord(char)
        found = False
        for script, (start, end) in script_ranges.items():
            if start <= code_point <= end:
                script_counts[script] += 1
                found = True
                break
        if not found and char.isalpha():
            latin_count += 1
    
    # Return the script with the most characters
    max_script = max(script_counts, key=script_counts.get)
    if script_counts[max_script] > 0:
        return max_script
    
    return 'latin'

def get_font_for_script(script: str) -> str:
    """
    Get the PyMuPDF font name for a given script.
    Returns the font name to use with PyMuPDF.
    """
    if script in SCRIPT_FONTS:
        return SCRIPT_FONTS[script]['pymupdf_name']
    return SCRIPT_FONTS['latin']['pymupdf_name']

def get_font_family_for_script(script: str) -> str:
    """
    Get the CSS font family for a given script.
    Returns the primary font name for web rendering.
    """
    if script in SCRIPT_FONTS:
        return SCRIPT_FONTS[script]['primary']
    return SCRIPT_FONTS['latin']['primary']

def get_tesseract_lang(language_code: str) -> str:
    """
    Convert ISO language code to Tesseract language code.
    """
    return TESSERACT_LANG_CODES.get(language_code, 'eng')

def get_multi_lang_tesseract_config(languages: List[str] = None) -> str:
    """
    Get Tesseract language configuration for multiple languages.
    If no languages specified, returns a config for common Indic languages + English.
    """
    if languages is None:
        # Default: English + major Indic languages
        languages = ['en', 'hi', 'ta', 'te', 'bn']
    
    tesseract_codes = [get_tesseract_lang(lang) for lang in languages]
    return '+'.join(tesseract_codes)

def detect_language_from_text(text: str) -> str:
    """
    Detect language from text using langdetect.
    Returns ISO language code (e.g., 'hi', 'ta', 'en').
    """
    try:
        from langdetect import detect
        lang = detect(text)
        # Map common language codes
        if lang in LANGUAGE_SCRIPT_MAP:
            return lang
        return 'en'
    except:
        # Fallback: detect script and map to language
        script = detect_script(text)
        # Return first language that uses this script
        for lang, scr in LANGUAGE_SCRIPT_MAP.items():
            if scr == script:
                return lang
        return 'en'
