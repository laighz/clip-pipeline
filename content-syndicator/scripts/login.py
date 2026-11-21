# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.49"]
# ///
"""Anmeldungen im Browser-Profil vorbereiten oder prüfen.

Ohne Option: startet den installierten Browser (Chrome/Edge/Brave, oder
CLIPS_BROWSER) ganz normal, ohne Automatisierung, mit dem Profil unter
<CLIPS_HOME>/state/browser und je einem Tab pro Plattform. Du meldest dich selbst
an und beendest den Browser (Cmd+Q). Passwörter werden nicht gelesen und nicht
eingetippt. Google lehnt Anmeldungen in einem automatisierten Browser ab, deshalb
kein Playwright für diesen Schritt.

--check: ohne Fenster nachsehen, welche Plattform noch eine Anmeldung will.
Ausgabe: eine JSON-Zeile. Exit 0 = alle angemeldet, 5 = mindestens eine fehlt,
4 = Chromium fehlt.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from postclips.browser import LOGIN_URL, OPEN, REAL_BROWSER_PROFILE, find_browser, known_platforms  # noqa: E402
from postclips.library import PLATFORM_ORDER  # noqa: E402


def launch(playwright, profile: Path, headless: bool):
    try:
        return playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=find_browser(),
            ignore_default_args=[REAL_BROWSER_PROFILE] if find_browser() else None,
            headless=headless,
            viewport={"width": 1400, "height": 900},
            locale="de-DE",
        )
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            print(json.dumps({"ok": False, "error": "Chromium fehlt. Einmal: "
                              "uv run --with playwright python -m playwright install chromium"}))
            raise SystemExit(4) from exc
        if "ProcessSingleton" in str(exc) or "SingletonLock" in str(exc):
            print(json.dumps({"ok": False, "error": "Profil ist schon in einem offenen Fenster in Benutzung"}))
            raise SystemExit(2) from exc
        raise


def needs_login(url: str, platform: str) -> bool:
    return any(part in url for part in LOGIN_URL[platform])


# Sitzungs-Cookies je Plattform (Domain-Endung, Name). Nur Name und Ablauf werden
# angesehen, nie der Wert. Die URL allein reicht nicht: Instagram, X und Snapchat
# leiten ohne Anmeldung nicht immer auf eine Login-URL um.
SESSION_COOKIE = {
    "youtube": [("youtube.com", "SID"), ("youtube.com", "__Secure-3PSID")],
    "tiktok": [("tiktok.com", "sessionid")],
    "instagram": [("instagram.com", "sessionid")],
    "x": [("x.com", "auth_token")],
    # Erst nach der Anmeldung da; EssentialSession/_sc-sid setzt Snapchat auch ohne.
    "snapchat": [("accounts.snapchat.com", "__Host-sc-a-auth-session")],
}


def has_session_cookie(cookies: list[dict], platform: str) -> bool:
    now = time.time()
    for domain, name in SESSION_COOKIE[platform]:
        for c in cookies:
            if c["name"] == name and c["domain"].lstrip(".").endswith(domain) \
                    and (c.get("expires", -1) in (-1, None) or c["expires"] > now):
                return True
    return False


def check(context, platforms: list[str]) -> dict:
    result = {}
    cookies = context.cookies()
    for platform in platforms:
        if platform in SESSION_COOKIE:
            result[platform] = {"logged_in": has_session_cookie(cookies, platform), "via": "cookie"}
            continue
        page = context.new_page()
        try:
            page.goto(OPEN[platform], wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(5_000)  # Weiterleitungen zur Login-Seite abwarten
            url = page.url
            login_button = page.get_by_role("button", name="Log in").or_(
                page.get_by_role("link", name="Log in")).or_(
                page.get_by_role("button", name="Anmelden")).or_(
                page.get_by_role("link", name="Anmelden"))
            wall = needs_login(url, platform) or login_button.count() > 0
            empty = not page.inner_text("body").strip()
            entry = {"logged_in": not wall and not empty, "via": "seite", "url": url.split("?")[0]}
            if empty and not wall:
                entry["note"] = "Seite leer geladen, Status unklar"
            result[platform] = entry
        except Exception as exc:  # noqa: BLE001
            result[platform] = {"logged_in": False, "error": f"{exc.__class__.__name__}: {str(exc)[:200]}"}
        finally:
            page.close()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browser-Anmeldungen fürs Posten vorbereiten oder prüfen.")
    parser.add_argument("--platforms", default=",".join(PLATFORM_ORDER),
                        help="kommagetrennt, Standard: alle")
    parser.add_argument("--check", action="store_true", help="ohne Fenster prüfen, nichts öffnen")
    parser.add_argument("--home", default=os.environ.get("CLIPS_HOME", ""),
                        help="Datenordner, Standard ~/clips")
    args = parser.parse_args(argv)

    platforms = known_platforms(args.platforms)
    home = Path(args.home).expanduser() if args.home else Path.home() / "clips"
    profile = home / "state" / "browser"
    profile.mkdir(parents=True, exist_ok=True)

    if not args.check:
        exe = find_browser()
        if exe:
            urls = [OPEN[p] for p in platforms]
            print(f"[login] {Path(exe).name}: {len(urls)} Tabs offen. Selbst anmelden, "
                  "dann den Browser beenden (Cmd+Q).", file=sys.stderr)
            subprocess.run([exe, f"--user-data-dir={profile}", "--no-first-run",
                            "--no-default-browser-check", *urls])
            print(json.dumps({"ok": True, "browser": exe, "profile": str(profile),
                              "next": "uv run scripts/login.py --check"}, ensure_ascii=False))
            return 0
        print("[login] Kein Brave/Chrome/Edge gefunden, nehme Playwright-Chromium. "
              "Google-Anmeldung kann dort abgelehnt werden.", file=sys.stderr)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        if args.check:
            context = launch(playwright, profile, headless=True)
            try:
                status = check(context, platforms)
            finally:
                context.close()
            missing = [p for p, s in status.items() if not s["logged_in"]]
            print(json.dumps({"ok": not missing, "missing": missing, "platforms": status}, ensure_ascii=False))
            return 0 if not missing else 5

        context = launch(playwright, profile, headless=False)
        first = context.pages[0] if context.pages else context.new_page()
        for i, platform in enumerate(platforms):
            page = first if i == 0 else context.new_page()
            try:
                page.goto(OPEN[platform], wait_until="domcontentloaded", timeout=45_000)
            except Exception:  # noqa: BLE001
                pass  # Tab bleibt offen, der User kann neu laden
        print(f"[login] {len(platforms)} Tabs offen. Selbst anmelden, dann das Fenster schließen.",
              file=sys.stderr)
        try:
            while context.pages:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        try:
            context.close()
        except Exception:  # noqa: BLE001
            pass
    print(json.dumps({"ok": True, "profile": str(profile),
                      "next": "uv run scripts/login.py --check"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
