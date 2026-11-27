"""Offline browser tests for TikTok's post-confirmation dialog."""
import ast
from importlib import import_module
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TITLE = 'Continue to post?'
PENDING = ("We're still checking your video for potential issues. "
           'Do you want to continue posting before the check is complete?')


def modal(title=TITLE, message=PENDING, extra=''):
    return (f'<section role="dialog" aria-labelledby="title">'
            f'<div id="title">{title}</div><div>{message}</div>{extra}'
            '<button>Cancel</button><button onclick="window.posts++">Post now</button>'
            '</section>')


class TikTokDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            launch = import_module('playwright.sync_api').sync_playwright
        except ImportError as exc:
            raise unittest.SkipTest('Playwright required for browser fixtures') from exc
        cls.popup = staticmethod(import_module('tiktok_popup').confirm_pending_check)
        cls.playwright = launch().start()
        cls.addClassCleanup(cls.playwright.stop)
        cls.browser = cls.playwright.chromium.launch(channel='chrome')
        cls.addClassCleanup(cls.browser.close)

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        # Every request is fulfilled locally, including the upload and posts URLs.
        self.page.route('**/*', lambda route: route.fulfill(body='<html></html>', content_type='text/html'))
        self.page.goto('https://www.tiktok.com/tiktokstudio/upload')
        self.page.evaluate('window.posts = 0')

    def test_waits_for_delayed_pending_check_and_clicks_once(self):
        self.page.evaluate('(html) => setTimeout(() => document.body.innerHTML = html, 200)', modal())
        self.assertTrue(self.popup(self.page, timeout=3000))
        self.assertEqual(self.page.evaluate('window.posts'), 1)

    def test_caption_replaces_delayed_draft_filename(self):
        # Controlled editor restores its existing value when an insertion arrives
        # before a separate deletion, reproducing the captured clip01 prefix.
        self.page.set_content('''<div role="combobox" contenteditable="true"><div data-contents="true">clip01</div></div>
            <script>
            const editor = document.querySelector('[contenteditable]');
            editor.addEventListener('beforeinput', e => {
              if (e.inputType === 'insertText' && editor.innerText === 'clip01') {
                e.preventDefault(); editor.innerText += e.data;
              }
            });
            </script>''')
        src = (Path(__file__).resolve().parents[1] / 'ttu.py').read_text()
        body = ast.parse(src).body
        start = next(i for i, n in enumerate(body) if isinstance(n, ast.If)
                     and 'hint.count()' in ast.unparse(n.test)) + 1
        end = next(i for i, n in enumerate(body) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'button' for t in n.targets))
        from publish_status import normalize
        env = dict(page=self.page, editor=self.page.get_by_role('combobox'), caption='Ein Test #gta6', normalize=normalize)
        exec(compile(ast.Module(body=body[start:end], type_ignores=[]), '<caption-write>', 'exec'), env)
        self.assertEqual(env['editor'].inner_text(), env['caption'])

    def test_does_not_touch_warning_or_ambiguous_popup(self):
        for html in (modal(title='Duplicate video'), modal(message='Copyright infringement detected.'),
                     modal(extra='<div>Audio copyright issue detected.</div>'),
                     modal() + modal(title='Policy warning')):
            with self.subTest(html=html):
                self.page.set_content(html)
                self.assertFalse(self.popup(self.page, timeout=1000))
                self.assertEqual(self.page.evaluate('window.posts'), 0)

    def test_does_not_click_buttons_after_direct_redirect(self):
        self.page.evaluate("history.pushState({}, '', '/tiktokstudio/content?tab=video')")
        self.page.set_content(modal())
        self.assertFalse(self.popup(self.page, timeout=1000))
        self.assertEqual(self.page.evaluate('window.posts'), 0)

    def test_does_not_click_confirmation_from_another_origin(self):
        self.page.goto('https://example.test/tiktokstudio/upload')
        self.page.evaluate('window.posts = 0')
        self.page.set_content(modal())
        self.assertFalse(self.popup(self.page, timeout=1000))
        self.assertEqual(self.page.evaluate('window.posts'), 0)
