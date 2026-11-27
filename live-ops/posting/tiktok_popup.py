"""Handle the exact TikTok Studio confirmation for a check still in progress."""
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as BrowserTimeout

from publish_status import normalize

TITLE = 'Continue to post?'
CHECK_PENDING = ("We're still checking your video for potential issues. "
                 'Do you want to continue posting before the check is complete?')


def on_upload(url):
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.netloc == 'www.tiktok.com'
            and parsed.path.rstrip('/') == '/tiktokstudio/upload')


def confirm_pending_check(page, *, timeout=30000):
    """Confirm once, only when the upload page shows the known pending-check dialog."""
    if not on_upload(page.url):
        return False
    try:
        page.wait_for_function('''() => {
            if (location.origin !== 'https://www.tiktok.com' ||
                !/^\\/tiktokstudio\\/upload\\/?$/.test(location.pathname)) return true;
            return Array.from(document.querySelectorAll('[role="dialog"], dialog[open]'))
                .some(el => el.getClientRects().length && getComputedStyle(el).visibility === 'visible');
        }''', timeout=timeout)
    except BrowserTimeout:
        return False
    dialogs = page.get_by_role('dialog').all()
    visible = [dialog for dialog in dialogs if dialog.is_visible()]
    expected = page.get_by_role('dialog', name=TITLE, exact=True)
    if not on_upload(page.url) or len(visible) != 1 or expected.count() != 1 or not expected.is_visible():
        return False
    # Text on adjacent buttons has no guaranteed separator in innerText.
    text = expected.evaluate('''root => {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        const parts = [];
        while (walker.nextNode()) {
            const node = walker.currentNode;
            const parent = node.parentElement;
            if (parent.getClientRects().length && getComputedStyle(parent).visibility === 'visible') {
                parts.push(node.textContent);
            }
        }
        return parts.join(' ');
    }''', timeout=3000)
    if normalize(text) != f'{TITLE} {CHECK_PENDING} Cancel Post now':
        return False
    button = expected.get_by_role('button', name='Post now', exact=True)
    cancel = expected.get_by_role('button', name='Cancel', exact=True)
    if button.count() != 1 or cancel.count() != 1 or not button.is_enabled() or not on_upload(page.url):
        return False
    button.click(timeout=3000)
    return True
