"""OSP Academics — Format-Fix Pipeline.

Section-handler architecture: each document section (cover, acknowledgement,
declaration, ToC, chapter, references, ...) is a separate handler class that
declares whether it applies to a given page and renders its output into a
shared python-docx Document.

Entry point: `from format_fix.orchestrator import run`
"""
