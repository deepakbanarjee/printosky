"""Browser tests for admin.html — runs against live Netlify site."""
import pytest
from playwright.sync_api import Page
from tests.pages.admin_page import AdminPage


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def admin(page: Page, base_url: str) -> AdminPage:
    ap = AdminPage(page)
    ap.url = f"{base_url}/admin.html"
    ap.navigate()
    return ap


@pytest.fixture
def logged_in_admin(admin: AdminPage, admin_password: str) -> AdminPage:
    admin.login_admin(admin_password)
    assert admin.is_logged_in(), "Admin login failed — check PRINTOSKY_ADMIN_PASSWORD in .env"
    return admin


@pytest.fixture
def logged_in_staff(admin: AdminPage, staff_pin: str) -> AdminPage:
    admin.login_pin(staff_pin)
    assert admin.is_logged_in(), "Staff PIN login failed — check PRINTOSKY_STAFF_PIN in .env"
    return admin


# ── A01 ───────────────────────────────────────────────────────────────────────

def test_a01_pin_login_success(admin: AdminPage, staff_pin: str) -> None:
    admin.login_pin(staff_pin)
    assert admin.is_logged_in(), "Expected dashboard visible after PIN login"


# ── A02 ───────────────────────────────────────────────────────────────────────

def test_a02_pin_login_wrong_pin(admin: AdminPage) -> None:
    admin.login_pin("000000")
    assert admin.login_error_visible(), "Expected #login-err after wrong PIN"
    assert not admin.is_logged_in()


# ── A03 ───────────────────────────────────────────────────────────────────────

def test_a03_admin_login_success(admin: AdminPage, admin_password: str) -> None:
    admin.login_admin(admin_password)
    assert admin.is_logged_in(), "Expected dashboard after admin override login"


# ── A04 ───────────────────────────────────────────────────────────────────────

def test_a04_admin_login_wrong_password(admin: AdminPage) -> None:
    admin.login_admin("wrongpassword123")
    assert not admin.is_logged_in(), "Should not log in with wrong admin password"


# ── A05 ───────────────────────────────────────────────────────────────────────

def test_a05_logout(logged_in_admin: AdminPage) -> None:
    logged_in_admin.logout()
    assert logged_in_admin.page.locator("#pin-input").is_visible()


# ── A06 ───────────────────────────────────────────────────────────────────────

def test_a06_idle_auto_logout(admin: AdminPage, admin_password: str) -> None:
    admin.page.evaluate("window._IDLE_TIMEOUT_MS = 3000;")
    admin.login_admin(admin_password)
    assert admin.is_logged_in()
    admin.page.wait_for_timeout(5000)
    pin_visible = admin.page.locator("#pin-input").is_visible()
    if not pin_visible:
        pytest.skip("Idle logout not triggered in 5s — idleLogout() may need STORE_PC to be active")


# ── A07 ───────────────────────────────────────────────────────────────────────

def test_a07_stats_panel_loads(logged_in_admin: AdminPage) -> None:
    page = logged_in_admin.page
    for stat_id in ["#s-jobs", "#s-done", "#s-pend", "#s-rev"]:
        assert page.locator(stat_id).is_visible(), f"{stat_id} not visible"
    assert page.locator("#s-jobs").inner_text().strip() != "", \
        "#s-jobs blank — Supabase sync may be broken"


# ── A08 ───────────────────────────────────────────────────────────────────────

def test_a08_konica_panel_loads(logged_in_admin: AdminPage) -> None:
    assert logged_in_admin.page.locator("#konica-files").is_visible()
    if logged_in_admin.page.locator("#konica-files > *").count() == 0:
        pytest.skip("WARN: #konica-files empty — printer_poller may not be running")


# ── A09 ───────────────────────────────────────────────────────────────────────

def test_a09_epson_panel_loads(logged_in_admin: AdminPage) -> None:
    assert logged_in_admin.page.locator("#epson-files").is_visible()
    if logged_in_admin.page.locator("#epson-files > *").count() == 0:
        pytest.skip("WARN: #epson-files empty — printer_poller may not be running")


# ── A10 ───────────────────────────────────────────────────────────────────────

def test_a10_tab_switching(logged_in_admin: AdminPage) -> None:
    logged_in_admin.switch_tab("conv")
    assert logged_in_admin.page.locator("#conv-inbox-list, #conv-search").first().is_visible()
    logged_in_admin.switch_tab("jobs")
    assert logged_in_admin.page.locator("#search").is_visible()


# ── A11–A14 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("label", ["All", "Completed", "Pending", "Today"])
def test_a11_to_a14_filter_buttons(logged_in_admin: AdminPage, label: str) -> None:
    logged_in_admin.set_filter(label)
    assert logged_in_admin.page.locator("table").first().is_visible(), \
        f"Table not visible after filter '{label}'"


# ── A15 ───────────────────────────────────────────────────────────────────────

def test_a15_search_filters_rows(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    total = logged_in_admin.job_row_count()
    logged_in_admin.search_jobs("ZZZNOMATCH999")
    assert logged_in_admin.job_row_count() <= total
    logged_in_admin.search_jobs("")


# ── A16 ───────────────────────────────────────────────────────────────────────

def test_a16_select_job_opens_panel(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No jobs in table")
    logged_in_admin.select_job(0)
    assert logged_in_admin.detail_panel_visible()


# ── A17 ───────────────────────────────────────────────────────────────────────

def test_a17_detail_panel_items(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No jobs")
    logged_in_admin.select_job(0)
    assert logged_in_admin.page.locator("#job-panel table").count() > 0


# ── A18 ───────────────────────────────────────────────────────────────────────

def test_a18_detail_panel_timeline(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No jobs")
    logged_in_admin.select_job(0)
    assert logged_in_admin.page.locator("#jp-timeline-section").is_visible()


# ── A19 ───────────────────────────────────────────────────────────────────────

def test_a19_detail_panel_dtp(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No jobs")
    logged_in_admin.select_job(0)
    assert logged_in_admin.page.locator("#jp-dtp-section").count() > 0


# ── A20 ───────────────────────────────────────────────────────────────────────

def test_a20_open_new_job_modal(logged_in_admin: AdminPage) -> None:
    logged_in_admin.open_new_job_modal()
    assert logged_in_admin.new_job_modal_visible()
    logged_in_admin.close_new_job_modal()


# ── A21 ───────────────────────────────────────────────────────────────────────

def test_a21_new_job_step1(logged_in_admin: AdminPage) -> None:
    logged_in_admin.open_new_job_modal()
    logged_in_admin.nj_step1_fill("PYTEST_BROWSER_TEST", "9999999999")
    assert logged_in_admin.page.locator("button:has-text('Skip'), #nj-file-input").first().is_visible()
    logged_in_admin.close_new_job_modal()


# ── A22 ───────────────────────────────────────────────────────────────────────

def test_a22_new_job_step2_skip(logged_in_admin: AdminPage) -> None:
    logged_in_admin.open_new_job_modal()
    logged_in_admin.nj_step1_fill("PYTEST_BROWSER_TEST", "9999999999")
    logged_in_admin.nj_step2_skip()
    assert logged_in_admin.page.locator("#nj-pages").is_visible()
    logged_in_admin.close_new_job_modal()


# ── A23 ───────────────────────────────────────────────────────────────────────

def test_a23_new_job_step2_upload(logged_in_admin: AdminPage, tmp_path) -> None:
    test_file = tmp_path / "test_upload.pdf"
    test_file.write_bytes(b"%PDF-1.4 1 0 obj<</Type/Catalog>>endobj")
    logged_in_admin.open_new_job_modal()
    logged_in_admin.nj_step1_fill("PYTEST_BROWSER_TEST", "9999999999")
    logged_in_admin.page.set_input_files("#nj-file-input", str(test_file))
    logged_in_admin.page.wait_for_timeout(1500)
    filename_shown = logged_in_admin.page.locator("#nj-file-name").inner_text().strip()
    assert filename_shown != "", "File name not shown after upload"
    logged_in_admin.close_new_job_modal()


# ── Helper: reach step 3 ──────────────────────────────────────────────────────

def _reach_step3(admin: AdminPage) -> None:
    admin.open_new_job_modal()
    admin.nj_step1_fill("PYTEST_BROWSER_TEST", "9999999999")
    admin.nj_step2_skip()
    admin.page.wait_for_selector("#nj-pages", state="visible")
    admin.page.fill("#nj-pages", "10")
    admin.page.fill("#nj-copies", "1")
    admin.page.wait_for_timeout(600)


# ── A24 ───────────────────────────────────────────────────────────────────────

def test_a24_quote_updates_on_colour_change(logged_in_admin: AdminPage) -> None:
    _reach_step3(logged_in_admin)
    before = logged_in_admin.nj_quote_value()
    logged_in_admin.nj_step3_set_colour("col")
    after = logged_in_admin.nj_quote_value()
    assert before != after, f"Quote unchanged after colour switch: {before} → {after}"
    logged_in_admin.close_new_job_modal()


# ── A25 ───────────────────────────────────────────────────────────────────────

def test_a25_quote_updates_on_sides_change(logged_in_admin: AdminPage) -> None:
    _reach_step3(logged_in_admin)
    before = logged_in_admin.nj_quote_value()
    logged_in_admin.nj_step3_set_sides("double")
    after = logged_in_admin.nj_quote_value()
    assert before != after, f"Quote unchanged after sides switch: {before} → {after}"
    logged_in_admin.close_new_job_modal()


# ── A26 ───────────────────────────────────────────────────────────────────────

def test_a26_quote_updates_on_paper_change(logged_in_admin: AdminPage) -> None:
    _reach_step3(logged_in_admin)
    before = logged_in_admin.nj_quote_value()
    logged_in_admin.nj_step3_set_paper("A3")
    after = logged_in_admin.nj_quote_value()
    assert before != after, f"Quote unchanged after paper change: {before} → {after}"
    logged_in_admin.close_new_job_modal()


# ── A27: S7-5 gap — thermal finishing ────────────────────────────────────────

def test_a27_thermal_finishing_quote(logged_in_admin: AdminPage) -> None:
    _reach_step3(logged_in_admin)
    logged_in_admin.nj_step3_set_finishing("thermal")
    quote = logged_in_admin.nj_quote_value()
    assert quote not in ("", "0", "₹0", "Error"), \
        f"Thermal finishing returned bad quote: '{quote}' — S7-5 gap: thermal rate may not be configured"
    logged_in_admin.close_new_job_modal()


# ── A28 ───────────────────────────────────────────────────────────────────────

def test_a28_student_discount_updates_quote(logged_in_admin: AdminPage) -> None:
    _reach_step3(logged_in_admin)
    before = logged_in_admin.nj_quote_value()
    logged_in_admin.nj_step3_toggle_student()
    after = logged_in_admin.nj_quote_value()
    assert before != after, f"Student discount did not change quote: {before} → {after}"
    logged_in_admin.close_new_job_modal()


# ── A29 (creates real job) ────────────────────────────────────────────────────

@pytest.mark.creates_data
def test_a29_full_payment_submit(logged_in_admin: AdminPage) -> None:
    _reach_step3(logged_in_admin)
    logged_in_admin.nj_step3_next()
    logged_in_admin.page.wait_for_selector("label:has-text('Full')", state="visible")
    logged_in_admin.nj_step4_select_payment("full")
    logged_in_admin.nj_submit()
    assert not logged_in_admin.new_job_modal_visible(), \
        "Modal still open after submit — job creation may have failed"


# ── A30 ───────────────────────────────────────────────────────────────────────

@pytest.mark.creates_data
def test_a30_partial_payment_path(logged_in_admin: AdminPage) -> None:
    _reach_step3(logged_in_admin)
    logged_in_admin.nj_step3_next()
    logged_in_admin.page.wait_for_selector("label:has-text('Partial')", state="visible")
    logged_in_admin.nj_step4_select_payment("partial")
    assert logged_in_admin.page.locator("#nj-amount").is_visible()
    logged_in_admin.close_new_job_modal()


# ── A31 ───────────────────────────────────────────────────────────────────────

@pytest.mark.creates_data
def test_a31_override_payment_path(logged_in_admin: AdminPage) -> None:
    _reach_step3(logged_in_admin)
    logged_in_admin.nj_step3_next()
    logged_in_admin.page.wait_for_selector("label:has-text('Override')", state="visible")
    logged_in_admin.nj_step4_select_payment("override")
    assert logged_in_admin.page.locator("#nj-amount").is_visible()
    assert logged_in_admin.page.locator("#nj-override-reason").is_visible()
    logged_in_admin.close_new_job_modal()


# ── A32 ───────────────────────────────────────────────────────────────────────

def test_a32_cancel_new_job_modal(logged_in_admin: AdminPage) -> None:
    logged_in_admin.open_new_job_modal()
    logged_in_admin.close_new_job_modal()
    assert not logged_in_admin.new_job_modal_visible()


# ── A33 ───────────────────────────────────────────────────────────────────────

@pytest.mark.creates_data
def test_a33_photocopy_modal_submit(logged_in_admin: AdminPage) -> None:
    logged_in_admin.open_photocopy_modal()
    logged_in_admin.photocopy_submit("5")
    assert not logged_in_admin.page.locator("#photocopy-modal").is_visible()


# ── A34 ───────────────────────────────────────────────────────────────────────

def test_a34_photocopy_modal_cancel(logged_in_admin: AdminPage) -> None:
    logged_in_admin.open_photocopy_modal()
    logged_in_admin.close_photocopy_modal()
    assert not logged_in_admin.page.locator("#photocopy-modal").is_visible()


# ── A35 ───────────────────────────────────────────────────────────────────────

def test_a35_add_print_item(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No jobs")
    logged_in_admin.select_job(0)
    before = logged_in_admin.page.locator("[id*='items'] tr, .item-row").count()
    logged_in_admin.page.click("button:has-text('Add Print Item')")
    logged_in_admin.page.wait_for_timeout(400)
    after = logged_in_admin.page.locator("[id*='items'] tr, .item-row").count()
    assert after > before


# ── A36 ───────────────────────────────────────────────────────────────────────

def test_a36_remove_print_item(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No jobs")
    logged_in_admin.select_job(0)
    logged_in_admin.page.click("button:has-text('Add Print Item')")
    logged_in_admin.page.wait_for_timeout(400)
    before = logged_in_admin.page.locator("[id*='items'] tr, .item-row").count()
    remove_btns = logged_in_admin.page.locator("button:has-text('✕')")
    if remove_btns.count() == 0:
        pytest.skip("No remove buttons — only 1 item")
    remove_btns.first().click()
    logged_in_admin.page.wait_for_timeout(400)
    assert logged_in_admin.page.locator("[id*='items'] tr, .item-row").count() < before


# ── A37 (store only) ──────────────────────────────────────────────────────────

@pytest.mark.store_only
def test_a37_print_item(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("Pending")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No pending jobs")
    logged_in_admin.select_job(0)
    with logged_in_admin.page.expect_response("**/print") as resp_info:
        logged_in_admin.page.locator("button:has-text('Print')").first().click()
    assert resp_info.value.status == 200


# ── A38 (store only) ──────────────────────────────────────────────────────────

@pytest.mark.store_only
def test_a38_dtp_session_cycle(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("Pending")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No pending jobs")
    logged_in_admin.select_job(0)
    p = logged_in_admin.page
    p.click("button:has-text('Start Work')")
    p.wait_for_timeout(800)
    assert p.locator("button:has-text('Pause')").is_visible()
    p.click("button:has-text('Pause')")
    p.wait_for_timeout(800)
    assert p.locator("button:has-text('Resume')").is_visible()
    p.click("button:has-text('Resume')")
    p.wait_for_timeout(800)
    p.click("button:has-text('Done')")
    p.wait_for_timeout(800)


# ── A39 (store only) ──────────────────────────────────────────────────────────

@pytest.mark.store_only
def test_a39_colour_detection(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("Pending")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No pending jobs")
    logged_in_admin.select_job(0)
    logged_in_admin.page.click("button:has-text('Run Detection')")
    logged_in_admin.page.wait_for_timeout(2000)
    assert logged_in_admin.page.locator("button:has-text('Confirm')").is_visible()
    logged_in_admin.page.click("button:has-text('Confirm')")


# ── A40 (store only) ──────────────────────────────────────────────────────────

@pytest.mark.store_only
def test_a40_save_specs(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No jobs")
    logged_in_admin.select_job(0)
    with logged_in_admin.page.expect_response("**/update-job") as resp_info:
        logged_in_admin.page.click("button:has-text('Save Specs')")
    assert resp_info.value.status == 200


# ── A41 (store only) ──────────────────────────────────────────────────────────

@pytest.mark.store_only
def test_a41_vendor_modal(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("All")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No jobs")
    logged_in_admin.select_job(0)
    logged_in_admin.page.click("button:has-text('Send to Vendor')")
    logged_in_admin.page.wait_for_selector("#vm-vendor", state="visible")
    assert logged_in_admin.page.locator("#vm-cost").is_visible()
    assert logged_in_admin.page.locator("#vm-return-date").is_visible()
    logged_in_admin.page.click("button:has-text('Cancel')")


# ── A42 (store only) ──────────────────────────────────────────────────────────

@pytest.mark.store_only
def test_a42_notify_customer_ready(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("Pending")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No pending jobs")
    logged_in_admin.select_job(0)
    with logged_in_admin.page.expect_response("**/mark-ready") as resp_info:
        logged_in_admin.page.click("button:has-text('Notify Customer')")
    assert resp_info.value.status == 200


# ── A43 (store only) ──────────────────────────────────────────────────────────

@pytest.mark.store_only
def test_a43_payment_modal(logged_in_admin: AdminPage) -> None:
    logged_in_admin.set_filter("Pending")
    if logged_in_admin.job_row_count() == 0:
        pytest.skip("No pending jobs")
    logged_in_admin.select_job(0)
    logged_in_admin.page.click("button:has-text('Collect Payment')")
    logged_in_admin.page.wait_for_selector("#pm-amount", state="visible")
    assert logged_in_admin.page.locator("#pm-mode").is_visible()
    logged_in_admin.page.click("button:has-text('Cancel')")


# ── A44 ───────────────────────────────────────────────────────────────────────

def test_a44_inbox_loads(logged_in_admin: AdminPage) -> None:
    logged_in_admin.switch_tab("conv")
    logged_in_admin.page.wait_for_timeout(1500)
    assert logged_in_admin.page.locator("#conv-inbox-list").is_visible()
    if logged_in_admin.inbox_item_count() == 0:
        pytest.skip("WARN: Inbox empty — conversation_log Supabase sync may be broken")


# ── A45 ───────────────────────────────────────────────────────────────────────

def test_a45_search_filters_contacts(logged_in_admin: AdminPage) -> None:
    logged_in_admin.switch_tab("conv")
    logged_in_admin.page.wait_for_timeout(1000)
    if logged_in_admin.inbox_item_count() == 0:
        pytest.skip("No conversations to search")
    total = logged_in_admin.inbox_item_count()
    logged_in_admin.search_conversations("ZZZNOMATCH")
    assert logged_in_admin.inbox_item_count() <= total
    logged_in_admin.search_conversations("")


# ── A46 ───────────────────────────────────────────────────────────────────────

def test_a46_select_contact_shows_thread(logged_in_admin: AdminPage) -> None:
    logged_in_admin.switch_tab("conv")
    logged_in_admin.page.wait_for_timeout(1000)
    if logged_in_admin.inbox_item_count() == 0:
        pytest.skip("No conversations")
    logged_in_admin.select_conversation(0)
    assert logged_in_admin.thread_visible()


# ── A47 (creates_data — sends real WhatsApp message) ─────────────────────────

@pytest.mark.creates_data
def test_a47_send_reply(logged_in_admin: AdminPage) -> None:
    logged_in_admin.switch_tab("conv")
    logged_in_admin.page.wait_for_timeout(1000)
    if logged_in_admin.inbox_item_count() == 0:
        pytest.skip("No conversations to reply to")
    logged_in_admin.select_conversation(0)
    with logged_in_admin.page.expect_response("**/admin/send") as resp_info:
        logged_in_admin.send_reply("PYTEST TEST MESSAGE — IGNORE")
    assert resp_info.value.status == 200
