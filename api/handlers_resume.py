"""Resume-builder AI coach and parser HTTP handlers.

Backs the /resume/coach and /resume/parse endpoints.

Security notes:
  * These endpoints are public (CORS *) and unauthenticated, calling Claude on
    the shop's ANTHROPIC_API_KEY. To keep them from being drained as a free
    general-purpose LLM proxy, every request body and every user-supplied field
    is length-capped BEFORE it reaches the prompt (see _CAPS / _clip), and the
    whole body is size-checked first. Prompts are fixed-purpose (resume help
    only); we never forward a raw client-supplied prompt.
  * On failure we log the real exception but return a generic message — raw
    exception strings must not leak to the browser.
"""
import json
import logging
import os
import base64

logger = logging.getLogger("api.webhook")

# Max raw request body sizes (bytes). /coach carries only text fields; /parse
# carries a base64-encoded document, so it needs a much larger envelope.
_MAX_COACH_BODY = 120_000
_MAX_PARSE_BODY = 10_000_000  # ~7.5 MB decoded file

# Per-field character caps. Short = single-line identity fields; the rest bound
# the free-text that actually drives token spend.
_CAPS = {
    "short": 200,      # name, role, company, location
    "summary": 4_000,  # current_summary, a single experience desc
    "jd": 8_000,       # job description
    "resume": 20_000,  # flattened resume text / serialized resume_data
}


def _get_claude_client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.warning("handlers_resume: ANTHROPIC_API_KEY missing; AI features will fail soft")
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except Exception as exc:
        logger.error("handlers_resume: anthropic init failed: %s", exc)
        return None


def _clip(value, kind="short") -> str:
    """Coerce to a stripped string capped to the limit for `kind`."""
    return str(value or "").strip()[: _CAPS.get(kind, _CAPS["short"])]


def _extract_text(response) -> str:
    """Concatenate the text blocks of a Claude message, robustly.

    Real SDK text blocks carry type == "text"; tool_use / other blocks are
    skipped. Blocks with no `type` (e.g. test doubles) are treated as text so
    the endpoint degrades gracefully rather than IndexError-ing on content[0].
    """
    parts = []
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", "text") == "text" and hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts).strip()


def _strip_json_fence(text: str) -> str:
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _handle_resume_coach(h, body: bytes) -> None:
    """POST /resume/coach — AI-assisted resume builder assistant."""
    from api.index import _json_response
    try:
        if body and len(body) > _MAX_COACH_BODY:
            _json_response(h, 413, {"error": "Request too large"})
            return

        data = json.loads(body)
        action = data.get("action", "")
        payload = data.get("data", {}) or {}

        client = _get_claude_client()
        if not client:
            _json_response(h, 503, {"error": "AI service is currently unavailable. Please verify API key setup."})
            return

        model = os.environ.get("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")
        prompt = ""

        if action == "improve_summary":
            name = _clip(payload.get("name"))
            role = _clip(payload.get("role"))
            location = _clip(payload.get("location"))
            current = _clip(payload.get("current_summary"), "summary")
            jd = _clip(payload.get("job_description"), "jd")

            prompt = f"Write a professional CV summary for a candidate named '{name}' with the role/title '{role}'"
            if location:
                prompt += f" based in '{location}'"
            if current:
                prompt += f". Improve this existing draft: '{current}'"
            if jd:
                prompt += f". Tailor it specifically to align with this job description:\n{jd}\n"
            prompt += "\nFormat guidelines: Keep it highly professional, outcome-focused, 3-4 lines maximum. Return ONLY the plain text summary, no quotes, no markdown, no conversational preface/epilogue."

        elif action == "improve_experience":
            role = _clip(payload.get("role"))
            company = _clip(payload.get("company"))
            current = _clip(payload.get("desc"), "summary")
            jd = _clip(payload.get("job_description"), "jd")

            prompt = f"Improve the work experience description for the role '{role}' at company '{company}'"
            if current:
                prompt += f". Current description/bullets: '{current}'"
            if jd:
                prompt += f". Tailor it to highlight keywords and requirements from this job description:\n{jd}\n"
            prompt += "\nFormat guidelines: Rewrite as 2-3 high-impact, professional bullet points using the STAR method (quantifiable metrics, strong action verbs, e.g., 'Led...', 'Optimized...'). Use a plain list starting with '- ' for each line. No markdown formatting like bolding, no preface/epilogue."

        elif action == "suggest_skills":
            role = _clip(payload.get("role"))
            jd = _clip(payload.get("job_description"), "jd")

            prompt = f"Suggest 12-15 relevant professional skills for a candidate with the role/background: '{role}'"
            if jd:
                prompt += f" tailored to this job description:\n{jd}\n"
            prompt += "\nFormat guidelines: Return ONLY a comma-separated list of skills (e.g. 'Project Management, SQL, Excel, Team Leadership'). No explanations, no introductory text, no markdown."

        elif action == "optimize_ats":
            resume_text = _clip(payload.get("resume_text"), "resume")
            jd = _clip(payload.get("job_description"), "jd")

            prompt = f"""You are an expert ATS (Applicant Tracking System) recruiter and resume scanner. Compare the following resume content with the target job description and provide a structured JSON response.

JOB DESCRIPTION:
{jd}

RESUME TEXT:
{resume_text}

Format guidelines: Return ONLY a JSON object conforming strictly to the schema below. Do not include markdown code block backticks (like ```json), no preface, no epilogue.
{{
  "score": 85, // integer matching score from 0 to 100 based on keyword alignment, structural layout, formatting and requirements
  "matching_keywords": ["SQL", "Agile"], // list of matching keywords found in both
  "missing_keywords": ["Tableau", "AWS"], // list of important keywords in JD missing from resume
  "formatting_tips": ["Include a professional email address", "Ensure contact number matches typical formats"], // list of formatting warnings or checklist items
  "content_tips": ["Add more metrics in work experience", "Revise summary to highlight cloud technology experience"] // list of bullet optimization tips
}}"""

        elif action == "tailor_full_resume":
            resume_data = payload.get("resume_data", {})
            # Serialize then cap: bounds token spend even if the client sends a huge object.
            resume_data_json = json.dumps(resume_data, indent=2)[: _CAPS["resume"]]
            jd = _clip(payload.get("job_description"), "jd")

            prompt = f"""You are an elite executive resume writer. Tailor the following candidate resume to align perfectly with the target job description while maintaining realistic and professional descriptions.

JOB DESCRIPTION:
{jd}

CANDIDATE RESUME DATA:
{resume_data_json}

Task:
1. Revise the summary statement to align with the keywords and responsibilities in the job description.
2. Revise each experience entry's 'desc' bullet points to emphasize relevant projects, methodologies, and achievements that match the JD. Keep the list format starting with '-' for each bullet. Ensure the returned 'experience' array has the exact same number of entries as the input resume, in the exact same order, containing only the tailored 'desc' string for each respective job.
3. Recommend an updated list of comma-separated skills matching the JD, preserving their core expertise.

Format guidelines: Return ONLY a JSON object conforming strictly to the schema below. Do not include markdown code block backticks (like ```json), no preface, no epilogue.
{{
  "summary": "Revised professional summary statement...",
  "skills": "Skill1, Skill2, Skill3, ...",
  "experience": [
    {{
      "desc": "- Revised bullet 1\\n- Revised bullet 2..."
    }}
  ]
}}"""

        else:
            _json_response(h, 400, {"error": f"Invalid action: '{action}'"})
            return

        response = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        result_text = _extract_text(response)

        # Clean up JSON if Claude wrapped it in backticks despite instructions
        if action in ("optimize_ats", "tailor_full_resume"):
            result_text = _strip_json_fence(result_text)
            try:
                result_json = json.loads(result_text)
                _json_response(h, 200, result_json)
            except Exception as e:
                logger.error("Failed to parse JSON response from Claude: %s", e)
                _json_response(h, 502, {"error": "Failed to parse AI optimization recommendations."})
        else:
            _json_response(h, 200, {"result": result_text})

    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "Invalid JSON"})
    except Exception as exc:
        logger.error("resume coach error: %s", exc)
        _json_response(h, 500, {"error": "Internal error"})


def _handle_resume_parse(h, body: bytes) -> None:
    """POST /resume/parse — Parse CV PDF/DOCX or LinkedIn PDF export into structured schema."""
    from api.index import _json_response
    # Lazy import (matches handlers_pb): keeps python-docx / pdfplumber / PyMuPDF
    # out of the import graph of every unrelated endpoint's cold start.
    import docx_engine
    try:
        if body and len(body) > _MAX_PARSE_BODY:
            _json_response(h, 413, {"error": "File too large. Please upload a resume under ~7 MB."})
            return

        data = json.loads(body)
        content_b64 = data.get("content_b64", "")
        filename = data.get("filename", "resume.pdf")

        if not content_b64:
            _json_response(h, 400, {"error": "No file content provided"})
            return

        try:
            file_bytes = base64.b64decode(content_b64)
        except Exception:
            _json_response(h, 400, {"error": "File content is not valid base64"})
            return

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext == "pdf":
            text = ""
            # Pass 1: Try PyMuPDF (fitz) — fast and robust
            try:
                import fitz
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                text = "\n\n".join(page.get_text() for page in doc)
            except Exception as e:
                logger.warning("fitz extraction failed: %s", e)

            # Pass 2: Fallback to pdfplumber if fitz returned nothing
            if not text.strip():
                try:
                    text = docx_engine.extract_text_from_pdf(file_bytes)
                except Exception as e:
                    logger.warning("pdfplumber extraction failed: %s", e)
        elif ext == "docx":
            text = docx_engine.extract_text_from_docx(file_bytes)
        else:
            _json_response(h, 400, {"error": "Only .pdf and .docx files are supported for import"})
            return

        if not text.strip():
            _json_response(h, 400, {
                "error": "Failed to extract text from the document. This usually happens if the PDF is scanned/image-only and lacks a digital text layer, or if the file is empty/corrupted. Please upload a digitally exported PDF/DOCX or paste details manually."
            })
            return

        client = _get_claude_client()
        if not client:
            _json_response(h, 503, {"error": "AI parsing service is currently unavailable. Please verify API key setup."})
            return

        model = os.environ.get("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")
        # Cap extracted text before it hits the prompt — a huge PDF shouldn't
        # translate into an unbounded token bill.
        text = text[:20_000]
        prompt = f"""You are an expert Applicant Tracking System (ATS) parser. Analyze the extracted resume text below and structure it into a JSON object matching the schema.

EXTRACTED TEXT:
{text}

Format guidelines: Return ONLY a JSON object conforming strictly to the schema below. Do not include markdown code block backticks (like ```json), no preface, no epilogue. If a field cannot be found, populate it with an empty string or empty array as appropriate.

SCHEMA:
{{
  "name": "Full Name",
  "role": "Job Title / Role title",
  "phone": "Phone number",
  "email": "Email address",
  "location": "City, State / Location name",
  "linkedin": "LinkedIn profile link or empty",
  "summary": "Professional summary paragraph",
  "skills": "Comma-separated list of skills",
  "achievements": "Achievements and certifications, separated by newline starting with '-' (e.g. '\\n- Cert 1\\n- Cert 2')",
  "languages": "Comma-separated list of languages",
  "education": [
    {{
      "degree": "Degree/Qualification",
      "institution": "School/College/Institution name",
      "year": "Graduation year or date range (e.g. 2021-2024)",
      "grade": "Percentage or CGPA or empty"
    }}
  ],
  "experience": [
    {{
      "role": "Job Title/Role",
      "company": "Company/Organization name",
      "duration": "Duration (e.g. June 2022 - Present)",
      "desc": "Responsibilities and achievements (paragraphs or bullet points)"
    }}
  ]
}}"""

        response = client.messages.create(
            model=model,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        result_text = _strip_json_fence(_extract_text(response))

        try:
            parsed_resume = json.loads(result_text)
            _json_response(h, 200, parsed_resume)
        except Exception as e:
            logger.error("Failed to parse Claude output as JSON: %s. Raw output: %s", e, result_text)
            _json_response(h, 502, {"error": "Failed to parse document text into structured resume format."})

    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "Invalid JSON"})
    except Exception as exc:
        logger.error("resume parse error: %s", exc)
        _json_response(h, 500, {"error": "Internal error"})
