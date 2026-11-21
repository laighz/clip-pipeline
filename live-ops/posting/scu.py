from h import *
import os, sys, json
vod,clip=sys.argv[1],sys.argv[2]
p,b,ctx=connect()
pg=get_page(ctx,'snapchat.com')
pg.bring_to_front()
pg.goto('https://profile.snapchat.com/',wait_until='domcontentloaded'); pg.wait_for_timeout(7000)
pg.get_by_text('Post to Snapchat').first.click(); pg.wait_for_timeout(4000)
pg.locator('input[type=file]').first.set_input_files(os.path.expanduser(f'~/clips/out/{vod}/{clip}.mp4'))
pg.wait_for_timeout(12000)
ta=pg.locator('textarea[placeholder="Add a description and #topics"]')
if not ta.count():
    pg.get_by_text('Post to Spotlight').first.click(); pg.wait_for_timeout(2000)
t=open(os.path.expanduser(f'~/clips/out/{vod}/{clip}.txt')).read()
txt=(t.split('--- SNAPCHAT ---')[1] if '--- SNAPCHAT ---' in t else t.split('--- TIKTOK ---')[1]).split('---')[0].strip()
ta=pg.locator('textarea[placeholder="Add a description and #topics"]').first
ta.click(); ta.fill(txt); pg.wait_for_timeout(800); pg.keyboard.press('Escape')
print('desc:',ta.input_value())
bt=pg.get_by_role('button',name='Post',exact=True).first
bt.scroll_into_view_if_needed(); bt.click()
for i in range(15):
    pg.wait_for_timeout(4000)
    if 'web-uploader' not in pg.url: break
body=pg.locator('body').inner_text()
print(pg.url)
print([l for l in body.split('\n') if any(k in l.lower() for k in ['denied','error','fail','success','posted','action needed','spend'])][:8])
shot(pg,'scu_'+clip)
