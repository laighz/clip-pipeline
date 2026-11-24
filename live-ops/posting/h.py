from playwright.sync_api import sync_playwright
import sys, os
OUT=os.path.expanduser('~/.hermes/cache/scratch/pw')
managed_session = None
before_submit = None
def connect():
    if managed_session is not None:
        return managed_session[:3]
    p=sync_playwright().start()
    try:
        b=p.chromium.connect_over_cdp('http://localhost:9333', timeout=12000)
    except Exception:
        p.stop()
        raise
    return p,b,b.contexts[0]
def get_page(ctx, part, url=None):
    if managed_session is not None:
        pages = managed_session[3]
        if part not in pages:
            pages[part] = ctx.new_page()
            if url: pages[part].goto(url, wait_until='domcontentloaded')
        return pages[part]
    for pg in ctx.pages:
        if part in pg.url: return pg
    pg=ctx.new_page()
    if url: pg.goto(url, wait_until='domcontentloaded')
    return pg
def shot(pg,name):
    f=f'{OUT}/{name}.png'; pg.screenshot(path=f); print('SHOT',f); return f

def record_proof(platform, proof):
    if managed_session is not None:
        managed_session[4].append((platform, proof))


def mark_submission():
    if before_submit is not None:
        before_submit()
