from pathlib import Path
import re
import sys

from playwright.sync_api import TimeoutError as BrowserTimeout
from h import connect, get_page, mark_submission
from publish_status import normalize

vod, clip = sys.argv[1:3]
base = Path.home() / 'clips/out' / vod / clip
caption = base.with_suffix('.txt').read_text().split('--- TIKTOK ---', 1)[1].split('---', 1)[0].strip()
_, _, ctx = connect()
page = get_page(ctx, 'tiktok')
page.goto('https://www.tiktok.com/tiktokstudio/upload', wait_until='domcontentloaded', timeout=30000)
page.locator('input[type=file]').first.set_input_files(str(base.with_suffix('.mp4')), timeout=30000)
# Wait for controls rather than guessing how long this upload needs.
editor = page.locator('div[contenteditable="true"]').first
editor.wait_for(state='visible', timeout=60000)
hint = page.get_by_role('dialog').get_by_role('button', name='Got it', exact=True)
if hint.count() == 1 and hint.is_visible():
    hint.click()
editor.fill(caption)
editor.press('Tab')
if normalize(editor.inner_text(timeout=5000)) != normalize(caption):
    raise RuntimeError('TikTok caption differs from the intended caption')
button = page.locator('button[data-e2e="post_video_button"]')
button.click(trial=True, timeout=90000)
mark_submission()
button.click(timeout=5000)
# Never dismiss a content-check or duplicate prompt blindly.
try:
    page.wait_for_url(re.compile(r'/tiktokstudio/content(?:[/?]|$)'), timeout=10000)
except BrowserTimeout:
    print('TikTok did not navigate to its posts list; checking readback separately.')