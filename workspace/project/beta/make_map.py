# -*- coding: utf-8 -*-
"""make_map.py — карта сектора для бета-документа Sector Watch: 40k (выносные плашки)."""
import math
from uikit import *

W, H = 1760, 1180
img = Image.new("RGB", (W, H), (16, 17, 22))
stars(img, 320, seed=7)
nebula(img, 1500, 220, 260, (90, 40, 130), 110)
nebula(img, 300, 900, 300, (30, 60, 90), 60)
nebula(img, 900, 600, 320, (60, 45, 30), 45)
d = ImageDraw.Draw(img)

d = frame(img, title="", sub="Mechanicus • Дух Машины слышит всех и каждого • схема маршрутов прогрессии базы «pack_imperium»")

BIG = ImageFont.truetype(FDIR + "LiberationSans-Bold.ttf", 44)
lsp(d, (60, 40), "SECTOR WATCH: 40K", BIG, (235, 205, 130), 7)
maltese(d, 44, 62, 14, (235, 205, 130))
d.text((62, 100), "КАРТА ОПЕРАЦИЙ • база «pack_imperium» • сторона: Империум • 6 локаций • схема маршрутов прогрессии, не география", font=SMALL, fill=MUTED)
flourish(d, 60, W - 60, 138, cog_r=9)

ARTLOC = {"hive": "art_loc_hive.png", "waste": "art_loc_ash.png", "tomb": "art_loc_tomb.png",
        "forge": "art_loc_forge.png", "bastion": "art_loc_betray.png", "eye": "art_loc_eye.png"}

LOCS = [
    (1, "Улей «Терциус-Прайм»", "ур. 1–5", (87, 202, 110), (330, 880), "hive",
     "гангеры, мутанты, одичавшие твари, первые культисты", "мини-босс: Ганг-барон", (480, 950)),
    (2, "Пепельные Пустоши", "ур. 5–10", (170, 210, 90), (560, 760), "waste",
     "ренегаты, падальщики, радиоактивные звери", "мини-босс: Вождь пустошей", (300, 520)),
    (3, "Могильный мир «Стигмата»", "ур. 10–15", (250, 190, 70), (850, 610), "tomb",
     "гаунты, ормагаунты, генокрады", "босс: Карнифекс-авангард", (900, 790)),
    (4, "Руины Кузницы «Омега-Два»", "ур. 15–20", (250, 140, 50), (1130, 690), "forge",
     "сервиторы-мятежники, скрапкод-конструкты", "босс: Технодемон Кузницы", (860, 392)),
    (5, "Бастион Предателей «Багряный Рассвет»", "ур. 20–25", (240, 90, 60), (1380, 460), "bastion",
     "культисты, ренегаты-псайкеры, младшие демоны", "босс: Магистр культа", (1290, 780)),
    (6, "Око Ужаса", "ур. 25–30", (190, 90, 230), (1600, 240), "eye",
     "кровожады, чумоносцы, геральды варпа", "рейд-боссы эндгейма", (1180, 170)),
]

def icon(kind, cx, cy, col):
    if kind == "hive":
        for dx, hh in ((-26, 46), (0, 66), (26, 40)):
            d.polygon([(cx + dx - 14, cy + 26), (cx + dx + 14, cy + 26), (cx + dx, cy + 26 - hh)], fill=col)
    elif kind == "waste":
        d.polygon([(cx - 40, cy + 26), (cx - 8, cy - 34), (cx + 16, cy + 26)], fill=col)
        d.polygon([(cx + 2, cy + 26), (cx + 26, cy - 18), (cx + 46, cy + 26)], fill=tuple(c * 7 // 10 for c in col))
    elif kind == "tomb":
        d.polygon([(cx - 12, cy + 28), (cx + 12, cy + 28), (cx + 9, cy - 36), (cx, cy - 46), (cx - 9, cy - 36)], fill=col)
        d.line([(cx - 7, cy - 14), (cx + 7, cy - 14)], fill=(16, 17, 22), width=4)
        d.line([(cx - 6, cy + 2), (cx + 6, cy + 2)], fill=(16, 17, 22), width=4)
    elif kind == "forge":
        d.ellipse((cx - 30, cy - 30, cx + 30, cy + 30), fill=col)
        d.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=(16, 17, 22))
        for a in range(8):
            ang = a * math.pi / 4
            d.line([(cx + 28 * math.cos(ang), cy + 28 * math.sin(ang)),
                    (cx + 40 * math.cos(ang), cy + 40 * math.sin(ang))], fill=col, width=10)
    elif kind == "bastion":
        d.rectangle((cx - 26, cy - 18, cx + 26, cy + 28), fill=col)
        for i in range(3):
            d.rectangle((cx - 26 + i * 18, cy - 30, cx - 14 + i * 18, cy - 18), fill=col)
        d.line([(cx, cy - 30), (cx, cy - 54)], fill=col, width=4)
        d.polygon([(cx, cy - 54), (cx + 26, cy - 46), (cx, cy - 38)], fill=(220, 60, 50))
    elif kind == "eye":
        d.ellipse((cx - 44, cy - 26, cx + 44, cy + 26), outline=col, width=6)
        d.ellipse((cx - 14, cy - 14, cx + 14, cy + 14), fill=col)
        d.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=(16, 17, 22))

# маршруты прогрессии
for i in range(len(LOCS) - 1):
    dashed_line(d, LOCS[i][4], LOCS[i + 1][4], (110, 100, 80), w=3)

def callout(num, name, lvl, col, node, foes, boss, box):
    bx, by = box
    bw, bh = 380, 150
    # лидер-линия от узла к плашке
    nx, ny = node
    tx, ty = bx + bw // 2, by + bh // 2
    ang = math.atan2(ny - ty, nx - tx)
    px, py = nx - 66 * math.cos(ang), ny - 66 * math.sin(ang)
    qx = bx + (0 if tx > bx + bw else bw if tx < bx else bw // 2)
    qy = by + (0 if ty > by + bh else bh if ty < by else bh // 2)
    dashed_line(d, (px, py), (qx, qy), tuple(c // 2 + 40 for c in col), w=2, dash=8, gap=7)
    ov = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.rounded_rectangle((bx, by, bx + bw, by + bh), 8, fill=(20, 21, 26, 215), outline=col + (255,), width=2)
    img.paste(Image.alpha_composite(img.convert("RGBA"), ov).crop((bx, by, bx + bw, by + bh)), (bx, by))
    d2 = ImageDraw.Draw(img)
    globals()["d"] = d2
    lbw = int(SMALL.getlength(lvl)) + 18
    d2.rounded_rectangle((bx + bw - lbw - 10, by + 10, bx + bw - 10, by + 36), 12, fill=(30, 31, 34), outline=col, width=2)
    d2.text((bx + bw - lbw - 1, by + 12), lvl, font=SMALL, fill=col)
    # имя с переносом (не шире bw минус бейдж)
    lim = bw - 28 - lbw - 14
    words, cur, nl = name.split(), "", 0
    yy = by + 10
    for w_ in words:
        t = (cur + " " + w_).strip()
        if SMALLB.getlength(t) > lim and cur:
            d2.text((bx + 14, yy), cur, font=SMALLB, fill=HEAD); yy += 26; cur = w_; nl += 1
        else:
            cur = t
    if cur:
        d2.text((bx + 14, yy), cur, font=SMALLB, fill=HEAD); yy += 26; nl += 1
    # перенос врагов
    words, cur = foes.split(), ""
    yy = max(yy + 8, by + 44)
    for w_ in words:
        t = (cur + " " + w_).strip()
        if SMALL.getlength(t) > bw - 30:
            d2.text((bx + 14, yy), cur, font=TINY, fill=MUTED); yy += 20; cur = w_
        else:
            cur = t
    if cur: d2.text((bx + 14, yy), cur, font=TINY, fill=MUTED); yy += 20
    d2.text((bx + 14, by + bh - 26), boss, font=TINY, fill=(200, 120, 90))
    # номер на узле
    d2.ellipse((nx + 40, ny - 70, nx + 72, ny - 38), fill=col)
    d2.text((nx + 50, ny - 66), str(num), font=FB, fill=(16, 17, 22))

# узлы
for num, name, lvl, col, (cx, cy), kind, foes, boss, box in LOCS:  # noqa
    d.ellipse((cx - 62, cy - 62, cx + 62, cy + 62), fill=(24, 25, 31), outline=col, width=4)
    vig = art_card(ADIR + ARTLOC[kind], 108, 108).point(lambda v: int(v * 0.78))
    vm = Image.new("L", (108, 108), 0)
    ImageDraw.Draw(vm).ellipse((2, 2, 106, 106), fill=255)
    img.paste(vig, (cx - 54, cy - 54), vm)
    d = ImageDraw.Draw(img)
    d.ellipse((cx - 54, cy - 54, cx + 54, cy + 54), outline=tuple(c // 2 + 60 for c in col), width=1)
    d.ellipse((cx - 62, cy - 62, cx + 62, cy + 62), outline=col, width=4)
    d.ellipse((cx - 70, cy - 70, cx + 70, cy + 70), outline=tuple(c // 3 for c in col), width=2)
    icon(kind, cx, cy, col)
for num, name, lvl, col, node, _k, foes, boss, box in LOCS:
    callout(num, name, lvl, col, node, foes, boss, box)
    d = globals()["d"]

# легенда
lx, ly = 60, 960
rr(d, (lx, ly, lx + 390, ly + 180), 10, fill=(24, 25, 31), outline=(70, 60, 40), width=2)
d.text((lx + 20, ly + 12), "ЛЕГЕНДА", font=SMALLB, fill=(235, 205, 130))
d.text((lx + 20, ly + 42), "• цвет кольца — порог угрозы локации", font=TINY, fill=TEXT)
d.text((lx + 20, ly + 64), "• пунктир — маршрут прогрессии (по min_level)", font=TINY, fill=TEXT)
d.text((lx + 20, ly + 86), "• /сектор карта показывает только локации", font=TINY, fill=TEXT)
d.text((lx + 34, ly + 104), "стороны вашего активного персонажа", font=TINY, fill=TEXT)
d.text((lx + 20, ly + 126), "• мини-босс — в соло-операции, босс локаций 3–6 —", font=TINY, fill=TEXT)
d.text((lx + 34, ly + 144), "также рейд-босс отряда 5–10 / 10–20", font=TINY, fill=TEXT)

img.save("assets/map_sector.png")
print("map ok", img.size)
