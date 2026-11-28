#!/usr/bin/env python3
"""Mix an intro and re-render subtitles against the same intro window.

The old video stays as <clip>.novo.mp4. A failed render/mix leaves it untouched.
CLIPS_HOME can isolate the operation; --audio-file reuses a matching local intro.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / 'clip-forge/skills/clipping-streams/scripts'
sys.path[:0] = [str(SCRIPTS), str(Path(__file__).resolve().parents[1] / 'posting')]
import _https  # noqa: E402  # pyright: ignore[reportMissingImports]
from publish_status import media_revision, voiceover_receipt  # noqa: E402  # pyright: ignore[reportMissingImports]

VO_DELAY_MS = 200
DUCK_TAIL_S = .10
ORIGINAL_INTRO_GAIN = .05
ORIGINAL_FADE_IN_S = .12


def voiceover_text(clip: dict, override: str | None = None) -> tuple[str, str]:
    """Return the display text and a single, optionally respelled TTS sentence."""
    canonical = clip.get('vo')
    value = override if override is not None else canonical
    if not isinstance(value, str) or not (text := ' '.join(value.split())):
        raise ValueError('Kein Voice-over-Text im Clip')
    matches_plan = isinstance(canonical, str) and text == ' '.join(canonical.split())
    spoken = clip.get('vo_spoken', text) if matches_plan else text
    if not isinstance(spoken, str) or not (spoken := ' '.join(spoken.split())):
        raise ValueError('vo_spoken muss ein vollständiger Satz sein')
    return text, spoken


def original_volume_filter(duck_until: float) -> str:
    gain = ORIGINAL_INTRO_GAIN
    # Evaluate each sample, so gain is already full at a protected speech onset.
    return f"aeval='val(ch)*({gain}+{1-gain}*clip((t-{duck_until:.3f})/{ORIGINAL_FADE_IN_S},0,1))':c=same"


def check_protected_opening(clip: dict, duck_until: float, duration: float) -> None:
    """Guard a source-speech onset in seconds of clip playback, not VOD time."""
    if 'vo_protect_from' not in clip:
        return
    opening = clip['vo_protect_from']
    if type(opening) not in (int, float) or not 0 <= opening < duration:
        raise ValueError('vo_protect_from muss eine Zeit innerhalb des Clips sein')
    audible_from = duck_until + ORIGINAL_FADE_IN_S
    if opening < audible_from:
        raise ValueError(f'Voice-over überdeckt den ersten Originalsatz bei {opening:.3f}s '
                         f'(Tonblende bis {audible_from:.3f}s); echten Vorlauf ergänzen')


def synthesize(text: str) -> bytes:
    key = os.environ.get('ELEVENLABS_API_KEY')
    if not key:
        raise ValueError('ELEVENLABS_API_KEY fehlt')
    voice = os.environ.get('ELEVENLABS_VOICE_ID', 'raYPS0b2ZPlIzZWkcD0G')
    return _https.read_https(
        _https.https_url('api.elevenlabs.io', f'/v1/text-to-speech/{voice}?output_format=mp3_44100_128'),
        data=json.dumps({'text': text, 'model_id': 'eleven_multilingual_v2',
                         'voice_settings': {'stability': .45, 'similarity_boost': .8, 'style': .3}}).encode(),
        headers={'xi-api-key': key, 'Content-Type': 'application/json', 'Accept': 'audio/mpeg'}, timeout=60,
    )


def video_timeline(file: Path) -> dict:
    result = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                                      'stream=start_time,duration,nb_frames,avg_frame_rate', '-of', 'json', str(file)])
    return json.loads(result)['streams'][0]


def audio_duration(file: Path) -> float:
    result = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                                      'stream=duration', '-of', 'json', str(file)])
    streams = json.loads(result)['streams']
    return float(streams[0].get('duration') or 0) if streams else 0.


def check_plan_duration(timeline: dict, audio: float, planned: float) -> None:
    video = float(timeline['duration'])
    fps = float(Fraction(timeline['avg_frame_rate']))
    if not all(map(math.isfinite, (video, fps, audio, planned))) or min(video, fps, audio, planned) <= 0:
        raise ValueError('Ungültige Bild- oder Tonlaufzeit')
    tolerance = max(.15, min(.25, 2 / fps))  # concat rounding and AAC tails, not missing segments
    if abs(video - planned) > tolerance or abs(audio - planned) > tolerance:
        raise ValueError(f'Bild oder Ton unvollständig: Soll {planned:.3f}s; Bild {video:.3f}s; Ton {audio:.3f}s')


def process(home: Path, rec: str, clip: str, text: str | None, reuse: Path | None) -> dict:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,159}', rec) or not re.fullmatch(r'clip[0-9]+', clip):
        raise ValueError('Ungültige Aufnahme- oder Clip-ID')
    work = home / 'work' / rec
    out = home / 'out' / rec
    original = out / f'{clip}.mp4'
    backup = out / f'{clip}.novo.mp4'
    receipt = original.with_suffix('.vo-ready')
    if backup.exists() or receipt.exists():
        raise ValueError('Voice-over bereits gemischt; für Änderungen eine neue Render-Revision verwenden')
    before = original.stat()
    plan = json.loads((work / 'clips.json').read_text())
    selected = next(c for c in plan['clips'] if c['id'] == clip)
    text, spoken = voiceover_text(selected, text)
    sidecar = json.loads((out / f'{clip}.json').read_text())
    if sidecar['segments'] != selected['segments']:
        raise ValueError('Schnittplan hat sich geändert; Original neu rendern')
    input_digest = sidecar.get('render_input_sha256')
    if not input_digest:
        raise ValueError('Original ohne Render-Fingerprint; eine neue Revision rendern')
    if media_revision(original) != sidecar.get('original_render_sha256'):
        raise ValueError('Original-MP4 passt nicht zu seinem Render-Sidecar; eine neue Revision rendern')
    timeline = video_timeline(original)
    source_audio = audio_duration(original)
    planned = sum(float(seg['end']) - float(seg['start']) for seg in selected['segments'])
    check_plan_duration(timeline, source_audio, planned)
    with tempfile.TemporaryDirectory(prefix=f'.{clip}-vo-', dir=out) as directory:
        stage = Path(directory)
        mp3 = stage / f'{clip}.vo.mp3'
        if reuse is None:
            mp3.write_bytes(synthesize(spoken))
        else:
            shutil.copy2(reuse, mp3)
        dur = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                             '-of', 'csv=p=0', str(mp3)]))
        if not math.isfinite(dur) or dur <= 0:
            raise ValueError('Voice-over-Datei ohne gültige Dauer')
        delay = VO_DELAY_MS / 1000
        duck_until = round(dur + delay + DUCK_TAIL_S, 3)
        if source_audio < duck_until:
            raise ValueError('Originalton endet vor dem Voice-over-Zeitfenster')
        check_protected_opening(selected, duck_until, min(source_audio, planned))
        windows = {'start': delay, 'end': round(delay + dur, 3), 'duck_until': duck_until}
        info = stage / 'voiceover.json'
        info.write_text(json.dumps(windows, ensure_ascii=False), encoding='utf-8')
        temp_work = stage / 'work' / rec
        temp_work.mkdir(parents=True)
        for name in ('clips.json', 'sections.json'):
            shutil.copy2(work / name, temp_work / name)
        emoji_review = work / 'emoji-reviews' / f'{clip}.json'
        target = temp_work / 'emoji-reviews' / emoji_review.name
        try:
            if emoji_review.is_file():
                review_bytes = emoji_review.read_bytes()
                target.parent.mkdir()
                # The per-mix stage is private. Publish only a complete receipt to the renderer.
                pending = target.with_suffix('.tmp')
                pending.write_bytes(review_bytes)
                pending.replace(target)
        except OSError:
            pass  # The fingerprint below still rejects any changed render inputs.
        if (work / 'meta.json').is_file():
            shutil.copy2(work / 'meta.json', temp_work / 'meta.json')
        if (work / 'transcripts').is_dir():
            shutil.copytree(work / 'transcripts', temp_work / 'transcripts')
        if (home / 'streamers.yaml').is_file():
            shutil.copy2(home / 'streamers.yaml', stage / 'streamers.yaml')
        env = dict(os.environ, CLIPS_HOME=str(stage))
        encoder = sidecar.get('encoder', 'x264')
        if encoder not in {'amf', 'videotoolbox', 'x264'}:
            raise ValueError('Unbekannter Encoder im Original-Render')
        flags = ['--reviewed-emoji-only'] if sidecar.get('reviewed_emoji_only') is True else []
        subprocess.run(['uv', 'run', '--quiet', str(SCRIPTS / 'render_clip.py'), '--vod-id', rec, '--clip', clip,
                        '--encoder', encoder, '--voiceover-window', str(info), *flags], env=env, stdout=sys.stderr, check=True)
        rendered = stage / 'out' / rec / f'{clip}.mp4'
        refreshed = json.loads(rendered.with_suffix('.json').read_text())
        if refreshed.get('render_input_sha256') != input_digest:
            raise ValueError('Render-Eingaben haben sich geändert; Original wurde nicht ersetzt')
        if video_timeline(rendered) != timeline:
            raise ValueError('Bildzeitachse beim Untertitel-Render verändert; Original unverändert')
        # Keep the already normalized original audio, but use the picture with matching subtitles.
        filt = (f"[1:a]{original_volume_filter(duck_until)}[a0];"
                f"[2:a]adelay={VO_DELAY_MS}|{VO_DELAY_MS},volume=2.0[a1];"
                "[a0][a1]amix=inputs=2:duration=first:normalize=0,"
                "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]")
        mixed = stage / 'mixed.mp4'
        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-i', str(rendered),
                        '-i', str(original), '-i', str(mp3), '-filter_complex', filt, '-map', '0:v', '-map', '[a]',
                        '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(mixed)], check=True)
        subprocess.run(['uv', 'run', '--quiet', str(SCRIPTS / 'verify_clip.py'), '--file', str(mixed)],
                       env=env, stdout=sys.stderr, check=True)
        mixed_audio = audio_duration(mixed)
        check_plan_duration(video_timeline(mixed), mixed_audio, planned)
        if mixed_audio < windows['end']:
            raise ValueError('Voice-over im fertigen Audiomix abgeschnitten')
        ready = stage / 'vo-ready'
        ready.write_text(voiceover_receipt(media_revision(mixed)) + '\n', encoding='ascii')
        after = original.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('Original wurde während des Mischens geändert; Abbruch')
        ass = temp_work / 'render' / f'{clip}.ass'
        ass_out = work / 'render' / f'{clip}.ass'
        ass_out.parent.mkdir(exist_ok=True)
        os.link(original, backup)
        try:
            mixed.replace(original)
        except OSError:
            backup.unlink()
            raise
        mp3.replace(out / f'{clip}.vo.mp3')
        # The final clip and its on-disk ASS now describe the same edit.
        ass.replace(ass_out)
        info.replace(work / 'render' / f'{clip}.vo.json')
        # Publication checks the hash, not the backup; partial work can never pass this gate.
        ready.replace(receipt)
    return {'ok': True, 'clip': str(original), 'vo_text': text, 'vo_s': round(dur, 2), 'vo_captions': False}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('rec')
    ap.add_argument('clip')
    ap.add_argument('text', nargs='?')
    ap.add_argument('--audio-file', type=Path, help='Lokale MP3 mit genau dem angegebenen Voice-over-Text')
    a = ap.parse_args()
    home = Path(os.environ.get('CLIPS_HOME') or Path.home() / 'clips').expanduser().resolve()
    try:
        result = process(home, a.rec, a.clip, a.text, a.audio_file)
    except (OSError, KeyError, ValueError, TypeError, ZeroDivisionError, StopIteration, subprocess.CalledProcessError) as exc:
        raise SystemExit(f'Voice-over nicht fertiggestellt: {exc}') from exc
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
