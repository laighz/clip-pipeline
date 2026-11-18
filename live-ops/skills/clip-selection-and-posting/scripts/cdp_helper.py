"""Playwright-Helfer fuer manuelles Posten im angemeldeten Clip-Browserprofil.

Vorher Chrome als Hintergrundprozess starten (echtes Chrome, Profil traegt die Logins):
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    --user-data-dir=$HOME/clips/state/browser --remote-debugging-port=9333
    --no-first-run --window-size=1400,1000 <start-url>
Dann pro Schritt ein kurzes Skript im selben Ordner wie diese Datei:
  uv run --quiet --with playwright python step.py   (mit: from cdp_helper import *)
Nach jedem Schritt shot() ansehen; Modals schliessen, bevor geklickt wird.
"""
from playwright.sync_api import sync_playwright
import os

OUT = os.path.expanduser('~/.hermes/cache/scratch/pw')
os.makedirs(OUT, exist_ok=True)


def connect(port=9333):
    p = sync_playwright().start()
    b = p.chromium.connect_over_cdp(f'http://localhost:{port}')
    return p, b, b.contexts[0]


def get_page(ctx, part, url=None):
    """Vorhandenen Tab mit `part` in der URL nehmen, sonst neuen oeffnen."""
    for pg in ctx.pages:
        if part in pg.url:
            return pg
    pg = ctx.new_page()
    if url:
        pg.goto(url, wait_until='domcontentloaded')
    return pg


def shot(pg, name):
    f = f'{OUT}/{name}.png'
    pg.screenshot(path=f)
    print('SHOT', f)
    return f


def caption(clip_txt, block, fallback=None):
    """Text eines Blocks aus clipXX.txt (YOUTUBE/TIKTOK/INSTAGRAM/X/SNAPCHAT) oder den Titel (block='TITEL').
    Fehlt der Block (render_clip schreibt nur YT/TT/IG), wird `fallback` genommen."""
    t = open(clip_txt).read()
    if block == 'TITEL':
        return t.split('TITEL:')[1].split('\n')[0].strip()
    if f'--- {block} ---' not in t and fallback:
        block = fallback
    return t.split(f'--- {block} ---')[1].split('---')[0].strip()


def add_platform_blocks(clip_txt, x=None, snapchat=None):
    """X- und SNAPCHAT-Block an clipXX.txt anhaengen (nach dem Rendern; render_clip ueberschreibt die Datei).
    Eigener Text + eigene, wenige Hashtags pro Plattform, nie den TikTok-Text 1:1."""
    t = open(clip_txt).read()
    for blk, val in (('X', x), ('SNAPCHAT', snapchat)):
        if val and f'--- {blk} ---' not in t:
            t += f'\n--- {blk} ---\n{val}\n'
    open(clip_txt, 'w').write(t)
