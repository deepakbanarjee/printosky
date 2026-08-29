"""Stage 1 of the transcription feedback loop: corrections must be recorded.

website/dtp.html has always let staff edit a transcript and save it, but the
save PATCHes `content` in place — the model's original text was overwritten and
lost. Every correction anyone has ever made to a manuscript is gone. These tests
pin the capture path so it cannot quietly regress to overwriting again.

The diff itself lives in the browser (the console writes to Supabase directly,
as every other page in this repo does), so this file asserts on the source the
way tests/test_docs_deploy_pipeline.py does. The interpretation of the captured
data — word-level pairs, chillu normalisation, the promotion rule — is Stage 2
and lands in Python with real unit tests.
"""
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
DTP = (ROOT / "website" / "dtp.html").read_text(encoding="utf-8")
MANIFEST = yaml.safe_load((ROOT / "config" / "schema_manifest.yaml").read_text(encoding="utf-8"))
MIGRATION = (ROOT / "supabase" / "migrations"
             / "20260829120000_transcript_corrections.sql").read_text(encoding="utf-8")


def test_saving_a_transcript_records_the_corrections():
    """The whole point: a save must write to transcript_corrections, not just
    PATCH content."""
    assert "trRecordCorrections" in DTP
    assert "/rest/v1/transcript_corrections" in DTP


def test_corrections_are_recorded_only_after_the_save_lands():
    """A correction row for an edit that never persisted would be a lie in the
    data Stage 2 learns from. The call must sit inside the res.ok branch."""
    body = DTP[DTP.index("async function trSaveTranscript()"):]
    ok_branch = body.index("if (res.ok)")
    call = body.index("trRecordCorrections(fileObj")
    assert call > ok_branch, "corrections must be logged after the content PATCH succeeds"


def test_a_failed_correction_log_never_breaks_the_save():
    """The transcript is what the user came to do; this is bookkeeping on top."""
    body = DTP[DTP.index("async function trSaveTranscript()"):]
    guarded = re.search(r"try \{\s*logged = await trRecordCorrections", body)
    assert guarded, "the correction log must be wrapped so it cannot fail the save"


def test_a_failed_correction_log_is_visible():
    """docs/FAIL_LOUD.md: a silent drop would leave us believing we are
    collecting data when we are not."""
    assert "correction log failed" in DTP


def test_the_page_parser_is_shared_not_reimplemented():
    """The diff must split pages exactly the way the editor does, or it would
    compare mismatched text."""
    assert "function trPagesFromContent" in DTP
    assert DTP.count("=== PAGE (\\d+) ===") >= 2


def test_unchanged_pages_are_not_recorded():
    """A save touches one page; recording all 57 would bury the signal."""
    body = DTP[DTP.index("async function trRecordCorrections"):]
    assert "if (was === now" in body, "unchanged pages must be skipped"


@pytest.mark.parametrize("column", [
    "transcript_id", "filename", "page", "before_text", "after_text",
    "corrected_by", "store_id", "created_at",
])
def test_the_console_and_the_schema_agree(column):
    """Every column the console writes must exist — the confidence_data outage
    was exactly this failure (code shipped, migration did not)."""
    assert column in MANIFEST["tables"]["transcript_corrections"]["columns"]
    if column not in ("created_at",):
        assert column in DTP, f"the console never writes {column}"


def test_the_migration_matches_the_manifest():
    declared = set(MANIFEST["tables"]["transcript_corrections"]["columns"])
    for column in declared:
        assert column in MIGRATION, f"{column} is in the manifest but not the migration"


def test_the_table_has_rls():
    """Every public table in this project has RLS; docs/SCHEMA.md tracks the
    exceptions and this must not become one."""
    assert MANIFEST["tables"]["transcript_corrections"]["rls"] is True
    assert "ENABLE ROW LEVEL SECURITY" in MIGRATION
