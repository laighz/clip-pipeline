from pathlib import Path
import re
import sys

from playwright.sync_api import TimeoutError as BrowserTimeout
from h import connect, get_page, mark_submission, record_proof
from publish_status import normalize, spotlight_receipt

vod, clip = sys.argv[1:3]
base = Path.home() / 'clips/out' / vod / clip
text = base.with_suffix('.txt').read_text()
block = '--- SNAPCHAT ---' if '--- SNAPCHAT ---' in text else '--- TIKTOK ---'
caption = text.split(block, 1)[1].split('---', 1)[0].strip()
_, _, ctx = connect()
page = get_page(ctx, 'snapchat.com')
page.goto('https://profile.snapchat.com/', wait_until='domcontentloaded', timeout=30000)
page.get_by_text('Post to Snapchat', exact=True).click(timeout=30000)
page.locator('input[type=file]').first.set_input_files(str(base.with_suffix('.mp4')), timeout=30000)
dest = page.get_by_text('Post to Spotlight', exact=True)
dest.wait_for(state='visible', timeout=60000)
spotlight_only = '''() => {
    const spotlight = /^(?:Post to )?Spotlight$/i;
    const story = /^(?:Post to )?(?:Public |My )?Stor(?:y|ies)$/i;
    const controls = '[aria-selected], [aria-checked], [aria-pressed], input[type="radio"], input[type="checkbox"]';
    const name = e => (e.getAttribute('aria-label') || e.labels?.[0]?.textContent || e.textContent).trim();
    const state = e => e.getAttribute('aria-checked') ?? e.getAttribute('aria-selected') ??
        e.getAttribute('aria-pressed') ?? (e instanceof HTMLInputElement ? String(e.checked) : null);
    const groups = [...document.querySelectorAll('[role="radiogroup"], [role="tablist"], [role="group"], fieldset')];
    const matches = groups.map(g => [...g.querySelectorAll(controls)].filter(e => e.getClientRects().length))
        .filter(items => items.length >= 2 && items.some(e => spotlight.test(name(e))) && items.some(e => story.test(name(e))));
    return matches.length > 0 && matches.every(items => items.filter(e => spotlight.test(name(e))).length === 1 &&
        items.every(e => spotlight.test(name(e)) ? state(e) === 'true' : state(e) === 'false'));
}'''
if not page.evaluate(spotlight_only):
    dest.click()
page.wait_for_function(spotlight_only, timeout=10000)
description = page.get_by_role('textbox', name=re.compile(r'Add a description'))
if not description.count():
    description = page.get_by_placeholder('Add a description and #topics', exact=True)
description.wait_for(state='visible', timeout=30000)
description.fill(caption)
if normalize(description.input_value()) != normalize(caption):
    raise RuntimeError('Snapchat caption differs from the intended caption')
receipt = page.get_by_text('Success! Your Snap is now live!', exact=True)
if receipt.count() and receipt.first.is_visible():
    raise RuntimeError('Stale Snapchat receipt before submitting')
post = page.get_by_role('button', name='Post', exact=True)
post.click(trial=True, timeout=90000)
if not page.evaluate(spotlight_only):
    raise RuntimeError('Spotlight is no longer the only selected destination')
mark_submission()
post.click(timeout=5000)
try:
    receipt.wait_for(state='visible', timeout=60000)
except BrowserTimeout:
    print('No explicit Spotlight receipt.')
else:
    if spotlight_receipt(page.url, page.locator('body').inner_text()):
        record_proof('SC', 'Spotlight explicitly selected; Snapchat confirms the Snap is live')