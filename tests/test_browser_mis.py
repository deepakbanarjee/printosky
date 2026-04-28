"""Browser tests for mis.html — runs against live Netlify site."""
import pytest
from playwright.sync_api import Page
from tests.pages.mis_page import MISPage


@pytest.fixture
def mis(page: Page, base_url: str) -> MISPage:
    mp = MISPage(page)
    mp.url = f"{base_url}/mis.html"
    mp.navigate()
    return mp


@pytest.fixture
def logged_in_mis(mis: MISPage, mis_password: str) -> MISPage:
    mis.login(mis_password)
    assert mis.is_logged_in(), "MIS login failed — check PRINTOSKY_MIS_PASSWORD in .env"
    return mis


def test_m01_login_success(mis: MISPage, mis_password: str) -> None:
    mis.login(mis_password)
    assert mis.is_logged_in()


def test_m02_wrong_password(mis: MISPage) -> None:
    mis.login("wrongpassword999")
    assert not mis.is_logged_in()


def test_m03_logout(logged_in_mis: MISPage) -> None:
    logged_in_mis.logout()
    assert logged_in_mis.page.locator("#pw").is_visible()


def test_m04_refresh_loads_data(logged_in_mis: MISPage) -> None:
    logged_in_mis.refresh()
    assert logged_in_mis.is_logged_in()


def test_m05_konica_stats(logged_in_mis: MISPage) -> None:
    assert logged_in_mis.page.locator("#m-k-total").is_visible()
    assert logged_in_mis.konica_total() != "", \
        "Konica total blank — Supabase counter sync may be broken"


def test_m06_epson_stats(logged_in_mis: MISPage) -> None:
    assert logged_in_mis.page.locator("#m-e-total").is_visible()
    assert logged_in_mis.epson_total() != "", \
        "Epson total blank — Supabase counter sync may be broken"


def test_m07_konica_supplies(logged_in_mis: MISPage) -> None:
    assert logged_in_mis.konica_supplies_visible()


def test_m08_epson_supplies(logged_in_mis: MISPage) -> None:
    assert logged_in_mis.epson_supplies_visible()


def test_m09_supply_changes_panel(logged_in_mis: MISPage) -> None:
    assert logged_in_mis.page.locator("#supply-changes-panel").count() > 0


@pytest.mark.parametrize("period", ["today", "week", "month", "year"])
def test_m10_to_m13_konica_tabs(logged_in_mis: MISPage, period: str) -> None:
    logged_in_mis.set_konica_tab(period)
    assert logged_in_mis.kj_period_visible(period), \
        f"#kj-{period} not visible after clicking {period} tab"


@pytest.mark.parametrize("period", ["today", "week", "month"])
def test_m14_to_m16_staff_tabs(logged_in_mis: MISPage, period: str) -> None:
    logged_in_mis.set_staff_tab(period)
    assert logged_in_mis.sp_period_visible(period), \
        f"#sp-{period} not visible after clicking {period} staff tab"
