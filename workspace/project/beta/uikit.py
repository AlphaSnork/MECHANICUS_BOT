# -*- coding: utf-8 -*-
"""uikit.py — рендер мокапов Discord-интерфейса «Sector Watch: 40k» (Pillow).

Редакция «Mechanicus»: бот 𝙼𝚌𝚑𝚗𝚒𝚌𝚞𝚜 (Mechanicus#1701), орнаментальные рамы,
шестерни, мальтийские кресты, Flourish-разделители и иллюстрации к каждому действию
(стиль-вдохновитель — «Подземелья колодца»).
"""
import math, re, random, os
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
FDIR = os.path.join(HERE, "fonts") + os.sep
ADIR = os.path.join(HERE, "assets") + os.sep
FR   = ImageFont.truetype(FDIR + "LiberationSans-Regular.ttf", 22)
FB   = ImageFont.truetype(FDIR + "LiberationSans-Bold.ttf", 22)
FI   = ImageFont.truetype(FDIR + "LiberationSans-Italic.ttf", 22)
TITLE= ImageFont.truetype(FDIR + "LiberationSans-Bold.ttf", 28)
SMALL= ImageFont.truetype(FDIR + "LiberationSans-Regular.ttf", 18)
SMALLB=ImageFont.truetype(FDIR + "LiberationSans-Bold.ttf", 18)
TINY = ImageFont.truetype(FDIR + "LiberationSans-Regular.ttf", 16)
CAPS = ImageFont.truetype(FDIR + "LiberationSans-Bold.ttf", 15)
MICRO= ImageFont.truetype(FDIR + "LiberationSans-Regular.ttf", 13)
BIG  = ImageFont.truetype(FDIR + "LiberationSans-Bold.ttf", 40)

# палитра Discord (тёмная тема)
BG      = (49, 51, 56)
EMBED   = (43, 45, 49)
EMBED2  = (38, 40, 44)
HEAD    = (242, 243, 245)
TEXT    = (219, 222, 225)
MUTED   = (148, 155, 164)
PRIMARY = (88, 101, 242)
SECOND  = (78, 80, 88)
SUCCESS = (36, 128, 70)
DANGER  = (218, 55, 60)
WARN    = (250, 166, 26)
LINK    = (0, 168, 252)
# имперская позолота / омниссия
GOLD      = (198, 158, 66)
GOLD_DIM  = (126, 100, 50)
GOLD_FAINT= (78, 66, 40)
MECH_RED  = (146, 46, 40)
ONLINE    = (59, 163, 74)
# цвета редкостей
RAR = {
    "std":  (156, 163, 175),
    "sup":  (87, 202, 110),
    "bl":   (88, 140, 242),
    "sac":  (180, 120, 250),
    "rel":  (250, 168, 60),
    "art":  (255, 90, 90),
}
SIDE_ACCENT = GOLD
BOT_AVATAR  = MECH_RED
BOT_NAME    = "Mechanicus"
BOT_BIO     = "Дух Машины слышит всех и каждого"

_BRAND_AV = Image.open(ADIR + "brand_avatar.png").convert("RGBA")

def _runs(s):
    """разбор **bold** и ~~muted~~ в список (text, font)."""
    out = []
    pos = 0
    for m in re.finditer(r"\*\*(.+?)\*\*|~~(.+?)~~", s):
        if m.start() > pos:
            out.append((s[pos:m.start()], FR))
        if m.group(1) is not None:
            out.append((m.group(1), FB))
        else:
            out.append((m.group(2), FI))
        pos = m.end()
    if pos < len(s):
        out.append((s[pos:], FR))
    return out or [("", FR)]

def _runw(t, f):
    return f.getlength(t)

def wrap_rich(s, maxw, indent=""):
    """перенос строки с runs; возвращает список списков (text, font)."""
    words = []
    for t, f in _runs(s):
        parts = t.split(" ")
        for i, p in enumerate(parts):
            if i > 0:
                words.append((" ", f))
            if p:
                words.append((p, f))
    lines, cur, curw = [], [], 0.0
    first = True
    for w, f in words:
        ww = _runw(w, f)
        limit = maxw - (FR.getlength(indent) if first else 0)
        if curw + ww > limit and cur:
            lines.append(cur); cur, curw = [], 0.0
            first = False
        cur.append((w, f)); curw += ww
    if cur:
        lines.append(cur)
    return lines or [[]]

def draw_rich(d, xy, lines, fill=TEXT, indent="", lh=30):
    x0, y = xy
    for ln in lines:
        x = x0 + (FR.getlength(indent) if indent else 0)
        if indent:
            d.text((x0, y), indent, font=FR, fill=fill)
        for t, f in ln:
            d.text((x, y), t, font=f, fill=fill)
            x += _runw(t, f)
        y += lh
    return y

def rich_h(s, maxw, indent="", lh=30):
    return len(wrap_rich(s, maxw, indent)) * lh

def rr(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)

# ─────────────────────────── орнаменты (закорючки) ───────────────────────────
def cog(d, cx, cy, r, fill, teeth=8, hole=None, rot=0.0):
    """шестерня-омниссия: диск + зубья + осевое отверстие."""
    hole = hole or BG
    for i in range(teeth):
        a = rot + i * 2 * math.pi / teeth
        w, L = r * 0.30, r * 0.34
        pts = []
        for rad, tt in [(r * 0.70, -w), (r * 0.70 + L, -w * 0.65),
                        (r * 0.70 + L, w * 0.65), (r * 0.70, w)]:
            pts.append((cx + rad * math.cos(a) - tt * math.sin(a),
                        cy + rad * math.sin(a) + tt * math.cos(a)))
        d.polygon(pts, fill=fill)
    d.ellipse((cx - r * 0.78, cy - r * 0.78, cx + r * 0.78, cy + r * 0.78), fill=fill)
    d.ellipse((cx - r * 0.30, cy - r * 0.30, cx + r * 0.30, cy + r * 0.30), fill=hole)
    for i in range(4):
        a = rot + math.pi / 4 + i * math.pi / 2
        d.ellipse((cx + r * 0.52 * math.cos(a) - 1.5, cy + r * 0.52 * math.sin(a) - 1.5,
                   cx + r * 0.52 * math.cos(a) + 1.5, cy + r * 0.52 * math.sin(a) + 1.5), fill=hole)

def maltese(d, cx, cy, r, fill):
    """мальтийский крест (4 рукава с V-вырезом)."""
    for k in range(4):
        a = k * math.pi / 2
        ca, sa = math.cos(a), math.sin(a)
        loc = [(-0.16, -0.06), (0.16, -0.06), (0.42, -0.98), (0.0, -0.70), (-0.42, -0.98)]
        pts = [(cx + (px * ca - py * sa) * r, cy + (px * sa + py * ca) * r) for px, py in loc]
        d.polygon(pts, fill=fill)
    d.ellipse((cx - r * 0.20, cy - r * 0.20, cx + r * 0.20, cy + r * 0.20), fill=fill)

def diamond(d, cx, cy, r, fill):
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=fill)

def flourish(d, x1, x2, y, col=None, line=None, cog_r=7):
    """разделитель-флориш: линия ─ ромб ─ шестерня ─ ромб ─ линия."""
    col = col or GOLD_DIM
    line = line or GOLD_FAINT
    cm = (x1 + x2) / 2
    d.line([(x1 + 6, y), (cm - cog_r * 3.0, y)], fill=line, width=1)
    d.line([(cm + cog_r * 3.0, y), (x2 - 6, y)], fill=line, width=1)
    diamond(d, cm - cog_r * 2.1, y, 3, col)
    cog(d, cm, y, cog_r, col, teeth=8)
    diamond(d, cm + cog_r * 2.1, y, 3, col)
    d.ellipse((x1, y - 2, x1 + 4, y + 2), fill=col)
    d.ellipse((x2 - 4, y - 2, x2, y + 2), fill=col)

def corners(d, box, col, L=24):
    """угловые завитки рамы: двойной уголок + ромб."""
    x1, y1, x2, y2 = box
    for (X, Y, dx, dy) in [(x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)]:
        d.line([(X, Y + dy * L), (X, Y), (X + dx * L, Y)], fill=col, width=2)
        d.line([(X + dx * 5, Y + dy * (L - 8)), (X + dx * 5, Y + dy * 5),
                (X + dx * (L - 8), Y + dy * 5)], fill=col, width=1)
        diamond(d, X + dx * 12, Y + dy * 12, 3, col)

def lsp(d, xy, text, font, fill, tracking=3):
    """разрядка (letter-spacing) — для капителей."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking
    return x

def mono(d, xy, text, font, fill, adv=None):
    """пишущая машинка (имитация 𝙼𝚎𝚌𝚑𝚊𝚗𝚒𝚌𝚜): фиксированный шаг глифа."""
    x, y = xy
    adv = adv or (max(font.getlength(c) for c in text) + 1.5)
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += adv
    return x

def frame(img, title="SECTOR WATCH: 40K", sub=None):
    """двойная золотая рама полотна с угловыми завитками и титульной плашкой."""
    sub = sub if sub is not None else (BOT_NAME + " • " + BOT_BIO)
    d = ImageDraw.Draw(img)
    W, H = img.size
    d.rectangle((8, 8, W - 9, H - 9), outline=GOLD_DIM, width=2)
    d.rectangle((15, 15, W - 16, H - 16), outline=GOLD_FAINT, width=1)
    corners(d, (8, 8, W - 9, H - 9), GOLD)
    if title:
        f = CAPS
        tw = f.getlength(title) + len(title) * 3
        px = (W - tw) / 2
        d.rectangle((px - 46, 3, px + tw + 46, 28), fill=BG)
        diamond(d, px - 40, 15, 3, GOLD)
        cog(d, px - 22, 15, 9, GOLD)
        lsp(d, (px, 7), title, f, GOLD, 3)
        cog(d, px + tw + 22, 15, 9, GOLD)
        diamond(d, px + tw + 40, 15, 3, GOLD)
    if sub:
        f2 = TINY
        sw = f2.getlength(sub)
        px = (W - sw) / 2
        d.rectangle((px - 30, H - 28, px + sw + 30, H - 4), fill=BG)
        diamond(d, px - 18, H - 17, 3, GOLD_DIM)
        d.text((px, H - 25), sub, font=f2, fill=GOLD_DIM)
        diamond(d, px + sw + 18, H - 17, 3, GOLD_DIM)
    return d

def bot_header(img, d, x=30, y=26, ts="сегодня в 21:47", name=BOT_NAME):
    """шапка сообщения: аватар Mechanicus + сетевой индикатор + имя + бейдж БОТ."""
    av = _BRAND_AV.resize((48, 48), Image.LANCZOS)
    img.paste(av, (x, y), av)
    d.ellipse((x + 34, y + 34, x + 50, y + 50), fill=BG)
    d.ellipse((x + 37, y + 37, x + 47, y + 47), fill=ONLINE)
    nx = mono(d, (x + 60, y + 2), name, FB, HEAD)
    nx = nx + 10
    rr(d, (nx, y + 6, nx + 52, y + 28), 5, fill=PRIMARY)
    d.text((nx + 8, y + 7), "БОТ", font=TINY, fill=(255, 255, 255))
    d.text((nx + 62, y + 8), ts, font=TINY, fill=MUTED)

def art_card(path, w, h):
    """иллюстрация действия: центрокроп под карточку w×h."""
    im = Image.open(path).convert("RGB")
    iw, ih = im.size
    tar = w / h
    if iw / ih > tar:
        nw = int(ih * tar)
        im = im.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
    else:
        nh = int(iw / tar)
        im = im.crop((0, (ih - nh) // 2, iw, (ih - nh) // 2 + nh))
    return im.resize((w, h), Image.LANCZOS)

def paste_art(img, d, path, x, y, w, h, cap=None):
    """вставляет иллюстрацию с скруглением, золотой обводкой и угловыми ромбами."""
    im = art_card(path, w, h)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), 8, fill=255)
    img.paste(im, (x, y), mask)
    rr(d, (x, y, x + w, y + h), 8, outline=GOLD_DIM, width=1)
    rr(d, (x + 3, y + 3, x + w - 4, y + h - 4), 6, outline=GOLD_FAINT, width=1)
    for (X, Y) in [(x + 8, y + 8), (x + w - 8, y + 8), (x + 8, y + h - 8), (x + w - 8, y + h - 8)]:
        diamond(d, X, Y, 3, GOLD)
    if cap:
        cw = MICRO.getlength(cap) + 18
        rr(d, (x + 8, y + h - 30, x + 8 + cw, y + h - 8), 4, fill=(20, 18, 14))
        d.text((x + 17, y + h - 27), cap, font=MICRO, fill=GOLD)
    return y + h

# ─────────────────────────── блоки сообщения ───────────────────────────
BTN_PFX = {"p": "► ", "s": "• ", "g": "† ", "r": "× "}

class Msg:
    """конструктор одного мокапа: список блоков."""
    def __init__(self, author=BOT_NAME, w=1160, ts="сегодня в 21:47"):
        self.author = author; self.w = w; self.ts = ts
        self.blocks = []
    def sysmsg(self, text):
        self.blocks.append(("sys", text)); return self
    def embed(self, accent=SIDE_ACCENT, author_line=None, title=None,
              desc=None, fields=None, footer=None, thumb=None, art=None, art_h=210):
        self.blocks.append(("embed", dict(accent=accent, author_line=author_line,
            title=title, desc=desc or [], fields=fields or [], footer=footer,
            thumb=thumb, art=art, art_h=art_h)))
        return self
    def buttons(self, row):
        self.blocks.append(("buttons", row)); return self
    def select(self, placeholder, opts=None, value=None):
        self.blocks.append(("select", placeholder, opts, value)); return self
    def note(self, text):
        self.blocks.append(("note", text)); return self

    # ---- измерение ----
    def _embed_size(self, e):
        W = self.w - 130
        inner = W - 46 - (70 if e["thumb"] else 0)
        h = 24
        if e["author_line"]: h += 32
        if e["title"]: h += rich_h(e["title"], inner, lh=38) + 20
        if e["art"]: h += e["art_h"] + 16
        for raw in e["desc"]:
            for sub in raw.split("\n"):
                h += rich_h(sub, inner) + 2
        if e["desc"]: h += 8
        for f in e["fields"]:
            name, val = f[0], f[1]
            for sub in name.split("\n"):
                h += rich_h(sub, inner, lh=28)
            for sub in val.split("\n"):
                h += rich_h(sub, inner)
            h += 16
        if e["footer"]: h += 30
        return W, max(h + 16, 60)

    def _btn_w(self, label):
        return int(max(FR.getlength(label), SMALL.getlength(label)) + 44)

    def total_h(self):
        h = 104
        for b in self.blocks:
            if b[0] == "sys": h += 44
            elif b[0] == "embed":
                _, eh = self._embed_size(b[1]); h += eh + 14
            elif b[0] == "buttons":
                h += 46 + 10
            elif b[0] == "select":
                h += 58
                if b[2]:
                    h += 8 + len(b[2]) * 44 + 10
            elif b[0] == "note":
                h += 34
        h += 46
        return h

    def render(self, path):
        W, H = self.w, self.total_h()
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        frame(img, title="SECTOR WATCH: 40K")
        d = ImageDraw.Draw(img)
        x0, y = 30, 100
        for b in self.blocks:
            if b[0] == "sys":
                d.text((x0 + 4, y + 6), b[1], font=SMALL, fill=MUTED)
                y += 44
            elif b[0] == "embed":
                e = b[1]
                ew, eh = self._embed_size(e)
                ex, ey = x0 + 60, y
                rr(d, (ex, ey, ex + ew, ey + eh), 8, fill=EMBED)
                rr(d, (ex, ey, ex + ew, ey + eh), 8, outline=(58, 54, 46), width=1)
                d.rounded_rectangle((ex, ey, ex + 8, ey + eh), 4, fill=e["accent"])
                # угловые засечки эмбеда
                for (X, Y, dx, dy) in [(ex + 5, ey + 5, 1, 1), (ex + ew - 5, ey + 5, -1, 1),
                                       (ex + 5, ey + eh - 5, 1, -1), (ex + ew - 5, ey + eh - 5, -1, -1)]:
                    d.line([(X, Y + dy * 9), (X, Y), (X + dx * 9, Y)], fill=GOLD_FAINT, width=1)
                cx = ex + 26
                cy = ey + 18
                inner = ew - 46 - (70 if e["thumb"] else 0)
                if e["thumb"]:
                    tx = ex + ew - 20 - 60
                    rr(d, (tx, ey + 16, tx + 60, ey + 16 + 60), 6, fill=e["thumb"])
                if e["author_line"]:
                    cog(d, cx + 8, cy + 8, 8, e["accent"], hole=EMBED)
                    d.text((cx + 24, cy - 4), e["author_line"], font=SMALLB, fill=HEAD)
                    cy += 32
                if e["title"]:
                    cy = draw_rich(d, (cx, cy - 4), wrap_rich(e["title"], inner), fill=HEAD, lh=38) + 4
                    flourish(d, cx, cx + inner, cy + 4, cog_r=6)
                    cy += 20
                if e["art"]:
                    cy = paste_art(img, d, e["art"], cx, cy, inner, e["art_h"]) + 16
                    d = ImageDraw.Draw(img)
                for raw in e["desc"]:
                    for sub in raw.split("\n"):
                        cy = draw_rich(d, (cx, cy), wrap_rich(sub, inner), fill=TEXT)
                if e["desc"]: cy += 8
                for i, f in enumerate(e["fields"]):
                    name, val = f[0], f[1]
                    col = f[2] if len(f) > 2 else None
                    for sub in name.split("\n"):
                        cy = draw_rich(d, (cx, cy - 4), wrap_rich(sub, inner), fill=HEAD, lh=28)
                    for sub in val.split("\n"):
                        cy = draw_rich(d, (cx, cy), wrap_rich(sub, inner), fill=col or TEXT)
                    cy += 6
                    if i < len(e["fields"]) - 1:
                        d.line([(cx, cy + 2), (cx + inner, cy + 2)], fill=(56, 58, 64), width=1)
                        diamond(d, cx + inner / 2, cy + 2, 3, (74, 76, 84))
                        cy += 10
                if e["footer"]:
                    cog(d, cx + 7, cy + 10, 7, GOLD_DIM, hole=EMBED)
                    d.text((cx + 20, cy + 2), e["footer"], font=TINY, fill=MUTED)
                y += eh + 14
            elif b[0] == "buttons":
                bx = x0 + 60
                for label, style in b[1]:
                    lab = BTN_PFX.get(style, "") + label
                    bw = self._btn_w(lab)
                    col = {"p": PRIMARY, "s": SECOND, "g": SUCCESS, "r": DANGER}[style]
                    rr(d, (bx, y, bx + bw, y + 42), 6, fill=col)
                    d.line([(bx + 4, y + 3), (bx + bw - 4, y + 3)], fill=(255, 255, 255, 40), width=1)
                    d.text((bx + 20, y + 8), lab, font=SMALL, fill=(255, 255, 255))
                    bx += bw + 12
                y += 52
            elif b[0] == "select":
                _, ph, opts, value = b
                sw = self.w - 190
                rr(d, (x0 + 60, y, x0 + 60 + sw, y + 48), 6, fill=EMBED2, outline=(30, 31, 34), width=2)
                rr(d, (x0 + 60, y, x0 + 60 + sw, y + 48), 6, outline=GOLD_FAINT, width=1)
                diamond(d, x0 + 76, y + 24, 4, GOLD_DIM)
                txt = value if value else ph
                d.text((x0 + 90, y + 11), txt, font=FR, fill=TEXT if value else MUTED)
                d.polygon([(x0 + 60 + sw - 34, y + 19), (x0 + 60 + sw - 18, y + 19),
                           (x0 + 60 + sw - 26, y + 30)], fill=MUTED)
                y += 58
                if opts:
                    oy = y
                    hh = 8 + len(opts) * 44
                    rr(d, (x0 + 60, oy, x0 + 60 + sw, oy + hh), 6, fill=(43, 45, 49),
                       outline=(30, 31, 34), width=2)
                    yy = oy + 8
                    for o in opts:
                        if isinstance(o, tuple):
                            name, desc = o
                        else:
                            name, desc = o, None
                        diamond(d, x0 + 74, yy + 10, 3, GOLD_DIM)
                        d.text((x0 + 86, yy + 2), name, font=SMALLB, fill=HEAD)
                        if desc:
                            d.text((x0 + 86 + SMALLB.getlength(name) + 14, yy + 5), desc, font=SMALL, fill=MUTED)
                        yy += 44
                    y = oy + hh + 10
            elif b[0] == "note":
                d.text((x0 + 64, y + 4), b[1], font=TINY, fill=(110, 115, 122))
                y += 34
        bot_header(img, d, x0, 26, self.ts, self.author)
        img.save(path)
        return path

# ─────────────────────────── вспомогательные рисунки ───────────────────────────
def hp_bar(d, x, y, w, h, frac, label="", colr=None):
    frac = max(0.0, min(1.0, frac))
    col = colr or ((87, 202, 110) if frac > 0.5 else (WARN if frac > 0.25 else DANGER))
    rr(d, (x, y, x + w, y + h), h // 2, fill=(30, 31, 34))
    if frac > 0:
        rr(d, (x, y, x + max(h, int(w * frac)), y + h), h // 2, fill=col)
    # насечки-деления
    for i in range(1, 4):
        gx = x + int(w * i / 4)
        d.line([(gx, y + 2), (gx, y + h - 2)], fill=(20, 21, 24), width=1)
    if label:
        d.text((x + w + 12, y + (h - 20) // 2), label, font=SMALL, fill=TEXT)

def stars(n_img, n=220, seed=40):
    random.seed(seed)
    d = ImageDraw.Draw(n_img)
    W, H = n_img.size
    for _ in range(n):
        x, y = random.randint(0, W), random.randint(0, H)
        r = random.choice([1, 1, 1, 2])
        c = random.randint(120, 235)
        d.ellipse((x, y, x + r, y + r), fill=(c, c, min(255, c + 15)))

def nebula(img, cx, cy, rad, color, alpha=90):
    ov = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(ov)
    for i in range(8):
        rr_ = rad - i * rad // 9
        d.ellipse((cx - rr_, cy - rr_ * 0.7, cx + rr_, cy + rr_ * 0.7), fill=int(alpha * (i + 1) / 8))
    ov = ov.filter(ImageFilter.GaussianBlur(40))
    tint = Image.new("RGB", img.size, color)
    img.paste(tint, (0, 0), ov)

def dashed_line(d, p1, p2, color, w=3, dash=14, gap=10):
    x1, y1 = p1; x2, y2 = p2
    dist = math.hypot(x2 - x1, y2 - y1)
    if dist == 0: return
    n = int(dist // (dash + gap))
    for i in range(n + 1):
        t0 = i * (dash + gap) / dist
        t1 = min(1.0, (i * (dash + gap) + dash) / dist)
        d.line([(x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0),
                (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)], fill=color, width=w)
