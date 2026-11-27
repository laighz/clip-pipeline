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
spotlight_only = '''() => {
    const visible = el => el.getClientRects().length && getComputedStyle(el).visibility === 'visible';
    const section = text => {
        const headers = [...document.querySelectorAll('div, h2, h3')]
            .filter(e => !e.children.length && e.textContent.trim() === text && visible(e));
        return headers.length === 1 ? headers[0].parentElement : null;
    };
    const controls = '[role="checkbox"], [role="radio"], [role="switch"], input[type="checkbox"], input[type="radio"]';
    const check = (group, expected, optional = {}) => {
        if (!group) return false;
        const items = [...group.querySelectorAll(controls)]
            .filter(e => e.getAttribute('aria-hidden') !== 'true' && visible(e));
        if (new Set(items.map(e => e.id)).size !== items.length) return false;
        return Object.keys(expected).every(id => items.some(e => e.id === id)) && items.every(e => {
            const state = e.getAttribute('aria-checked');
            if (Object.hasOwn(optional, e.id)) return state === 'true' || state === 'false';
            if (!Object.hasOwn(expected, e.id)) return false;
            const [label, desired] = expected[e.id];
            const labels = [...group.querySelectorAll('label')].filter(l => l.htmlFor === e.id && visible(l));
            return state === desired && labels.length === 1 &&
                [...labels[0].querySelectorAll('span')].some(s => s.textContent.trim() === label && visible(s));
        });
    };
    return check(section('Choose your posting destination'), {
        spotlight: ['Post to Spotlight', 'true'], publicStory: ['Post to Public Story', 'false'],
    }, {'spotlight.isSaved': true}) && check(section('Save to your Public Profile'), {
        publicProfile: ['Save to a Public Profile', 'false'],
    });
}'''
# Enabling Spotlight can also select the separate saved-profile destination.
for id_, desired in (('spotlight', 'true'), ('publicStory', 'false'), ('publicProfile', 'false')):
    control = page.locator(f'button[role="checkbox"][id="{id_}"]')
    control.wait_for(state='visible', timeout=30000)
    before = control.get_attribute('aria-checked')
    if before not in {'true', 'false'}:
        raise RuntimeError(f'Cannot read Snapchat checkbox: {id_}')
    if before != desired:
        control.click()
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
# The form can select the saved-profile destination after the preview becomes ready.
saved = page.locator('button[role="checkbox"][id="publicProfile"]')
if saved.get_attribute('aria-checked') == 'true':
    saved.click()
    post.click(trial=True, timeout=10000)
if not page.evaluate(spotlight_only):
    raise RuntimeError('Spotlight is no longer the only selected destination')
mark_submission()
post.click(timeout=5000)
try:
    receipt.wait_for(state='visible', timeout=120000)
except BrowserTimeout:
    print('No explicit Spotlight receipt.')
else:
    if spotlight_receipt(page.url, page.locator('body').inner_text()):
        record_proof('SC', 'Spotlight explicitly selected; Snapchat confirms the Snap is live')