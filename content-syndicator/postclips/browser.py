"""Lädt eine fertige MP4 über den lokal eingeloggten Browser hoch.

Passwörter werden nicht gelesen und nicht eingetippt. Fehlt eine Anmeldung,
bleibt das Fenster offen, bis du dich selbst einloggst.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from postclips.library import PLATFORM_ORDER, LoginRequired, discover_clips, run_posts

LOGIN_URL = {
    "youtube": ("accounts.google.com",),
    "tiktok": ("/login",),
    "instagram": ("/accounts/login",),
    "snapchat": ("accounts.snapchat.com",),
    "x": ("/i/flow/login", "/login"),
}

OPEN = {
    "youtube": "https://www.youtube.com/upload",
    "tiktok": "https://www.tiktok.com/tiktokstudio/upload?from=upload",
    "instagram": "https://www.instagram.com/",
    "snapchat": "https://profile.snapchat.com/",
    "x": "https://x.com/compose/post",
}


class StepError(RuntimeError):
    pass


def find_browser() -> str | None:
    """Installierten Chromium-Browser finden (CLIPS_BROWSER, sonst Chrome/Edge/Brave).

    Google lehnt Anmeldungen im Playwright-Chromium ("Chrome for Testing") ab.
    Mit dem echten Browser meldet sich der User ohne Automatisierung an
    (scripts/login.py), und das Posten nutzt dasselbe Programm mit demselben
    Profil, damit die Cookies lesbar bleiben. None = Playwright-Chromium.
    """
    env = os.environ.get("CLIPS_BROWSER")
    if env:
        return env if Path(env).expanduser().is_file() else None
    candidates: list[Path] = []
    if sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser"):
            for root in (Path("/Applications"), Path.home() / "Applications"):
                candidates.append(root / f"{app}.app" / "Contents" / "MacOS" / app)
    elif os.name == "nt":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                candidates += [
                    Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe",
                    Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                    Path(base) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
                ]
    else:
        for name in ("google-chrome", "chromium", "microsoft-edge", "brave-browser", "brave"):
            hit = shutil.which(name)
            if hit:
                candidates.append(Path(hit))
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


class Session:
    def __init__(self, page, login_timeout: int) -> None:
        self.page = page
        self.login_timeout = login_timeout

    def post(self, job) -> None:
        {
            "youtube": self.youtube,
            "tiktok": self.tiktok,
            "instagram": self.instagram,
            "snapchat": self.snapchat,
            "x": self.x,
        }[job.platform](job)

    def youtube(self, job) -> None:
        self.open("youtube")
        self.give_video(job.video)
        title = self.page.locator("#title-textarea #textbox").first
        title.wait_for(state="visible", timeout=120_000)
        self.replace(title, job.texts["youtube_title"])
        description = self.page.locator("#description-textarea #textbox").first
        if job.texts["youtube_description"]:
            self.replace(description, job.texts["youtube_description"])
        self.page.locator(
            'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"], '
            'tp-yt-paper-radio-button[name="NOT_MADE_FOR_KIDS"]'
        ).first.click(force=True)
        for _ in range(3):
            self.page.locator("#next-button").click()
            self.page.wait_for_timeout(600)
        self.page.locator('tp-yt-paper-radio-button[name="PUBLIC"]').click(force=True)
        self.page.locator("#done-button").click(timeout=180_000)
        self.click_if_visible('ytcp-button#dialog-button, #confirm-button', 4_000)

    def tiktok(self, job) -> None:
        self.open("tiktok")
        root = self.root()
        self.give_video(job.video, root)
        editor = root.locator('div[contenteditable="true"]').first
        editor.wait_for(state="visible", timeout=120_000)
        editor.click()
        self.select_all()
        self.page.keyboard.press("Backspace")
        self.page.keyboard.insert_text(job.texts["tiktok"])
        self.page.keyboard.press("Escape")
        button = root.locator('button[data-e2e="post_video_button"]').first
        button.wait_for(state="visible", timeout=30_000)
        root.wait_for_function(
            """() => {
                const b = document.querySelector('button[data-e2e="post_video_button"]');
                return !!b && b.getAttribute('aria-disabled') !== 'true' && !b.disabled;
            }""",
            timeout=180_000,
        )
        button.click()
        self.page.wait_for_url("**/tiktokstudio/content**", timeout=120_000)

    def instagram(self, job) -> None:
        self.open("instagram")
        self.dismiss_noise()
        self.click_first([
            '[aria-label="New post"]',
            '[aria-label="Neuer Beitrag"]',
            '[aria-label="Create"]',
            '[aria-label="Erstellen"]',
        ])
        self.click_if_visible('text=/^Reel$/', 3_000)
        self.give_video(job.video)
        self.click_named(["Next", "Weiter"])
        self.click_if_visible(
            'button:has-text("Next"), button:has-text("Weiter"), '
            'div[role="button"]:has-text("Next"), div[role="button"]:has-text("Weiter")',
            5_000,
        )
        caption = self.page.locator(
            '[aria-label="Write a caption..."], [aria-label="Caption schreiben..."], '
            '[aria-label="Bildunterschrift schreiben..."], textarea'
        ).first
        caption.wait_for(state="visible", timeout=60_000)
        self.replace(caption, job.texts["instagram"])
        self.click_named(["Share", "Teilen"])

    def snapchat(self, job) -> None:
        self.open("snapchat")
        self.give_video(job.video)
        self.page.get_by_text("Spotlight", exact=False).first.click(timeout=60_000)
        field = self.page.locator("textarea, input[type='text']").first
        if field.count():
            self.replace(field, job.texts["snapchat"])
        self.click_if_visible('text=/Creator Terms|Nutzungsbedingungen|I agree|Ich stimme/i', 3_000)
        self.click_named(["Post to Snapchat", "Posten", "Post"])

    def x(self, job) -> None:
        self.open("x")
        self.page.locator("input[data-testid='fileInput'], input[type='file']").first.set_input_files(str(job.video))
        box = self.page.locator("[data-testid='tweetTextarea_0']").first
        box.wait_for(state="visible", timeout=30_000)
        self.replace(box, job.texts["x"])
        self.page.locator("[data-testid='tweetButton']:not([aria-disabled='true'])").click(timeout=180_000)

    def open(self, platform: str) -> None:
        self.page.goto(OPEN[platform], wait_until="domcontentloaded")
        self.page.wait_for_timeout(800)
        self.wait_past_login(platform)
        if not self._on_target(platform):
            self.page.goto(OPEN[platform], wait_until="domcontentloaded")
            self.wait_past_login(platform)

    def wait_past_login(self, platform: str) -> None:
        told = False
        deadline = time.monotonic() + self.login_timeout
        while self._login_wall(platform):
            if not told:
                print(f"{platform}: im Fenster anmelden. Das Skript wartet.", file=sys.stderr)
                told = True
            if time.monotonic() > deadline:
                raise LoginRequired(f"{platform}: Anmeldung fehlt.")
            self.page.wait_for_timeout(1000)

    def root(self):
        """TikTok legt die Upload-Seite oft in ein iframe."""
        if self.page.locator("input[type='file']").count():
            return self.page
        for frame in self.page.frames:
            try:
                if frame.locator("input[type='file']").count():
                    return frame
            except Exception:
                continue
        return self.page

    def give_video(self, video: Path, root=None) -> None:
        root = root or self.root()
        inputs = root.locator("input[type='file']")
        if inputs.count():
            inputs.first.set_input_files(str(video))
            return
        try:
            with self.page.expect_file_chooser(timeout=8_000) as chooser:
                self.click_first([
                    'text="Select from computer"',
                    'text="Vom Computer auswählen"',
                    'button:has-text("Choose video")',
                    'button:has-text("Video auswählen")',
                ], timeout=8_000)
            chooser.value.set_files(str(video))
        except StepError as exc:
            raise StepError(f"Keine Datei-Auswahl für {video.name}.") from exc

    def replace(self, locator, text: str) -> None:
        locator.click()
        self.select_all()
        self.page.keyboard.press("Backspace")
        self.page.keyboard.insert_text(text)

    def select_all(self) -> None:
        key = "Meta" if sys.platform == "darwin" else "Control"
        self.page.keyboard.press(f"{key}+A")

    def click_first(self, selectors: list[str], timeout: int = 20_000) -> None:
        each = max(1_000, timeout // max(1, len(selectors)))
        errors: list[str] = []
        for selector in selectors:
            try:
                self.page.locator(selector).first.click(timeout=each)
                return
            except Exception as exc:
                errors.append(f"{selector}: {exc.__class__.__name__}")
        raise StepError("Kein Button. " + "; ".join(errors))

    def click_named(self, names: list[str]) -> None:
        self.click_first([f'button:has-text("{name}")' for name in names] + [
            f'div[role="button"]:has-text("{name}")' for name in names
        ])

    def click_if_visible(self, selector: str, timeout: int) -> None:
        try:
            self.page.locator(selector).first.click(timeout=timeout)
        except Exception:
            return

    def dismiss_noise(self) -> None:
        for selector in (
            'button:has-text("Not now")',
            'button:has-text("Jetzt nicht")',
            'button:has-text("Allow all cookies")',
            'button:has-text("Alle Cookies erlauben")',
            'button:has-text("Alle akzeptieren")',
        ):
            self.click_if_visible(selector, 1_200)

    def _login_wall(self, platform: str) -> bool:
        url = self.page.url
        return any(part in url for part in LOGIN_URL[platform])

    def _on_target(self, platform: str) -> bool:
        url = self.page.url
        host = {
            "youtube": "youtube.com",
            "tiktok": "tiktok.com",
            "instagram": "instagram.com",
            "snapchat": "snapchat.com",
            "x": "x.com",
        }[platform]
        return host in url and not self._login_wall(platform)


def publish(home: Path, vod_id: str, platforms: list[str], only: set[str] | None,
            headless: bool, login_timeout: int):
    clips = discover_clips(home, vod_id, only)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("playwright fehlt. uv run installiert es mit dem Skript.") from exc
    profile = home / "state" / "browser"
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                executable_path=find_browser(),
                # Echter Browser: Cookies sind mit dem Schlüsselbund verschlüsselt.
                # Playwrights Mock-Keychain könnte sie nicht lesen.
                ignore_default_args=["--use-mock-keychain"] if find_browser() else None,
                headless=headless,
                viewport={"width": 1400, "height": 900},
                locale="de-DE",
            )
        except Exception as exc:
            if "Executable doesn't exist" in str(exc) or "BrowserType.launch" in str(exc):
                raise SystemExit(
                    "Chromium fehlt. Einmal: uv run --with playwright python -m playwright install chromium"
                ) from exc
            raise
        page = context.pages[0] if context.pages else context.new_page()
        try:
            return run_posts(clips, platforms, Session(page, login_timeout).post)
        finally:
            context.close()


def known_platforms(raw: str) -> list[str]:
    wanted = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = [part for part in wanted if part not in PLATFORM_ORDER]
    if unknown:
        raise ValueError(f"Unbekannte Plattform: {', '.join(unknown)}. Erlaubt: {', '.join(PLATFORM_ORDER)}.")
    return [part for part in PLATFORM_ORDER if part in wanted]
