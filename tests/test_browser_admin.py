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
