from __future__ import annotations
from playwright.sync_api import Page


class AdminPage:
    url = "https://printosky.com/admin.html"

    def __init__(self, page: Page) -> None:
        self.page = page

    # ── Auth ──────────────────────────────────────────────────────────────

    def navigate(self) -> None:
        self.page.goto(self.url)
        self.page.wait_for_load_state("networkidle")

    def login_pin(self, pin: str) -> None:
        self.page.fill("#pin-input", pin)
        self.page.click("button:has-text('ENTER')")
        self.page.wait_for_timeout(1500)

    def login_admin(self, password: str) -> None:
        self.page.click("text=Admin override")
        self.page.wait_for_selector("#admin-override-form", state="visible")
        self.page.fill("#admin-override-form #pw", password)
        self.page.click("#admin-override-form button:has-text('ENTER')")
        self.page.wait_for_timeout(1500)

    def logout(self) -> None:
        self.page.click("button:has-text('Logout')")
        self.page.wait_for_selector("#pin-input", state="visible")

    def is_logged_in(self) -> bool:
        return self.page.locator("#s-jobs").is_visible()

    def login_error_visible(self) -> bool:
        return self.page.locator("#login-err").is_visible()

    # ── Navigation ────────────────────────────────────────────────────────

    def switch_tab(self, name: str) -> None:
        """name: 'jobs' or 'conv'"""
        self.page.click(f"button:has-text('{name.capitalize()}')")
        self.page.wait_for_timeout(500)

    def refresh(self) -> None:
        self.page.click("button:has-text('Refresh')")
        self.page.wait_for_load_state("networkidle")

    # ── Job Table ─────────────────────────────────────────────────────────

    def set_filter(self, label: str) -> None:
        """label: 'All', 'Completed', 'Pending', 'Today'"""
        self.page.click(f"button:has-text('{label}')")
        self.page.wait_for_timeout(400)

    def search_jobs(self, text: str) -> None:
        self.page.fill("#search", text)
        self.page.wait_for_timeout(400)

    def job_row_count(self) -> int:
        return self.page.locator("table tbody tr").count()

    def select_job(self, index: int = 0) -> None:
        self.page.locator("table tbody tr").nth(index).click()
        self.page.wait_for_timeout(800)

    def detail_panel_visible(self) -> bool:
        return self.page.locator("#job-panel").is_visible()

    # ── New Job Modal ─────────────────────────────────────────────────────

    def open_new_job_modal(self) -> None:
        self.page.keyboard.press("n")
        self.page.wait_for_selector("#newjob-modal", state="visible")

    def nj_step1_fill(self, name: str, phone: str, source: str = "walk-in") -> None:
        self.page.fill("#nj-name", name)
        self.page.fill("#nj-phone", phone)
        self.page.select_option("#nj-source", source)
        self.page.click("button:has-text('Next')")
        self.page.wait_for_timeout(500)

    def nj_step2_skip(self) -> None:
        self.page.click("button:has-text('Skip')")
        self.page.wait_for_timeout(500)

    def nj_step3_set_colour(self, value: str) -> None:
        """value: 'bw', 'col'"""
        self.page.select_option("#nj-colour", value)
        self.page.wait_for_timeout(600)

    def nj_step3_set_sides(self, value: str) -> None:
        """value: 'single', 'double'"""
        self.page.select_option("#nj-sides", value)
        self.page.wait_for_timeout(600)

    def nj_step3_set_paper(self, value: str) -> None:
        self.page.select_option("#nj-paper", value)
        self.page.wait_for_timeout(600)

    def nj_step3_set_finishing(self, value: str) -> None:
        self.page.select_option("#nj-finishing", value)
        self.page.wait_for_timeout(600)

    def nj_step3_toggle_student(self) -> None:
        self.page.click("#nj-student")
        self.page.wait_for_timeout(600)

    def nj_quote_value(self) -> str:
        return self.page.locator("#nj-quote-val").inner_text().strip()

    def nj_step3_next(self) -> None:
        self.page.click("button:has-text('Next')")
        self.page.wait_for_timeout(500)

    def nj_step4_select_payment(self, option: str) -> None:
        """option: 'full', 'partial', 'override'"""
        self.page.click(f"label:has-text('{option.capitalize()}')")
        self.page.wait_for_timeout(300)

    def nj_submit(self) -> None:
        self.page.click("button:has-text('Add to Queue')")
        self.page.wait_for_timeout(1500)

    def close_new_job_modal(self) -> None:
        self.page.click("button:has-text('Cancel')")
        self.page.wait_for_timeout(400)

    def new_job_modal_visible(self) -> bool:
        return self.page.locator("#newjob-modal").is_visible()

    # ── Photocopy Modal ───────────────────────────────────────────────────

    def open_photocopy_modal(self) -> None:
        self.page.click("button:has-text('Photocopy')")
        self.page.wait_for_selector("#photocopy-modal", state="visible")

    def photocopy_submit(self, cost: str) -> None:
        self.page.fill("#pc-cost", cost)
        self.page.click("button:has-text('Add & Complete')")
        self.page.wait_for_timeout(1000)

    def close_photocopy_modal(self) -> None:
        self.page.click("#photocopy-modal button:has-text('Cancel')")
        self.page.wait_for_timeout(400)

    # ── Conversations Tab ─────────────────────────────────────────────────

    def search_conversations(self, text: str) -> None:
        self.page.fill("#conv-search", text)
        self.page.wait_for_timeout(400)

    def inbox_item_count(self) -> int:
        return self.page.locator("#conv-inbox-list > *").count()

    def select_conversation(self, index: int = 0) -> None:
        self.page.locator("#conv-inbox-list > *").nth(index).click()
        self.page.wait_for_timeout(600)

    def thread_visible(self) -> bool:
        return self.page.locator("#conv-thread-view").is_visible()

    def send_reply(self, message: str) -> None:
        self.page.fill("#conv-reply-input", message)
        self.page.keyboard.press("Enter")
        self.page.wait_for_timeout(1000)
