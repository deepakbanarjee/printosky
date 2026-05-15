"""Format-Fix Pipeline orchestrator.

Walks every PDF page, asks each registered SectionHandler in priority order
whether it claims the page, and dispatches to the first match. Each handler
mutates a shared python-docx Document. After all pages are processed, a
final font-discipline pass enforces TNR / 12pt-body / 14pt-bold-heading /
black-text-only.

CLI:
    python -m format_fix.orchestrator <pdf> <university_id> <output_docx> \
        [--skip-pages 1,3,5]

Library:
    from format_fix.orchestrator import run
    run(pdf_path, university_id, output_path, skip_pages=[3])
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitz
from docx import Document

from . import extraction
from .context import Context
from .styles import apply_university_styles, enforce_font_discipline

from .handlers.base            import SectionHandler
from .handlers.cover           import CoverHandler
from .handlers.acknowledgement import AcknowledgementHandler
from .handlers.declaration     import DeclarationHandler
from .handlers.abstract        import AbstractHandler
from .handlers.toc             import TableOfContentsHandler
from .handlers.survey_table    import SurveyTableHandler
from .handlers.form_page       import FormPageHandler
from .handlers.references      import ReferencesHandler
from .handlers.annexures       import AnnexuresHandler
from .handlers.chapter         import ChapterHandler


def _build_handlers() -> list[SectionHandler]:
    """Registered handlers, sorted by priority (lower = checked first).

    Ordering matters because applies_to() decides who claims a page; the
    catch-all ChapterHandler must be last.
    """
    handlers: list[SectionHandler] = [
        SurveyTableHandler(),       # 10
        FormPageHandler(),          # 20
        CoverHandler(),             # 30
        AcknowledgementHandler(),   # 40
        DeclarationHandler(),       # 50
        AbstractHandler(),          # 60
        TableOfContentsHandler(),   # 70
        ReferencesHandler(),        # 80
        AnnexuresHandler(),         # 85
        ChapterHandler(),           # 100  catch-all
    ]
    handlers.sort(key=lambda h: h.priority)
    return handlers


def run(pdf_path: str | Path,
        university_id: str,
        output_path: str | Path,
        skip_pages: list[int] | None = None,
        verbose: bool = False) -> dict:
    """Build a formatted DOCX from a PDF using section-handler dispatch.

    Returns a small summary dict with counts (pages_processed, claims by
    handler name) so callers can log/display what each agent did.
    """
    pdf_path    = Path(pdf_path)
    output_path = Path(output_path)
    pdf         = fitz.open(str(pdf_path))

    try:
        ctx = Context.build(pdf, university_id, skip_pages=skip_pages)
        doc = Document()
        apply_university_styles(doc, ctx.config)

        handlers = _build_handlers()
        claims: dict[str, int] = {h.name: 0 for h in handlers}
        claims["__skipped__"] = 0
        claims["__nohandler__"] = 0

        for page_no in range(len(pdf)):
            if page_no in ctx.skip_set:
                claims["__skipped__"] += 1
                if verbose:
                    print(f"  p{page_no+1}: SKIPPED (user requested)")
                continue

            blocks = extraction.blocks_with_font(pdf, page_no)

            picked: SectionHandler | None = None
            for h in handlers:
                if h.applies_to(blocks, page_no, ctx):
                    picked = h
                    break

            if picked is None:
                claims["__nohandler__"] += 1
                if verbose:
                    print(f"  p{page_no+1}: no handler claimed (skipped)")
                continue

            if verbose:
                print(f"  p{page_no+1}: -> {picked.name}")
            picked.render(doc, blocks, page_no, ctx)
            claims[picked.name] += 1

        enforce_font_discipline(doc, ctx)
        doc.save(str(output_path))
        return {
            "output":  str(output_path),
            "pages":   len(pdf),
            "claims":  claims,
        }
    finally:
        pdf.close()


def run_from_docx(docx_bytes: bytes,
                  university_id: str,
                  output_path: str | Path,
                  skip_pages: list[int] | None = None,
                  verbose: bool = False) -> dict:
    """DOCX entry point — parallel to run() for PDF input.

    Parses the DOCX into virtual pages of 5-tuple blocks (matching the
    PDF blocks_with_font contract), then dispatches the existing handler
    pipeline over those pages. A blank fitz.Document with the same page
    count is created so ctx.pdf remains valid for handlers that touch
    it — image extraction in chapter/form_page handlers degrades to a
    no-op on blank pages, which is acceptable since DOCX images aren't
    re-embedded in this pipeline.

    Returns the same summary dict shape as run() plus source="docx".
    """
    from . import extraction_docx

    output_path = Path(output_path)

    pages_blocks, n_pages = extraction_docx.parse_docx_to_pages(docx_bytes)
    if n_pages == 0:
        n_pages = 1
        pages_blocks = [[]]

    # Synthesize a blank fitz.Document with the same page count so the
    # existing Context.build + handlers continue to function. They will
    # find no embedded images, which is fine for DOCX input.
    pdf = fitz.open()
    try:
        for _ in range(n_pages):
            pdf.new_page()  # A4 default

        ctx = Context.build(pdf, university_id, skip_pages=skip_pages)
        doc = Document()
        apply_university_styles(doc, ctx.config)

        # Skip FormPageHandler for DOCX input: it was designed for scanned
        # PDF survey/evaluation pages and renders the page as a PNG via
        # ctx.pdf. For DOCX, ctx.pdf is a blank shim — rendering it would
        # produce blank PNGs and silently drop the text content (e.g. the
        # "TRAINEE EVALUATION" pages of aswathy_Project.docx, which is
        # how 62% of body content was being lost). The other handlers
        # consume the 5-tuple blocks and work identically for DOCX
        # virtual pages.
        handlers = [h for h in _build_handlers() if h.name != "form_page"]
        claims: dict[str, int] = {h.name: 0 for h in handlers}
        claims["__skipped__"]   = 0
        claims["__nohandler__"] = 0

        for page_no in range(n_pages):
            if page_no in ctx.skip_set:
                claims["__skipped__"] += 1
                if verbose:
                    print(f"  p{page_no+1}: SKIPPED (user requested)")
                continue

            blocks = pages_blocks[page_no]

            picked: SectionHandler | None = None
            for h in handlers:
                if h.applies_to(blocks, page_no, ctx):
                    picked = h
                    break

            if picked is None:
                claims["__nohandler__"] += 1
                if verbose:
                    print(f"  p{page_no+1}: no handler claimed (skipped)")
                continue

            if verbose:
                print(f"  p{page_no+1}: -> {picked.name}")
            picked.render(doc, blocks, page_no, ctx)
            claims[picked.name] += 1

        enforce_font_discipline(doc, ctx)
        doc.save(str(output_path))
        return {
            "output":  str(output_path),
            "pages":   n_pages,
            "claims":  claims,
            "source":  "docx",
        }
    finally:
        pdf.close()


def _main() -> int:
    p = argparse.ArgumentParser(
        prog="python -m format_fix.orchestrator",
        description="Section-handler PDF -> DOCX pipeline.",
    )
    p.add_argument("pdf",     type=str, help="path to source PDF")
    p.add_argument("uni",     type=str, help="university config id (e.g. ignou)")
    p.add_argument("out",     type=str, help="path to output DOCX")
    p.add_argument("--skip-pages", type=str, default="",
                    help="comma-separated 1-based page numbers to drop")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    skip_pages: list[int] | None = None
    if args.skip_pages.strip():
        skip_pages = [int(x) for x in args.skip_pages.split(",")
                       if x.strip().isdigit()]

    summary = run(args.pdf, args.uni, args.out,
                   skip_pages=skip_pages, verbose=args.verbose)

    print()
    print(f"OK  output : {summary['output']}")
    print(f"OK  pages  : {summary['pages']}")
    print( "    claims :")
    for name, n in summary["claims"].items():
        if n:
            print(f"      {name:14s} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
