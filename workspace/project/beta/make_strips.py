# -*- coding: utf-8 -*-
"""make_strips.py — полоски-виньетки иллюстраций (классы и локации) для docx."""
import os
from uikit import *

HERE = os.path.dirname(os.path.abspath(__file__))
A = os.path.join(HERE, "assets") + os.sep

def strip(items, out, title):
    n = len(items)
    tw, th, gap = 300, 300, 14
    W = 2 * 24 + n * tw + (n - 1) * gap
    H = 24 + th + 74 + 24
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    frame(img, title=title, sub=None)
    d = ImageDraw.Draw(img)
    x = 24
    for fn, name, sub, col in items:
        im = art_card(A + fn, tw, th)
        mask = Image.new("L", (tw, th), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, tw - 1, th - 1), 10, fill=255)
        img.paste(im, (x, 24), mask)
        rr(d, (x, 24, x + tw, 24 + th), 10, outline=GOLD_DIM, width=1)
        # плашка имени
        rr(d, (x, 24 + th + 6, x + tw, 24 + th + 66), 6, fill=EMBED)
        d.rounded_rectangle((x, 24 + th + 6, x + 5, 24 + th + 66), 3, fill=col)
        d.text((x + 14, 24 + th + 12), name, font=SMALLB, fill=HEAD)
        d.text((x + 14, 24 + th + 38), sub, font=TINY, fill=MUTED)
        diamond(d, x + tw - 14, 24 + th + 16, 4, col)
        x += tw + gap
    img.save(A + out)
    print("strip", out, img.size)

strip([
    ("art_cls_astartes.png", "АСТАРТЕС", "танк / урон • фронт", (190, 60, 60)),
    ("art_cls_sororitas.png", "СЕСТРА БИТВЫ", "поддержка / хилер • вера", (230, 200, 90)),
    ("art_cls_guard.png", "ГВАРДЕЕЦ", "урон / призыв • расходники", (110, 150, 90)),
    ("art_cls_techpriest.png", "ТЕХНОЖРЕЦ", "поддержка • турели", (90, 160, 200)),
    ("art_cls_psyker.png", "ПСАЙКЕР", "AoE-урон / контроль • варп", (170, 100, 230)),
    ("art_cls_inquisitor.png", "ИНКВИЗИТОР", "гибрид • святые ордосы", (200, 200, 200)),
], "art_strip_classes.png", "КЛАССЫ БАЗЫ • СТОРОНА ИМПЕРИУМ")

strip([
    ("art_loc_hive.png", "Улей «Терциус-Прайм»", "ур. 1–5 • мини-босс: Ганг-барон", (87, 202, 110)),
    ("art_loc_ash.png", "Пепельные Пустоши", "ур. 5–10 • мини-босс: Вождь пустошей", (170, 210, 90)),
    ("art_loc_tomb.png", "Могильный мир «Стигмата»", "ур. 10–15 • босс: Карнифекс-авангард", (250, 190, 70)),
    ("art_loc_forge.png", "Руины Кузницы «Омега-Два»", "ур. 15–20 • босс: Технодемон Кузницы", (250, 140, 50)),
    ("art_loc_betray.png", "Бастион Предателей", "ур. 20–25 • босс: Магистр культа", (240, 90, 60)),
    ("art_loc_eye.png", "Око Ужаса", "ур. 25–30 • рейд-боссы эндгейма", (190, 90, 230)),
], "art_strip_locations.png", "ЛОКАЦИИ БАЗЫ • МАРШРУТ ПРОГРЕССИИ")
