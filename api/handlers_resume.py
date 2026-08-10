"""Resume-builder AI coach and parser HTTP handlers.

Backs the /resume/coach and /resume/parse endpoints.
"""
import json
import logging
import os
import base64
import io

logger = logging.getLogger("api.webhook")

import docx_engine

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

def _handle_resume_coach(h, body: bytes) -> None:
    """POST /resume/coach — AI-assisted resume builder assistant."""
    from api.index import _json_response
    try:
        data = json.loads(body)
        action = data.get("action", "")
        payload = data.get("data", {})
        
        client = _get_claude_client()
        if not client:
            _json_response(h, 503, {"error": "AI service is currently unavailable. Please verify API key setup."})
            return

        model = os.environ.get("ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6")
        prompt = ""

        if action == "improve_summary":
            name = payload.get("name", "")
            role = payload.get("role", "")
            location = payload.get("location", "")
            current = payload.get("current_summary", "")
            jd = payload.get("job_description", "")
            
            prompt = f"Write a professional CV summary for a candidate named '{name}' with the role/title '{role}'"
            if location:
                prompt += f" based in '{location}'"
            if current:
                prompt += f". Improve this existing draft: '{current}'"
            if jd:
                prompt += f". Tailor it specifically to align with this job description:\n{jd}\n"
            prompt += "\nFormat guidelines: Keep it highly professional, outcome-focused, 3-4 lines maximum. Return ONLY the plain text summary, no quotes, no markdown, no conversational preface/epilogue."

        elif action == "improve_experience":
            role = payload.get("role", "")
            company = payload.get("company", "")
            current = payload.get("desc", "")
            jd = payload.get("job_description", "")
            
            prompt = f"Improve the work experience description for the role '{role}' at company '{company}'"
            if current:
                prompt += f". Current description/bullets: '{current}'"
            if jd:
                prompt += f". Tailor it to highlight keywords and requirements from this job description:\n{jd}\n"
            prompt += "\nFormat guidelines: Rewrite as 2-3 high-impact, professional bullet points using the STAR method (quantifiable metrics, strong action verbs, e.g., 'Led...', 'Optimized...'). Use a plain list starting with '- ' for each line. No markdown formatting like bolding, no preface/epilogue."

        elif action == "suggest_skills":
            role = payload.get("role", "")
            jd = payload.get("job_description", "")
            
            prompt = f"Suggest 12-15 relevant professional skills for a candidate with the role/background: '{role}'"
            if jd:
                prompt += f" tailored to this job description:\n{jd}\n"
            prompt += "\nFormat guidelines: Return ONLY a comma-separated list of skills (e.g. 'Project Management, SQL, Excel, Team Leadership'). No explanations, no introductory text, no markdown."

        elif action == "optimize_ats":
            resume_text = payload.get("resume_text", "")
            jd = payload.get("job_description", "")
            
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
            jd = payload.get("job_description", "")
            
            prompt = f"""You are an elite executive resume writer. Tailor the following candidate resume to align perfectly with the target job description while maintaining realistic and professional descriptions.
            
JOB DESCRIPTION:
{jd}

CANDIDATE RESUME DATA:
{json.dumps(resume_data, indent=2)}

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
        result_text = response.content[0].text.strip()
        
        # Clean up JSON if Claude wrapped it in backticks despite instructions
        if action in ("optimize_ats", "tailor_full_resume"):
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            result_text = result_text.strip()
            try:
                result_json = json.loads(result_text)
                _json_response(h, 200, result_json)
            except Exception as e:
                logger.error("Failed to parse JSON response from Claude: %s", e)
                _json_response(h, 500, {"error": "Failed to parse AI optimization recommendations."})
        else:
            _json_response(h, 200, {"result": result_text})

    except Exception as exc:
        logger.error("resume coach error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})

def _handle_resume_parse(h, body: bytes) -> None:
    """POST /resume/parse — Parse CV PDF/DOCX or LinkedIn PDF export into structured schema."""
    from api.index import _json_response
    try:
        data = json.loads(body)
        content_b64 = data.get("content_b64", "")
        filename = data.get("filename", "resume.pdf")

        if not content_b64:
            _json_response(h, 400, {"error": "No file content provided"})
            return

        file_bytes = base64.b64decode(content_b64)
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
        
        result_text = response.content[0].text.strip()
        if result_text.startswith("```json"):
            result_text = result_text[7:]
        if result_text.endswith("```"):
            result_text = result_text[:-3]
        result_text = result_text.strip()

        try:
            parsed_resume = json.loads(result_text)
            _json_response(h, 200, parsed_resume)
        except Exception as e:
            logger.error("Failed to parse Claude output as JSON: %s. Raw output: %s", e, result_text)
            _json_response(h, 500, {"error": "Failed to parse document text into structured resume format."})

    except Exception as exc:
        logger.error("resume parse error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})
