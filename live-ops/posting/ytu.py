from h import *
import os, sys
vod,clip=sys.argv[1],sys.argv[2]
t=open(os.path.expanduser(f'~/clips/out/{vod}/{clip}.txt')).read()
title=t.split('TITEL:')[1].split('\n')[0].strip()
desc=t.split('--- YOUTUBE ---')[1].split('---')[0].strip()
p,b,ctx=connect()
pg=get_page(ctx,'studio.youtube.com')
pg.bring_to_front()
pg.goto('https://studio.youtube.com/channel/UCwCaCEXf-nQ0SbLcGi2Bv5Q/videos/upload?d=ud',wait_until='domcontentloaded'); pg.wait_for_timeout(7000)
pg.locator('input[type=file]').first.set_input_files(os.path.expanduser(f'~/clips/out/{vod}/{clip}.mp4'))
dlg=pg.locator('ytcp-uploads-dialog')
dlg.locator('#title-textarea #textbox').wait_for(timeout=60000); pg.wait_for_timeout(3000)
tb=dlg.locator('#title-textarea #textbox'); tb.click(); pg.keyboard.press('Meta+A'); pg.keyboard.type(title)
db=dlg.locator('#description-textarea #textbox'); db.click(); pg.keyboard.press('Meta+A'); pg.keyboard.type(desc)
dlg.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').click(); pg.wait_for_timeout(800)
for i in range(3):
    dlg.locator('#next-button').click(); pg.wait_for_timeout(2000)
dlg.locator('tp-yt-paper-radio-button[name="PUBLIC"]').click(); pg.wait_for_timeout(1000)
# wait for upload/checks before publishing
for i in range(60):
    txt=dlg.inner_text()
    if 'Uploading' not in txt and 'Hochladen' not in txt: break
    pg.wait_for_timeout(3000)
dlg.locator('#done-button').click(); pg.wait_for_timeout(3000)
# YT may still be running checks: confirm with "Publish anyway" (user rule)
for name in ('Publish anyway', 'Trotzdem veröffentlichen'):
    pa = pg.get_by_role('button', name=name)
    if pa.count():
        pa.first.click(); print('clicked:', name); pg.wait_for_timeout(3000); break
pg.wait_for_timeout(5000)
body=pg.locator('body').inner_text()
import re
m=re.search(r'https://youtube\.com/shorts/[\w-]+',body)
print('published' if ('published' in body.lower() or 'veröffentlicht' in body.lower()) else 'unclear', m.group(0) if m else '')
shot(pg,'ytu_'+clip)
c=pg.get_by_role('button',name='Close')
pass
