from playwright.sync_api import sync_playwright
import sys, os
OUT=os.path.expanduser('~/.hermes/cache/scratch/pw')
def connect():
    p=sync_playwright().start()
    b=p.chromium.connect_over_cdp('http://localhost:9333')
    return p,b,b.contexts[0]
def get_page(ctx, part, url=None):
    for pg in ctx.pages:
        if part in pg.url: return pg
    pg=ctx.new_page()
    if url: pg.goto(url, wait_until='domcontentloaded')
    return pg
def shot(pg,name):
    f=f'{OUT}/{name}.png'; pg.screenshot(path=f); print('SHOT',f); return f
