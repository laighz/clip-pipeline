"""Regression cases for the Snapchat destination form (requires Playwright)."""
import ast
from pathlib import Path
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def checkbox(id_, label, state):
    return (f'<div><button id="{id_}" type="button" role="checkbox" aria-checked="{state}">□</button>'
            f'<input type="checkbox" aria-hidden="true"><label for="{id_}"><span>{label}</span>'
            '<div>Helper text</div></label></div>')


def form(spotlight='true', story='false', saved='false', extra=''):
    return ('<div><div>Choose your posting destination</div>'
            + checkbox('spotlight', 'Post to Spotlight', spotlight)
            + checkbox('publicStory', 'Post to Public Story', story) + extra + '</div>'
            + '<div><div>Save to your Public Profile</div>'
            + checkbox('publicProfile', 'Save to a Public Profile', saved) + '</div>')


@unittest.skipIf(sync_playwright is None, 'Playwright required for browser fixtures')
class SpotlightBrowserTest(unittest.TestCase):
    def test_enabling_spotlight_clears_any_additional_destination_default(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'scu.py').read_text())
        selection = next(n for n in tree.body if isinstance(n, ast.For))
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            page = browser.new_page()
            page.set_content(form(spotlight='false'))
            page.evaluate('''() => {
                const saved = document.querySelector('#publicProfile');
                document.querySelector('#spotlight').onclick = e => {
                    e.currentTarget.setAttribute('aria-checked', 'true');
                    saved.setAttribute('aria-checked', 'true');
                };
                saved.onclick = () => saved.setAttribute('aria-checked', 'false');
            }''')
            exec(compile(ast.Module(body=[selection], type_ignores=[]), '<select destinations>', 'exec'), {'page': page})
            self.assertEqual(page.locator('#publicProfile').get_attribute('aria-checked'), 'false')
            browser.close()

    def test_requires_spotlight_and_excludes_story_saved_profile_and_unknown(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'scu.py').read_text())
        check = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == 'spotlight_only' for t in n.targets))
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            page = browser.new_page()
            fixtures = [(form(), True), (form(story='true'), False), (form(saved='true'), False),
                        (form(spotlight='false'), False), (form(saved='mixed'), False),
                        (form(extra=checkbox('other', 'Unknown', 'true')), False),
                        (form().replace('id="publicProfile"', 'id="missing"'), False)]
            for html, expected in fixtures:
                with self.subTest(html=html):
                    page.set_content(html)
                    self.assertEqual(page.evaluate(check), expected)
            browser.close()
