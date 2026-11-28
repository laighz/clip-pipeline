# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Sichtung lokaler Highlight-Kandidaten vor dem Schnitt: Aufbau, Moment, Reaktion."""
from __future__ import annotations

import argparse
import math
import re
import tempfile
from pathlib import Path

import _lib

LIMIT = 10
TILE_W, TILE_H = 320, 180


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('ungültige Zahl')
    return float(value)


def select_candidates(work, ids, limit):
    rows = _lib.read_json(work / 'candidates.json')['candidates']
    if not isinstance(rows, list):
        raise TypeError('candidates muss eine Liste sein')
    indexed = {}
    for row in rows:
        cid = row['id']
        if not isinstance(cid, str) or not re.fullmatch(r'c[0-9]+', cid) or cid in indexed:
            raise ValueError(f'ungültige oder doppelte Kandidaten-ID: {cid!r}')
        indexed[cid] = row
    if ids is not None:
        wanted = [x.strip() for x in ids.split(',')]
        if len(wanted) != len(set(wanted)) or len(wanted) > LIMIT:
            raise ValueError(f'1–{LIMIT} verschiedene Kandidaten-IDs angeben')
        for cid in wanted:
            if cid not in indexed:
                raise ValueError(f'unbekannter Kandidat: {cid!r}')
        return [indexed[cid] for cid in wanted]
    return sorted(rows, key=lambda row: -number(row['score']))[:limit]


def candidate_info(row):
    start, end = map(number, row['section'])
    moment = number(row['moment_s'])
    if not 0 <= start <= moment <= end or end <= start:
        raise ValueError(f"ungültiges Kandidatenfenster: {row['id']}")
    return {'id': row['id'], 'score': number(row['score']), 'section': [start, end],
            'moment_s': moment, 'reasons': row.get('reasons', [])}


def section_source(section, manifest, meta):
    # Newer Forge validates the entire live source map. Older manifests do not
    # have that map; their file and offset are sufficient for a local preview.
    binding_for = getattr(_lib, 'transcript_binding', None)
    if binding_for is not None:
        binding = binding_for(section, meta=meta, manifest_source=manifest.get('source'))
        return Path(binding['file']['path']), *binding['window']
    if section.get('live_map') is not None:
        raise ValueError('Live-Source-Zuordnung wird von dieser Forge-Version nicht unterstützt')
    lo, hi, offset = (float(section[k]) for k in ('start', 'end', 'file_offset'))
    if not all(map(math.isfinite, (lo, hi, offset))) or not 0 <= offset <= lo < hi:
        raise ValueError('ungültiges Abschnittsfenster')
    path = Path(section['file']).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Abschnitt ist keine Datei')
    if ('local' in (section.get('mode'), manifest.get('source'), meta.get('source'))
            and (offset != 0 or not meta.get('input') or Path(meta['input']).resolve() != path)):
        raise ValueError('Abschnitt gehört nicht zur lokalen Eingabedatei')
    if 'media_revision' in section:
        st = path.stat()
        revision = {'path': str(path), 'dev': st.st_dev, 'ino': st.st_ino, 'size': st.st_size,
                    'mtime_ns': st.st_mtime_ns, 'ctime_ns': st.st_ctime_ns}
        if section['media_revision'] != revision:
            raise ValueError('Abschnittsdatei wurde ersetzt')
    return path, lo, hi, offset


def plan_frames(info, manifest, meta):
    start, end = info['section']
    moment = info['moment_s']
    if moment >= end:
        return None
    candidates = []
    for section in manifest.get('sections', []):
        lo, hi = float(section['start']), float(section['end'])
        if not lo <= moment < hi:
            continue
        try:
            path, lo, hi, offset = section_source(section, manifest, meta)
            if 'file_duration' in section:
                hi = min(hi, offset + number(section['file_duration']))
            if 'duration_s' in meta and 'local' in (section.get('mode'), manifest.get('source'), meta.get('source')):
                hi = min(hi, number(meta['duration_s']))
        except (ValueError, OSError, KeyError, TypeError):
            continue
        if lo <= moment < hi:
            candidates.append((lo <= start and end <= hi, path, lo, hi, offset))
    if candidates:
        _, path, lo, hi, offset = max(candidates, key=lambda item: item[0])
    else:
        path = Path(str(meta['input'])).expanduser().resolve() if meta.get('input') else None
        if not path or not path.is_file() or meta.get('source') not in (None, 'local'):
            return None
        lo, hi, offset = start, end, 0
        if 'duration_s' in meta:
            hi = min(hi, number(meta['duration_s']))
    lo, hi = max(lo, start), min(hi, end)
    if not lo <= moment < hi:
        return None
    stop = max(moment, hi - min(.1, (hi - lo) / 4))
    times = [max(lo, moment - 3), moment, min(stop, moment + 3)]
    return path, offset, times


def extract_frames(ffmpeg, plan, scratch):
    from PIL import Image

    path, offset, times = plan
    frames = {}
    png = scratch / 'frame.png'
    for at in dict.fromkeys(times):
        png.unlink(missing_ok=True)
        proc = _lib.run([
            ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-protocol_whitelist', 'file',
            '-ss', f'{at - offset:.6f}', '-i', str(path), '-map', '0:V:0', '-frames:v', '1',
            '-vf', (f"scale=w='max(1,min({TILE_W},{TILE_H}*dar))':"
                    f"h='max(1,min({TILE_H},{TILE_W}/dar))',setsar=1"),
            '-threads', '1', '-y', str(png),
        ], timeout=15)
        if proc.returncode or not png.is_file():
            return None
        try:
            with Image.open(png) as image:
                frames[at] = image.convert('RGB')
        except OSError:
            return None
    return [frames[at] for at in times]


def save_sheet(work, rows):
    from PIL import Image, ImageDraw

    header, row_h = 37, TILE_H + 44
    sheet = Image.new('RGB', (3 * TILE_W, header + row_h * len(rows)), '#121212')
    draw = ImageDraw.Draw(sheet)
    draw.text((10, 11), 'KANDIDATENVORSCHAU  |  Zeiten relativ zum VOD  |  Score = Erkennungssignal', fill='white')
    for i, (info, frames) in enumerate(rows):
        y = header + i * row_h
        draw.text((10, y + 4), f"{info['id']}   Signal {info['score']:g}   Fenster {info['section'][0]:g}–{info['section'][1]:g} s", fill='white')
        for j, (label, at, frame) in enumerate(zip(('Aufbau', 'Moment', 'Reaktion'), info['frame_times_s'], frames)):
            x = j * TILE_W
            sheet.paste(frame, (x + (TILE_W - frame.width) // 2, y + 22 + (TILE_H - frame.height) // 2))
            draw.text((x + 10, y + TILE_H + 26), f'{label}  {at:.2f} s', fill='white')
    dest = work / 'previews'
    if dest.is_symlink():
        raise ValueError('Vorschauordner darf kein Symlink sein')
    dest.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest, prefix='shortlist-', suffix='.jpg', delete=False) as handle:
        path = Path(handle.name)
        try:
            sheet.save(handle, 'JPEG', quality=87, optimize=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vod-id', required=True)
    select = parser.add_mutually_exclusive_group()
    select.add_argument('--candidates', help='Kommagetrennte Kandidaten-IDs in gewünschter Reihenfolge')
    select.add_argument('--limit', type=int, default=3, help=f'Top 1–{LIMIT} nach Erkennungssignal (Standard: 3)')
    parser.add_argument('--dry-run', action='store_true', help='Nur Liste/Metadaten; kein FFmpeg, keine Ausgabedateien')
    args = parser.parse_args()
    _lib.setup('preview_candidates')
    try:
        if args.vod_id != _lib.safe_id(args.vod_id) or not 1 <= args.limit <= LIMIT:
            raise ValueError(f'ungültige VOD-ID oder Limit außerhalb 1–{LIMIT}')
        home = _lib.clips_home().resolve()
        work = _lib.work_dir(args.vod_id).resolve()
        if not work.is_relative_to(home / 'work'):
            raise ValueError('VOD-Arbeitsordner liegt außerhalb von CLIPS_HOME/work')
        infos = [candidate_info(r) for r in select_candidates(work, args.candidates, args.limit)]
        output = {'vod_id': args.vod_id, 'candidates': infos, 'contact_sheet': None}
        if args.dry_run:
            _lib.emit(dict(output, status='planned'))
        manifest = _lib.read_json(work / 'sections.json') if (work / 'sections.json').is_file() else {}
        meta = _lib.read_json(work / 'meta.json') if (work / 'meta.json').is_file() else {}
        sections = manifest.get('sections', []) if isinstance(manifest, dict) else None
        if not isinstance(sections, list) or not all(isinstance(s, dict) for s in sections):
            raise ValueError('sections.json: erwartet ein Objekt mit einer Liste von Abschnitten')
        if not isinstance(meta, dict):
            raise TypeError('meta.json: erwartet ein Objekt')
        plans = [plan_frames(info, manifest, meta) for info in infos]
        rows = []
        if any(plan is not None for plan in plans):
            ffmpeg = _lib.require_tool('ffmpeg')
            with tempfile.TemporaryDirectory(prefix='clip-preview-') as staging:
                for info, plan in zip(infos, plans):
                    if plan is None:
                        info['status'] = 'missing_media'
                        continue
                    frames = extract_frames(ffmpeg, plan, Path(staging))
                    if frames is None:
                        info['status'] = 'frame_error'
                        continue
                    info.update(status='previewed', frame_times_s=plan[2])
                    rows.append((info, frames))
        else:
            for info in infos:
                info['status'] = 'missing_media'
        if not rows:
            _lib.emit(dict(output, status='no_previews'), _lib.EXIT_ERROR)
        output['contact_sheet'] = str(save_sheet(work, rows))
        _lib.emit(dict(output, status='ready' if len(rows) == len(infos) else 'partial'))
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        _lib.fail(f'Vorschau nicht erstellt: {exc}', _lib.EXIT_INVALID)


if __name__ == '__main__':
    main()
