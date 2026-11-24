from h import *
p,b,ctx=connect()
pg=get_page(ctx,'x.com')
btn=pg.locator('[role=dialog] [data-testid="tweetButton"]').first
try:
    btn.click(trial=True, timeout=15000)
    mark_submission()
    btn.click(timeout=5000)
except Exception as e:
    print('ERR',str(e)[:300])
    # identify interceptor
    print(pg.evaluate('''(()=>{const b=document.querySelector('[role=dialog] [data-testid="tweetButton"]').getBoundingClientRect();const e=document.elementFromPoint(b.x+b.width/2,b.y+b.height/2);return e.outerHTML.slice(0,300)+' | parent '+(e.parentElement?e.parentElement.outerHTML.slice(0,300):'')})()'''))
pg.wait_for_timeout(8000)
print(pg.url); shot(pg,'x3')
