#!/usr/bin/env python3
"""Voice-over intro via ElevenLabs, mixed over the first seconds of a rendered clip.

Usage: vo_mix.py <rec> <clip> ["Text"]   (Text default: clips.json -> vo, sonst commentary, 1. Satz)
Needs ELEVENLABS_API_KEY (env or ~/.hermes/.env). Optional ELEVENLABS_VOICE_ID.
Keeps original as <clip>.novo.mp4; result replaces <clip>.mp4. Original audio ducked to 25 % while the voice speaks.
"""
import json, os, subprocess, sys, urllib.request

def key():
    k = os.environ.get('ELEVENLABS_API_KEY')
    if k: return k
    for l in open(os.path.expanduser('~/.hermes/.env')):
        l = l.strip()
        if l.startswith('ELEVENLABS_API_KEY=') and len(l) > 20: return l.split('=', 1)[1].strip().strip('"')
    sys.exit('ELEVENLABS_API_KEY fehlt')

rec, clip = sys.argv[1], sys.argv[2]
out = os.path.expanduser(f'~/clips/out/{rec}/')
if len(sys.argv) > 3:
    text = sys.argv[3]
else:
    cj = json.load(open(os.path.expanduser(f'~/clips/work/{rec}/clips.json')))
    c = next(x for x in cj['clips'] if x['id'] == clip)
    text = c.get('vo')
    if not text: sys.exit('kein vo-Text (humanized) im Clip, poste ohne Voice-over')
voice = os.environ.get('ELEVENLABS_VOICE_ID', 'raYPS0b2ZPlIzZWkcD0G')  # Lars (vom User gewählt)
req = urllib.request.Request(
    f'https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128',
    data=json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                     "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3}}).encode(),
    headers={"xi-api-key": key(), "Content-Type": "application/json", "Accept": "audio/mpeg"})
mp3 = out + f'{clip}.vo.mp3'
open(mp3, 'wb').write(urllib.request.urlopen(req, timeout=60).read())
dur = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', mp3]).strip())
src = out + f'{clip}.novo.mp4'
if not os.path.exists(src): os.rename(out + f'{clip}.mp4', src)
d = dur + 0.3
filt = (f"[0:a]volume='if(lt(t,{d:.2f}),0.12,1)':eval=frame[a0];"
        f"[1:a]adelay=200|200,volume=2.0[a1];[a0][a1]amix=inputs=2:duration=first:normalize=0,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]")
subprocess.check_call(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', src, '-i', mp3,
                       '-filter_complex', filt, '-map', '0:v', '-map', '[a]', '-c:v', 'copy',
                       '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', out + f'{clip}.mp4'])
print(json.dumps({"ok": True, "clip": out + f'{clip}.mp4', "vo_text": text, "vo_s": round(dur, 2)}, ensure_ascii=False))
