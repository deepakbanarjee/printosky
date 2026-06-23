import os
import pytest
from docx_engine import (
    verify_models_available,
    _parse_structure_sonnet,
    _classify_with_claude_metadata
)

def test_verify_models_nvidia():
    """Verify that both Anthropic and NVIDIA models are checked if keys are present."""
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    res = verify_models_available()
    assert res.get("_status") == "checked"
    if os.environ.get("NVIDIA_API_KEY"):
        assert res.get("meta/llama-3.1-70b-instruct") == "ok"
        assert res.get("meta/llama-3.1-8b-instruct") == "ok"

def test_structure_detection_nvidia_fallback():
    """Verify that structure detection falls back to NVIDIA NIM when Anthropic is disabled."""
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    if not os.environ.get("NVIDIA_API_KEY"):
        pytest.skip("NVIDIA_API_KEY not configured, skipping integration test")
        
    # Force fallback by clearing Anthropic key
    orig_anthropic = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = ""
    
    try:
        text = (
            "CHAPTER 1\nINTRODUCTION\nThis is the introduction prose verbatim.\n"
            "1.1 Background\nBackground info goes here."
        )
        res = _parse_structure_sonnet(text)
        assert "error" not in res
        assert res.get("model_used") == "meta/llama-3.1-70b-instruct"
        assert len(res.get("chapters", [])) >= 1
        # Accept either case-insensitive match or exact heading match
        assert res["chapters"][0]["heading"].strip().upper() == "INTRODUCTION"
    finally:
        if orig_anthropic is not None:
            os.environ["ANTHROPIC_API_KEY"] = orig_anthropic

def test_paragraph_classification_nvidia_fallback():
    """Verify that paragraph metadata classification falls back to NVIDIA NIM."""
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    if not os.environ.get("NVIDIA_API_KEY"):
        pytest.skip("NVIDIA_API_KEY not configured, skipping integration test")
        
    orig_anthropic = os.environ.get("ANTHROPIC_API_KEY")
    os.environ["ANTHROPIC_API_KEY"] = ""
    
    try:
        paras = [
            {"is_blank": False, "text": "CHAPTER 1: INTRODUCTION", "max_size": 16.0, "bold": True, "length": 22},
            {"is_blank": False, "text": "This is a regular sentence describing research.", "max_size": 12.0, "bold": False, "length": 48},
            {"is_blank": True, "text": "", "max_size": 12.0, "bold": False, "length": 0}
        ]
        res = _classify_with_claude_metadata(paras)
        assert len(res) == len(paras)
        assert res[0] in ("h1", "title")
        assert res[1] == "body"
        assert res[2] == "blank"
    finally:
        if orig_anthropic is not None:
            os.environ["ANTHROPIC_API_KEY"] = orig_anthropic
