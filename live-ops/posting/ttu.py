from h import *
import sys
vod,clip=sys.argv[1],sys.argv[2]
p,b,ctx=connect()
pg=get_page(ctx,'tiktok')
pg.bring_to_front()
pg.goto('https://www.tiktok.com/tiktokstudio/upload',wait_until='domcontentloaded'); pg.wait_for_timeout(5000)
d=pg.get_by_role('button',name='Discard')
if d.count():
    d.first.click(); pg.wait_for_timeout(1500)
    c=pg.get_by_role('button',name='Discard')
    if c.count(): c.last.click()
    pg.wait_for_timeout(1500)
pg.locator('input[type=file]').first.set_input_files(f'/Users/moritz/clips/out/{vod}/{clip}.mp4')
pg.wait_for_timeout(15000)
for n in ['Cancel','Got it']:
    x=pg.get_by_role('button',name=n)
    if x.count(): x.first.click(); pg.wait_for_timeout(800)
ed=pg.locator('div[contenteditable="true"]').first
ed.scroll_into_view_if_needed(); ed.click()
pg.keyboard.press('Meta+A'); pg.keyboard.press('Backspace')
txt=open(f'/Users/moritz/clips/out/{vod}/{clip}.txt').read().split('--- TIKTOK ---')[1].split('---')[0].strip()
for w in txt.split(' '):
    pg.keyboard.type(w)
    if w[:1] in '#@': pg.wait_for_timeout(1200)
    pg.keyboard.type(' ')
pg.wait_for_timeout(1500)
print('caption:',ed.inner_text())
btn=pg.locator('button[data-e2e="post_video_button"]')
btn.first.scroll_into_view_if_needed(); btn.first.click()
pg.wait_for_timeout(8000)
print(pg.url)
