from __future__ import annotations
from playwright.sync_api import Page


class MISPage:
    url = "https://printosky.com/mis.html"

    def __init__(self, page: Page) -> None:
        self.page = page

    def navigate(self) -> None:
        self.page.goto(self.url)
        self.page.wait_for_load_state("networkidle")

    def login(self, password: str) -> None:
        self.page.fill("#pw", password)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(1500)

    def logout(self) -> None:
        self.page.click("button:has-text('Logout')")
        self.page.wait_for_selector("#pw", state="visible")

    def is_logged_in(self) -> bool:
        return self.page.locator("#m-k-total").is_visible()

    def login_error_visible(self) -> bool:
        err = self.page.locator("[id*='err'], .error, .login-err")
        return err.is_visible() if err.count() > 0 else False

    def refresh(self) -> None:
        self.page.click("button:has-text('Refresh')")
        self.page.wait_for_load_state("networkidle")

    def set_konica_tab(self, period: str) -> None:
        """period: 'today', 'week', 'month', 'year'"""
        self.page.click(f"button:has-text('{period.capitalize()}')")
        self.page.wait_for_selector(f"#kj-{period}", state="visible")

    def set_staff_tab(self, period: str) -> None:
        """period: 'today', 'week', 'month'"""
        self.page.click(f"button:has-text('{period.capitalize()}')")
        self.page.wait_for_selector(f"#sp-{period}", state="visible")

    def konica_total(self) -> str:
        return self.page.locator("#m-k-total").inner_text().strip()

    def epson_total(self) -> str:
        return self.page.locator("#m-e-total").inner_text().strip()

    def konica_supplies_visible(self) -> bool:
        return self.page.locator("#konica-supplies").is_visible()

    def epson_supplies_visible(self) -> bool:
        return self.page.locator("#epson-supplies").is_visible()

    def supply_changes_visible(self) -> bool:
        return self.page.locator("#supply-changes-panel").is_visible()

    def kj_period_visible(self, period: str) -> bool:
        return self.page.locator(f"#kj-{period}").is_visible()

    def sp_period_visible(self, period: str) -> bool:
        return self.page.locator(f"#sp-{period}").is_visible()
