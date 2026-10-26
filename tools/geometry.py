"""Builds crop geometry (coordinates only, no booklet content) for Form 608.
Usage: python3 geometry.py booklet.pdf out.json  (needs poppler-utils, numpy, pillow)"""
import re, html, json, sys, subprocess, tempfile, os
import numpy as np
from PIL import Image

PDF, OUT = sys.argv[1], sys.argv[2]
SECTIONS = {'english': (2, 11, 48), 'math': (12, 25, 36), 'reading': (26, 33, 33), 'science': (34, 45, 36)}
COLS = [(36.0, 300.0), (312.0, 576.0)]
TOP, BOTTOM = 84.0, 734.0
DPI = 144; S = DPI / 72

tmp = tempfile.mkdtemp()
subprocess.run(['pdftotext', '-bbox-layout', PDF, f'{tmp}/b.html'], check=True)
subprocess.run(['pdftoppm', '-r', str(DPI), '-gray', PDF, f'{tmp}/p'], check=True)
raw = open(f'{tmp}/b.html').read()
pages = re.findall(r'<page width="[\d.]+" height="[\d.]+">(.*?)</page>', raw, re.S)
imgs = {}
def img(p):
    if p not in imgs:
        f = [x for x in os.listdir(tmp) if re.fullmatch(rf'p-0*{p}\.pgm', x)][0]
        imgs[p] = np.array(Image.open(f'{tmp}/{f}'))
    return imgs[p]

def lines_of(p):
    out = []
    for ln in re.findall(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', pages[p - 1], re.S):
        ws = [(float(a), float(b), float(c), float(d), html.unescape(t).replace('​', '').strip())
              for a, b, c, d, t in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', ln[4])]
        ws = [w for w in ws if w[4]]
        if ws: out.append({'x0': float(ln[0]), 'y0': float(ln[1]), 'x1': float(ln[2]), 'y1': float(ln[3]), 'words': ws})
    return out

def colof(x): return 0 if x < 306 else 1

def dark(p, x0, y0, x1, y1, thr=170):
    a = img(p)[int(y0 * S):int(y1 * S), int(x0 * S):int(x1 * S)]
    return a < thr

def trim(p, r, sides='br', pad=2.5):
    x0, y0, x1, y1 = r
    m = dark(p, x0, y0, x1, y1)
    if not m.any(): return None
    rows = np.where(m.any(axis=1))[0]; cols = np.where(m.any(axis=0))[0]
    if 'b' in sides: y1 = min(y1, y0 + (rows[-1] + 1) / S + pad)
    if 't' in sides: y0 = max(y0, y0 + rows[0] / S - pad)
    if 'r' in sides: x1 = min(x1, x0 + (cols[-1] + 1) / S + pad)
    return [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)]

data = {}
for sec, (pa, pb, n) in SECTIONS.items():
    allwords = {p: lines_of(p) for p in range(pa, pb + 1)}
    # all numeric line-first markers and headings in reading order
    marks, heads = [], []
    for p in range(pa, pb + 1):
        for L in allwords[p]:
            w = L['words'][0]
            m = re.fullmatch(r'(\d{1,2})\.', w[4])
            if m: marks.append({'n': int(m.group(1)), 'p': p, 'c': colof(w[0]), 'x': w[0], 'y': w[1], 'y1': w[3]})
            if w[4] in ('PASSAGE', 'Passage') and len(L['words']) > 1 and re.fullmatch(r'[IVX]+', L['words'][1][4]):
                heads.append({'p': p, 'c': colof(w[0]), 'y': w[1]})
            if w[4] == 'END' and len(L['words']) > 2 and L['words'][2][4] == 'TEST':
                heads.append({'p': p, 'c': 0, 'y': w[1], 'end': True})
    key = lambda o: (o['p'], o['c'], o['y'])
    marks.sort(key=key); heads.sort(key=key)

    def words_in(p, c, ya, yb):
        out = []
        for L in allwords[p]:
            for w in L['words']:
                if colof(w[0]) == c and ya <= w[1] < yb: out.append(w)
        return out

    def has_choices(m, nxt_y):
        ws = words_in(m['p'], m['c'], m['y'], nxt_y)
        return all(any(w[4] == f'{L}.' for w in ws) for L in 'ABCD')

    qs = {}
    prev = (0, 0, 0)
    for q in range(1, n + 1):
        found = None
        for i, m in enumerate(marks):
            if m['n'] != q or m['p'] < prev[0]: continue
            later = [o for o in marks[i + 1:] if o['p'] == m['p'] and o['c'] == m['c']]
            nxt_y = later[0]['y'] if later else BOTTOM
            if has_choices(m, nxt_y): found = (m, nxt_y); break
        assert found, (sec, q)
        m, nxt_y = found
        prev = key(m)
        cx0, cx1 = COLS[m['c']]
        if sec == 'math': cx0, cx1 = 36.0, 340.0
        # limit: next marker, heading, end-of-test in same column
        lim = nxt_y
        for h in heads:
            if h['p'] == m['p'] and (h['c'] == m['c'] or h.get('end')) and h['y'] > m['y']: lim = min(lim, h['y'])
        lim = min(lim, BOTTOM) - 3
        ws = words_in(m['p'], m['c'], m['y'], lim + 3)
        letters = {}
        for w in ws:
            for L in 'ABCD':
                if w[4] == f'{L}.' and L not in letters: letters[L] = w
        # text vs graphic choices
        def has_text(lw):
            ym = (lw[1] + lw[3]) / 2
            return any(abs((w[1] + w[3]) / 2 - ym) < 3 and w[0] > lw[2] - 1 and w[0] < lw[2] + 11
                       and w[4] not in ('A.', 'B.', 'C.', 'D.', 'x', 'y', 'O') for w in ws)
        textual = all(has_text(letters[L]) for L in 'ABCD')
        # choice pitch: roomy layouts (fractions, radicals, exponents) get cut in the widest white gap
        def above_of(lw):
            c = [o for o in letters.values() if abs(o[0] - lw[0]) < 15 and o[1] < lw[1] - 4]
            return max(c, key=lambda o: o[1]) if c else None
        def below_of(lw):
            c = [o for o in letters.values() if abs(o[0] - lw[0]) < 15 and o[1] > lw[1] + 4]
            return min(c, key=lambda o: o[1]) if c else None
        pitches = [below_of(w)[1] - w[1] for w in letters.values() if below_of(w)]
        roomy = (min(pitches) if pitches else 99) >= 11
        def gapcut(x0, x1, ya, yb, fallback):
            if yb - ya < 1: return fallback
            mk = dark(m['p'], x0, ya, x1, yb, thr=200).any(axis=1)
            best = None; i = 0
            while i < len(mk):
                if not mk[i]:
                    j = i
                    while j < len(mk) and not mk[j]: j += 1
                    if best is None or (j - i) >= best[1] - best[0]: best = (i, j)
                    i = j
                else: i += 1
            if not best or best[1] - best[0] < 2: return fallback
            return ya + (best[0] + best[1]) / 2 / S
        def xr(lw):
            right = [o for o in letters.values() if abs(o[1] - lw[1]) < 4 and o[0] > lw[0]]
            return lw[0] - 3, (min(o[0] for o in right) - 4 if right else cx1)
        def raised(lw):
            return any(w[0] > lw[2] and w[0] < lw[2] + 60 and w[1] < lw[1] - 1.5 and w[3] > lw[1] + 2 and w[4] not in ('A.', 'B.', 'C.', 'D.') for w in ws)
        tops = {}
        for L in 'ABCD':
            lw = letters[L]; ab = above_of(lw); x0, x1 = xr(lw)
            if ab:
                prevb = [w[3] for w in ws if w[1] >= ab[1] - 1 and w[1] < lw[1] - 1 and w[3] < lw[1] + 2.5 and w[0] >= ab[0] - 1]
                ya = max([ab[1] + 3] + [b - 4 for b in prevb])
            else:
                ya = max(m['y'] + 10, lw[1] - 24)
            use_gap = raised(lw) or (sec == 'math' and roomy)
            tops[L] = gapcut(x0, x1, ya, lw[1] + 2.5, lw[1] + 1.5) if use_gap else lw[1] + 1.5
        # bottom of question
        if textual and not (sec == 'math' and roomy):
            last = max(letters.values(), key=lambda w: w[1])
            bottom = last[3]
            lns = sorted([L for L in allwords[m['p']] if colof(L['x0']) == m['c'] and L['y0'] >= last[1] - 2 and L['y0'] < lim + 3], key=lambda L: L['y0'])
            for L in lns:
                if L['y0'] <= last[1] + 3: bottom = max(bottom, L['y1']); continue
                if L['y0'] - bottom < 5 and L['x0'] >= last[0] + 4: bottom = L['y1']
                else: break
            bottom = min(bottom + 1, (nxt_y if nxt_y < BOTTOM else 760) - 1, 739.5)
        else:
            bottom = lim
        stem = [m['x'] - 3, m['y'] - 3, cx1, min(tops[L] for L in 'ABCD' if not above_of(letters[L]))]
        stem = trim(m['p'], stem)
        ch = []
        for L in 'ABCD':
            lw = letters[L]; x0, x1 = xr(lw); bl = below_of(lw)
            y1 = tops[[K for K in 'ABCD' if letters[K] is bl][0]] if bl else bottom
            ch.append(trim(m['p'], [x0, tops[L], x1, y1]))
        qs[q] = {'p': m['p'], 'c': m['c'], 'stem': stem, 'ch': ch, 'y0': stem[1], 'y1': max(r[3] for r in ch)}

    # 'Questions N and M ask about the preceding passage' notes: keep out of passages
    notes = {}
    for p in range(pa, pb + 1):
        for L in allwords[p]:
            w = L['words']
            if w[0][4] in ('Question', 'Questions') and len(w) > 1 and re.fullmatch(r'\d+', w[1][4]):
                notes.setdefault(p, []).append((L['y0'] - 12, L['y1'] + 12))
    # passages
    passages, qpass = [], {}
    if sec != 'math':
        real_heads = [h for h in heads if not h.get('end')]
        endh = [h for h in heads if h.get('end')][0]
        cols = [0] if sec == 'english' else [0, 1]
        for k, h in enumerate(real_heads):
            nh = real_heads[k + 1] if k + 1 < len(real_heads) else endh
            segs = []
            for p in range(h['p'], nh['p'] + 1):
                for c in cols:
                    if (p, c) < (h['p'], h['c']) or (p, c) > (nh['p'], nh['c']): continue
                    ya = TOP; yb = BOTTOM
                    if p == h['p'] and c == h['c']: ya = h['y'] - 3
                    if p == h['p'] and p == pa: ya = max(ya, h['y'] - 3)
                    if p == nh['p'] and c == nh['c']: yb = nh['y'] - 3
                    if nh.get('end') and p == nh['p']: yb = min(yb, nh['y'] - 3)
                    # subtract question regions in this column
                    cuts = sorted([(Q['y0'] - 2, Q['y1'] + 2) for Q in qs.values() if Q['p'] == p and Q['c'] == c] + notes.get(p, []))
                    if sec == 'english' and c == 1: continue
                    cur = ya
                    pieces = []
                    for a, b in cuts:
                        if b <= cur or a >= yb: continue
                        if a > cur: pieces.append((cur, a))
                        cur = max(cur, b)
                    if cur < yb: pieces.append((cur, yb))
                    if sec == 'english':
                        split = []
                        for a, b in pieces:
                            m2 = dark(p, COLS[c][0], a, COLS[c][1], b).any(axis=1)
                            rows = np.where(m2)[0] / S + a
                            if not len(rows): continue
                            st = rows[0]; pr = rows[0]
                            for r in rows[1:]:
                                if r - pr > 22: split.append((st - 3, pr + 3)); st = r
                                pr = r
                            split.append((st - 3, pr + 3))
                        pieces = split
                    for a, b in pieces:
                        if b - a < 8: continue
                        x0, x1 = COLS[c]
                        r = trim(p, [x0, a, x1, b], sides='tb')
                        if r and r[3] - r[1] > 6: segs.append([p] + r)
            passages.append(segs)
        # question -> passage
        def in_passage(p, c, y):
            for k, segs in enumerate(passages):
                for s in segs:
                    if s[0] == p and COLS[colof(s[1] + 1)] and colof(s[1] + 1) == c and s[2] - 4 <= y <= s[4] + 4: return k
            return None
        hk = [(h['p'], h['c'], h['y']) for h in real_heads]
        for q, Q in qs.items():
            k = None
            if sec == 'english':
                # underline reference number in the passage column
                best = None
                for p in range(pa, Q['p'] + 1):
                    for L in allwords[p]:
                        for w in L['words']:
                            if w[4] == str(q) and colof(w[0]) == 0 and (w[3] - w[1]) < 9:
                                best = (p, w)
                if best:
                    p, w = best
                    Q['ref'] = [p, round(w[0], 1), round(w[1], 1), round(w[2], 1), round(w[3], 1)]
                    k = in_passage(p, 0, w[1])
                    # every line of the underlined phrase carries the number; find each underline
                    occ = [(p, x) for L in allwords[p] for x in L['words']
                           if x[4] == str(q) and colof(x[0]) == 0 and (x[3] - x[1]) < 9 and abs(x[1] - w[1]) < 60]
                    marks = []
                    for pp, x in occ:
                        cx = (x[0] + x[2]) / 2
                        a = img(pp); best_run = None
                        for yy in range(int((x[1] - 4.5) * S), int((x[1] + 1) * S)):
                            row = a[yy] < 140; ci = int(cx * S)
                            if not row[ci - 3:ci + 4].any(): continue
                            j = ci - 3 + int(np.argmax(row[ci - 3:ci + 4]))
                            l = j; r = j
                            while l > int(36 * S) and (row[l - 1] or row[l - 2]): l -= 1
                            while r < int(300 * S) and (row[r + 1] or row[r + 2]): r += 1
                            if (r - l) / S > 6 and (best_run is None or r - l > best_run[2] - best_run[1]): best_run = (yy, l, r)
                        if best_run:
                            yy, l, r = best_run
                            marks.append([pp, round(l / S - 1, 1), round(yy / S - 11.5, 1), round(r / S + 1, 1), round(yy / S + 1, 1)])
                    if marks: Q['marks'] = marks
            if k is None and sec == 'english' and q > 1:
                k = qs[q - 1]['k']
            if k is None:
                pos = (Q['p'], Q['c'], Q['y0'])
                k = max([i for i, h in enumerate(hk) if h <= pos], default=0)
            Q['k'] = k
    data[sec] = {'q': [qs[i] for i in range(1, n + 1)], 'passages': passages}

json.dump(data, open(OUT, 'w'), separators=(',', ':'))
for sec in data:
    print(sec, 'questions', len(data[sec]['q']), 'passages', [len(s) for s in data[sec]['passages']],
          'q->passage', [Q.get('k') for Q in data[sec]['q']] if sec != 'math' else '')
