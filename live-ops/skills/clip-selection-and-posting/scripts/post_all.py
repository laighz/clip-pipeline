"""Einen fertigen Clip per CDP auf YouTube, TikTok, Instagram, X und Snapchat posten.

Voraussetzung: Chrome mit dem Clip-Profil laeuft mit --remote-debugging-port=9333
(siehe cdp_helper.py). Aufruf (im Ordner dieser Datei):
  uv run --quiet --with playwright python post_all.py <vod> <clip> [yt,tt,ig,x,sc] [--stagger]
--stagger (Standard im Live-Betrieb, Anti-Shadowban): Plattformen in zufaelliger Reihenfolge,
  zwischen zwei Plattformen zufaellig 10-60 min Pause. Laeuft dann 1-3 h -> als Hintergrundprozess.
Globaler Browser-Lock (/tmp/livespiked_browser.lock): ueberlappende Laeufe warten aufeinander,
  statt sich gegenseitig in die Dialoge zu klicken.
Plattformen mit Flag-Datei ~/.hermes/cache/scratch/live/<KEY>_BLOCKED (z. B. IG_BLOCKED) werden uebersprungen.
Gibt pro Plattform eine Zeile OK/FAIL aus. Danach trotzdem im Profil/Studio pruefen;
bei FAIL erst in der Liste nachsehen, dann einmal wiederholen.
Captions: TITEL + Block pro Plattform aus out/<vod>/<clip>.txt; X/SNAPCHAT fallen auf TIKTOK/INSTAGRAM zurueck,
  wenn kein eigener Block da ist (besser: cdp_helper.add_platform_blocks vorher aufrufen).
"""
import os, random, re, sys, tempfile, time
from cdp_helper import connect, get_page, shot, caption

args = [a for a in sys.argv[1:] if not a.startswith('--')]
STAGGER = '--stagger' in sys.argv
VOD, CLIP = args[0], args[1]
WANT = (args[2] if len(args) > 2 else 'yt,tt,ig,x,sc').split(',')
BASE = os.path.expanduser(f'~/clips/out/{VOD}/{CLIP}')
MP4, TXT = BASE + '.mp4', BASE + '.txt'
YT_CHANNEL = 'UCwCaCEXf-nQ0SbLcGi2Bv5Q'
SNAP_PROFILE = 'https://profile.snapchat.com/'
LOCK = os.path.join(tempfile.gettempdir(), 'livespiked_browser.lock')
FLAGS = os.path.expanduser('~/.hermes/cache/scratch/live')

p, b, ctx = connect()


def yt():
    pg = get_page(ctx, 'studio.youtube.com')
    pg.goto(f'https://studio.youtube.com/channel/{YT_CHANNEL}/videos/upload?d=ud', wait_until='domcontentloaded')
    pg.wait_for_timeout(7000)
    pg.locator('input[type=file]').first.set_input_files(MP4)
    dlg = pg.locator('ytcp-uploads-dialog')
    dlg.locator('#title-textarea #textbox').wait_for(timeout=60000); pg.wait_for_timeout(3000)
    for sel, txt in (('#title-textarea #textbox', caption(TXT, 'TITEL')),
                     ('#description-textarea #textbox', caption(TXT, 'YOUTUBE'))):
        dlg.locator(sel).click(); pg.keyboard.press('Meta+A'); pg.keyboard.type(txt)
    dlg.locator('tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]').click()
    for _ in range(3):
        dlg.locator('#next-button').click(); pg.wait_for_timeout(2000)
    dlg.locator('tp-yt-paper-radio-button[name="PUBLIC"]').click(); pg.wait_for_timeout(1000)
    for _ in range(60):
        if 'Uploading' not in dlg.inner_text(): break
        pg.wait_for_timeout(3000)
    dlg.locator('#done-button').click(); pg.wait_for_timeout(8000)
    m = re.search(r'https://youtube\.com/shorts/[\w-]+', pg.locator('body').inner_text())
    return m.group(0) if m else 'kein Link, im Studio pruefen'


def tt():
    pg = get_page(ctx, 'tiktok')
    pg.goto('https://www.tiktok.com/tiktokstudio/upload', wait_until='domcontentloaded'); pg.wait_for_timeout(5000)
    d = pg.get_by_role('button', name='Discard')           # alter Entwurf
    if d.count():
        d.first.click(); pg.wait_for_timeout(1500)
        c = pg.get_by_role('button', name='Discard')
        if c.count(): c.last.click()
        pg.wait_for_timeout(1500)
    pg.locator('input[type=file]').first.set_input_files(MP4); pg.wait_for_timeout(15000)
    for n in ('Cancel', 'Got it'):                          # Content-Check-Modal, Hinweis
        x = pg.get_by_role('button', name=n)
        if x.count(): x.first.click(); pg.wait_for_timeout(800)
    ed = pg.locator('div[contenteditable="true"]').first
    ed.scroll_into_view_if_needed(); ed.click(); pg.keyboard.press('Meta+A'); pg.keyboard.press('Backspace')
    for w in caption(TXT, 'TIKTOK').split(' '):           # Hashtag-Vorschlaege einzeln abwarten
        pg.keyboard.type(w)
        if w[:1] in '#@': pg.wait_for_timeout(1200)
        pg.keyboard.type(' ')
    btn = pg.locator('button[data-e2e="post_video_button"]').first
    btn.scroll_into_view_if_needed(); btn.click(); pg.wait_for_timeout(8000)
    return 'gepostet' if 'content' in pg.url else 'unklar: ' + pg.url


def ig():
    pg = get_page(ctx, 'instagram.com')
    pg.goto('https://www.instagram.com/', wait_until='domcontentloaded'); pg.wait_for_timeout(5000)
    if '/accounts/suspended' in pg.url:
        open(os.path.join(FLAGS, 'IG_BLOCKED'), 'w').write('suspended\n')
        raise RuntimeError('Konto gesperrt -> IG_BLOCKED gesetzt, User informieren')
    pg.locator('svg[aria-label="Post"]').first.click(); pg.wait_for_timeout(2500)
    pg.locator('div[role=dialog] input[type=file]').first.set_input_files(MP4); pg.wait_for_timeout(8000)
    ok = pg.get_by_role('button', name='OK')
    if ok.count(): ok.first.click(); pg.wait_for_timeout(1000)
    pg.locator('svg[aria-label="Select crop"]').first.click(); pg.wait_for_timeout(1000)
    pg.get_by_text('Original', exact=True).first.click(); pg.wait_for_timeout(1500)
    dlg = pg.locator('div[role=dialog]')
    for _ in range(2):
        dlg.get_by_role('button', name='Next').first.click(); pg.wait_for_timeout(2500)
    pg.locator('div[role=dialog] div[contenteditable="true"]').first.click()
    pg.keyboard.type(caption(TXT, 'INSTAGRAM')); pg.wait_for_timeout(1500)
    pg.keyboard.press('Escape')   # Hashtag-Dropdown; falls es bleibt: auf den Kontonamen im Dialog klicken
    dlg.get_by_role('button', name='Share').first.click()
    for _ in range(30):
        pg.wait_for_timeout(5000)
        t = dlg.first.inner_text() if dlg.count() else ''
        if 'shared' in t.lower():
            d = pg.get_by_role('button', name='Done')
            if d.count(): d.first.click()
            return 'Reel geteilt'
        if 'could not be shared' in t.lower():
            return 'FEHLER: Post could not be shared'
    return 'unklar'


def x():
    pg = get_page(ctx, 'x.com')
    pg.goto('https://x.com/compose/post'); pg.wait_for_timeout(5000)
    pg.locator('input[data-testid="fileInput"]').first.set_input_files(MP4)
    pg.locator('[data-testid="tweetTextarea_0"]').first.click()
    pg.keyboard.type(caption(TXT, 'X', fallback='TIKTOK') + ' ')
    for _ in range(36):                                    # Video-Verarbeitung abwarten
        pg.wait_for_timeout(5000)
        if pg.locator('[data-testid="tweetButton"]').first.get_attribute('aria-disabled') != 'true': break
    pg.locator('[role=dialog] [data-testid="tweetButton"]').first.click(timeout=20000)
    pg.wait_for_timeout(10000)
    g = pg.get_by_role('button', name='Got it')            # "Unlock more on X"
    if g.count(): g.first.click()
    return 'gepostet (Profil erst nach 1-2 min pruefen, nicht sofort wiederholen)'


def sc():
    pg = get_page(ctx, 'snapchat.com')
    pg.goto(SNAP_PROFILE, wait_until='domcontentloaded'); pg.wait_for_timeout(7000)
    pg.get_by_text('Post to Snapchat').first.click(); pg.wait_for_timeout(4000)
    pg.locator('input[type=file]').first.set_input_files(MP4); pg.wait_for_timeout(12000)
    ta = pg.locator('textarea[placeholder="Add a description and #topics"]')
    if not ta.count():                                     # Klick schaltet Spotlight sonst wieder ab
        pg.get_by_text('Post to Spotlight').first.click(); pg.wait_for_timeout(2000)
    ta.first.click(); ta.first.fill(caption(TXT, 'SNAPCHAT', fallback='INSTAGRAM')); pg.keyboard.press('Escape')
    log = []
    pg.on('response', lambda r: log.append(r.text()[:200]) if 'PostProfileMedia' in r.url else None)
    bt = pg.get_by_role('button', name='Post', exact=True).first
    bt.scroll_into_view_if_needed(); bt.click()            # genau EIN Klick
    for _ in range(10):
        pg.wait_for_timeout(2000)
        if log: break
    if not log: return 'keine Antwort, Profil -> Spotlights pruefen'
    return 'gepostet' if 'EmptyResponse' in log[0] else 'FEHLER ' + log[0]


def lock():
    while True:
        try:
            os.mkdir(LOCK); return
        except FileExistsError:
            time.sleep(20)


FN = {'yt': yt, 'tt': tt, 'ig': ig, 'x': x, 'sc': sc}
order = [k for k in ('yt', 'tt', 'ig', 'x', 'sc') if k in WANT]
if STAGGER:
    random.shuffle(order)
first = True
for key in order:
    if os.path.exists(os.path.join(FLAGS, f'{key.upper()}_BLOCKED')):
        print(f'{key.upper()}: SKIP (gesperrt)', flush=True); continue
    if STAGGER and not first:
        time.sleep(600 + random.randint(0, 3000))
    first = False
    lock()
    try:
        print(f'{key.upper()} {time.strftime("%H:%M")}: OK {FN[key]()}', flush=True)
    except Exception as e:
        print(f'{key.upper()} {time.strftime("%H:%M")}: FAIL {str(e).splitlines()[0][:200]}', flush=True)
    finally:
        try: os.rmdir(LOCK)
        except OSError: pass
