import re
from h import *
import sys
vod,clip=sys.argv[1],sys.argv[2]
p,b,ctx=connect()
pg=get_page(ctx,'instagram.com')
pg.bring_to_front()
pg.goto('https://www.instagram.com/',wait_until='domcontentloaded'); pg.wait_for_timeout(5000)
import os
if any(marker in pg.url for marker in ('/accounts/disabled', 'suspended', 'challenge', 'restricted')):
    open(os.path.expanduser('~/.hermes/cache/scratch/live/IG_BLOCKED'),'w').write(pg.url); print('IG BLOCKED', pg.url); raise SystemExit(1)
if not pg.locator('div[role=dialog] input[type=file]').count():
    for sel in ['svg[aria-label="New post"]','svg[aria-label="Post"]','svg[aria-label="Create"]','svg[aria-label="Neuer Beitrag"]','a:has-text("Create")']:
        if pg.locator(sel).count():
            pg.locator(sel).first.click(); pg.wait_for_timeout(2500)
            if pg.get_by_text('Post',exact=True).count() and not pg.locator('div[role=dialog] input[type=file]').count():
                pg.get_by_text('Post',exact=True).first.click(); pg.wait_for_timeout(2500)
            break
pg.locator('div[role=dialog] input[type=file]').first.set_input_files(os.path.expanduser(f'~/clips/out/{vod}/{clip}.mp4'))
pg.wait_for_timeout(8000)
ok=pg.get_by_role('button',name='OK')
if ok.count(): ok.first.click(); pg.wait_for_timeout(1000)
dlg=pg.locator('div[role=dialog]')
pg.locator('svg[aria-label="Select crop"]').first.click(); pg.wait_for_timeout(1000)
pg.get_by_text('Original',exact=True).first.click(); pg.wait_for_timeout(1500)
for i in range(2):
    dlg.get_by_role('button',name='Next').first.click(); pg.wait_for_timeout(2500)
ed=pg.locator('div[role=dialog] div[contenteditable="true"]').first
ed.click()
txt=open(os.path.expanduser(f'~/clips/out/{vod}/{clip}.txt')).read().split('--- INSTAGRAM ---')[1].split('---')[0].strip()
pg.keyboard.type(txt); pg.wait_for_timeout(1500)
dlg.get_by_text(re.compile(r'^live\.?spiked(26)?$')).first.click(); pg.wait_for_timeout(1000)
print('caption:',ed.inner_text())
share = dlg.get_by_role('button',name='Share').first
share.click(trial=True, timeout=30000)
mark_submission()
share.click(timeout=5000)
t=''
for i in range(30):
    pg.wait_for_timeout(5000)
    t=pg.locator('div[role=dialog]').first.inner_text() if pg.locator('div[role=dialog]').count() else ''
    if re.search(r'Your (?:post|reel|video) has been shared\.?', t, re.I):
        # A receipt from this share dialog, not any page text containing "shared".
        record_proof('IG', 'Instagram confirmed the Reel was shared')
        break
    if 'could not be shared' in t.lower(): break
print(t[:120].replace('\n',' | '))
d=pg.get_by_role('button',name='Done')
if d.count(): d.first.click()
