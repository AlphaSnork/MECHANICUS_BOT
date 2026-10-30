#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регресс-харнес модуля «Новости» (партия №44, r27).

Запуск из корня проекта: python3 check_news.py
"""
import importlib.util
import sys
import time

spec = importlib.util.spec_from_file_location("bot", "bot.py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)

OK = FAIL = 0

def check(name, cond):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")

# --- 1. единая валидация (команды и сайт) ------------------------------------ #
check("годный черновик", bot.news_validate("Заголовок", "Текст новости") == "")
check("пустой заголовок отвергнут", "заголовок" in bot.news_validate("", "текст"))
check("длинный заголовок отвергнут", "256" in bot.news_validate("x" * 257, "текст"))
check("пустой текст отвергнут", "текст" in bot.news_validate("Заголовок", " "))
check("длинный текст отвергнут", "4000" in bot.news_validate("Заголовок", "x" * 4001))
check("битая ссылка на картинку отвергнута", "http" in bot.news_validate("Заголовок", "текст", "ftp://x.png"))
check("https-картинка принята", bot.news_validate("Заголовок", "текст", "https://x/y.png") == "")

# --- 2. разбор времени отложки ------------------------------------------------ #
now = time.time()
ts, err = bot.news_parse_when("45", 3)
check("«45» = +45 минут", err is None and abs(ts - (now + 2700)) < 5)
import datetime as _dt
_future = (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(days=2)).strftime("%Y-%m-%d") + " 20:00"
ts2, err2 = bot.news_parse_when(_future, 3)
check("дата в поясе сервера разобрана", err2 is None and ts2 > now)
ts3, err3 = bot.news_parse_when(_future.replace(" ", "T"), 3)
check("ISO-формат с T разобран", err3 is None and ts3 == ts2)
check("пустая строка = сразу", bot.news_parse_when("", 3) == (None, None))
check("мусор отвергнут с подсказкой", bot.news_parse_when("завтра", 3)[0] is None
      and "минуты" in bot.news_parse_when("завтра", 3)[1])
check("разные пояса дают разный ts",
      bot.news_parse_when("2026-10-24 20:00", 0)[0] != ts2)

# --- 3. очередь отложенных ----------------------------------------------------- #
g = {"published": [
    {"id": 1, "status": "scheduled", "publish_at": now - 10},
    {"id": 2, "status": "scheduled", "publish_at": now + 3600},
    {"id": 3, "status": "sent", "publish_at": 0},
]}
due = [e["id"] for e in bot.news_due(g, now)]
check("в очередь попал только просроченный", due == [1])

# --- 4. embed ------------------------------------------------------------------- #
emb = bot.news_build_embed({"title": "Т", "text": "текст", "image": "https://x/y.png"})
check("embed с заголовком и картинкой", emb.title == "📰 Т" and emb.image.url == "https://x/y.png")
emb2 = bot.news_build_embed({"title": "Т", "text": "текст"})
check("embed без картинки не падает", "image" not in emb2.to_dict())

# --- 5. данные и дерево команд --------------------------------------------------- #
GID = 920000000000000001
gd = bot.news_guild(GID)
check("структура данных сервера создана", set(gd) >= {"cfg", "drafts", "published", "seq"})
import discord as _discord
for n in [n for n in dir(bot) if n.startswith("g_")]:
    grp = getattr(bot, n)
    if bot.bot.tree.get_command(grp.name) is None:
        bot.bot.tree.add_command(grp)
def count(node):
    kids = node.get_commands() if isinstance(node, _discord.app_commands.CommandTree) else node.commands
    return sum(count(c) if isinstance(c, _discord.app_commands.Group) else 1 for c in kids)
total = count(bot.bot.tree)
check(f"дерево команд 114 (факт {total})", total == 114)
check("у /новости 10 подкоманд", len(bot.g_news.commands) == 10)
app = bot.build_dashboard_app()
paths = {r.resource.canonical for r in app.router.routes()}
check("маршруты досье и новостей есть", {"/guild/{gid}/dossier", "/guild/{gid}/news"} <= paths)
check("вкладки в навигации", all(t in bot.guild_tabs(GID, "") for t in ("dossier", "news")))

# --- уборка ------------------------------------------------------------------------ #
bot.NEWS.pop(str(GID), None)
bot.save_news()
print(f"\nИТОГ: {OK} OK, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
