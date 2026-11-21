from h import *
import os, sys
vod,clip=sys.argv[1],sys.argv[2]
p,b,ctx=connect()
pg=get_page(ctx,'x.com')
if 'compose' not in pg.url: pg.goto('https://x.com/compose/post'); pg.wait_for_timeout(5000)
pg.locator('input[data-testid="fileInput"]').first.set_input_files(os.path.expanduser(f'~/clips/out/{vod}/{clip}.mp4'))
ed=pg.locator('[data-testid="tweetTextarea_0"]').first
ed.click()
txt=(lambda t: (t.split('--- X ---')[1] if '--- X ---' in t else t.split('--- TIKTOK ---')[1]).split('---')[0].strip())(open(os.path.expanduser(f'~/clips/out/{vod}/{clip}.txt')).read())
pg.keyboard.type(txt); pg.wait_for_timeout(1000); pg.keyboard.type(' '); pg.keyboard.press('Escape') if False else None
for i in range(36):
    pg.wait_for_timeout(5000)
    dis=pg.locator('[data-testid="tweetButton"]').first.get_attribute('aria-disabled')
    if dis!='true': break
print('disabled',dis, ed.inner_text())
shot(pg,'x2')
