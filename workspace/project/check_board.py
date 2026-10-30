#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регресс-харнес модуля «Рейтинг активности» (партия №43, r25).

Запуск из корня проекта: python3 check_board.py
Данные проекта не трогает: работает на изолированных gid'ах теста и чистит их за собой.
"""
import asyncio
import importlib.util
import sys
from datetime import timedelta

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

class R:  # фейковая роль
    def __init__(self, rid): self.id = rid

class M:  # фейковый участник
    def __init__(self, uid, roles=(), name=None):
        self.id = uid
        self.roles = [R(r) for r in roles]
        self.display_name = name or f"user{uid}"

class G:  # фейковая гильдия
    def __init__(self, gid, members):
        self.id = gid
        self._m = {m.id: m for m in members}
        self.name = f"guild{gid}"
    def get_member(self, uid):
        return self._m.get(uid)

    async def fetch_member(self, uid):
        if uid in self._m:
            return self._m[uid]
        raise bot.discord.NotFound(type("R", (), {"status": 404, "reason": "nf"})(), "nf")

CFG = dict(bot.DEFAULT_CONFIG)
CFG.update({"board_enabled": True, "board_min_messages": 2,
            "board_rewards_daily": [300, 200, 100], "board_rewards_weekly": [800, 500],
            "board_channel": None, "board_utc_offset": 3})

GID = 900000000000000001
guild = G(GID, [M(11), M(22, roles=[777]), M(33)])

# --- 1. циклы и границы ----------------------------------------------------- #
day, week = bot.board_cycle_ids(CFG)
pday, pweek = bot.board_prev_ids(CFG)
check("id дня в формате ГГГГ-ММ-ДД", len(day) == 10 and day[4] == "-" and day[7] == "-")
check("id недели в формате ГГГГ-Www", week[4] == "-" and week[5] == "W" and len(week) == 8)
check("предыдущий день != текущий", pday != day)
nd, nw = bot.board_next_reset(CFG, "d"), bot.board_next_reset(CFG, "w")
import time as _t
check("перекат дня в будущем", nd > _t.time())
check("перекат недели не раньше переката дня", nw >= nd)
dt = bot.board_now(CFG)
check("перекат недели — понедельник", (dt.replace(hour=0, minute=0) + timedelta(days=8 - dt.isocalendar().weekday)).isocalendar().weekday == 1)

# --- 2. грамматика наград (общая для команд и сайта) ------------------------ #
check("награды «300,200,100»", bot.board_parse_rewards("300,200,100") == ([300, 200, 100], None))
check("награды через точку с запятой", bot.board_parse_rewards("10; 20") == ([10, 20], None))
check("пустая строка = без наград", bot.board_parse_rewards("") == ([], None))
check("отрицательная награда отвергнута", bot.board_parse_rewards("-5")[0] is None)
check("буквы отвергнуты", bot.board_parse_rewards("сто")[0] is None)
check("26 мест отвергнуто", bot.board_parse_rewards(",".join(["1"] * 26))[0] is None)

# --- 3. счётчики и таблица -------------------------------------------------- #
bot.board_bump(GID, 11, 10, CFG)
bot.board_bump(GID, 11, 10, CFG)
bot.board_bump(GID, 33, 25, CFG)          # 1 сообщение — ниже порога
bot.board_bump(GID, 22, 99, CFG)          # роль 777 в исключениях
CFG["board_exclude_roles"] = [777]
day, week = bot.board_cycle_ids(CFG)
rows = asyncio.run(bot.board_top(guild, CFG, "d", day))
check("порог сообщений отсекает", all(uid != 33 for uid, _x, _m in rows))
check("исключённая роль вне таблицы", all(uid != 22 for uid, _x, _m in rows))
check("лидер по xp первый", rows and rows[0][0] == 11 and rows[0][1] == 20)
check("ушедший участник вне таблицы", not any(uid == 44 for uid, _x, _m in asyncio.run(bot.board_top(G(GID, [M(11)]), CFG, "d", day))))

# --- 4. финализация цикла: награды, архив, очистка -------------------------- #
before = bot.prog_entry(GID, 11)["credits"]
async def run_finalize():
    await bot.board_finalize(guild, CFG, "d", day)
asyncio.run(run_finalize())
check("награда за 1 место начислена", abs(bot.prog_entry(GID, 11)["credits"] - before - 300) < 1e-9)
st = bot.board_state(GID)
check("итог попал в архив", st["hist"]["d"] and st["hist"]["d"][-1]["id"] == day)
check("ведро цикла очищено", day not in st["c"]["d"])
check("награда записана в архив", st["hist"]["d"][-1]["paid"] and st["hist"]["d"][-1]["paid"][0][1] == 300)

# --- 5. перекат через board_tick: однократно и идемпотентно ----------------- #
st["done"]["d"] = pday
st["c"]["d"][pday] = {"11": [50, 5]}
cred_before = bot.prog_entry(GID, 11)["credits"]
async def run_tick():
    await bot.board_tick(guild, CFG)
    await bot.board_tick(guild, CFG)   # второй тик того же цикла — без последствий
asyncio.run(run_tick())
check("перекат закрыл прошлый цикл", st["done"]["d"] == day)
check("награда начислена ровно один раз", abs(bot.prog_entry(GID, 11)["credits"] - cred_before - 300) < 1e-9)
check("неделя не тронута дневным перекатом", st["done"].get("w") in (week, None))

# --- 6. выключенный рейтинг не копит счётчики ------------------------------- #
CFG2 = dict(CFG, board_enabled=False)
n_before = len(bot.board_state(GID)["c"]["w"].get(week, {}))
bot.board_bump(GID, 11, 10, CFG2)
check("board_bump молчит при выключенном рейтинге", len(bot.board_state(GID)["c"]["w"].get(week, {})) == n_before)

# --- 7. дерево команд и дашборд --------------------------------------------- #
import discord as _discord
for n in [n for n in dir(bot) if n.startswith("g_")]:
    g = getattr(bot, n)
    if bot.bot.tree.get_command(g.name) is None:
        bot.bot.tree.add_command(g)

def count(node):
    kids = node.get_commands() if isinstance(node, _discord.app_commands.CommandTree) else node.commands
    return sum(count(c) if isinstance(c, _discord.app_commands.Group) else 1 for c in kids)

total = count(bot.bot.tree)
check(f"дерево команд 114 (факт {total})", total == 114)
subs = sorted(c.name for c in bot.g_board.commands)
check("у /рейтинг 11 подкоманд", len(subs) == 11 and {"день", "неделя", "архив", "награды", "исключения", "пояс"} <= set(subs))
app = bot.build_dashboard_app()
paths = {r.resource.canonical for r in app.router.routes()}
check("маршрут /guild/{gid}/board есть", "/guild/{gid}/board" in paths)
check("вкладка board в TAB_META и навигации", "board" in bot.TAB_META and 'data-tab=\'board\'' in bot.guild_tabs(GID, ""))

# --- уборка тестовых данных ------------------------------------------------- #
bot.BOARD.pop(str(GID), None)
bot.PROGRESS.pop(str(GID), None)
bot.save_board()
bot.save_progress()

# --- 8. r39: объёмная прогрессия и эксклюзивные ранг-роли --------------------- #
class Msg39:
    def __init__(self, guild, author, text):
        self.guild = guild
        self.author = author
        self.channel = type("C", (), {"id": 101, "parent_id": None})()
        self.content = text
        self.webhook_id = None

cfg39 = dict(bot.DEFAULT_CONFIG)
cfg39.update({"xp_cooldown": 3600, "xp_volume": True, "xp_per_message": 10,
              "credits_per_message": 5, "xp_char_cap": 2000})
g39 = G(39, [M(7)])
m39 = g39.get_member(7)
e0 = bot.prog_entry(39, 7)
e0["xp"] = 0; e0["credits"] = 0.0; e0["last"] = 0
asyncio.run(bot.grant_activity(Msg39(g39, m39, "к" * 2000), cfg39))
long_xp = bot.prog_entry(39, 7)["xp"]
long_cr = bot.prog_entry(39, 7)["credits"]
asyncio.run(bot.grant_activity(Msg39(g39, m39, "к" * 20), cfg39))
short_dx = bot.prog_entry(39, 7)["xp"] - long_xp
check("объём: сообщение на лимите даёт полную ставку", long_xp == 10)
check("объём: короткое сообщение даёт минимум", short_dx == 1)
check("объём: кулдаун не блокирует второе сообщение", short_dx > 0)
check("объём: валюта тоже масштабируется", long_cr > bot.prog_entry(39, 7)["credits"] - long_cr)
cfg39b = dict(cfg39, xp_volume=False)
before = bot.prog_entry(39, 7)["xp"]
asyncio.run(bot.grant_activity(Msg39(g39, m39, "к" * 1900), cfg39b))
check("фикс-режим: кулдаун снова действует", bot.prog_entry(39, 7)["xp"] == before)

class Role39:
    def __init__(self, rid, name):
        self.id, self.name = rid, name
class Member39:
    def __init__(self, roles):
        self.id = 7
        self.roles = list(roles)
        self.log = []
    async def add_roles(self, role, reason=None):
        self.roles.append(role); self.log.append(("add", role.id))
    async def remove_roles(self, role, reason=None):
        self.roles = [r for r in self.roles if r.id != role.id]; self.log.append(("rem", role.id))
class Guild39:
    id = 39
    def __init__(self, member, roles):
        self._m = member; self._r = {r.id: r for r in roles}
    def get_member(self, uid):
        return self._m
    def get_role(self, rid):
        return self._r.get(rid)
r5, r10 = Role39(500, "Адепт"), Role39(600, "Магос")
mem39 = Member39([r5])
guild39 = Guild39(mem39, [r5, r10])
cfg39c = dict(bot.DEFAULT_CONFIG, level_rewards=[
    {"level": 5, "kind": "role", "role_id": 500},
    {"level": 10, "kind": "role", "role_id": 600}])
descs = asyncio.run(bot.apply_level_rewards(guild39, mem39, cfg39c, [10]))
check("ранг-роль 10 выдана", ("add", 600) in mem39.log)
check("прежняя ранг-роль 5 снята (эксклюзив)", ("rem", 500) in mem39.log and r5 not in mem39.roles)
check("снятие упомянуто в описании наград", any("снято" in d for d in descs))
cfg39d = dict(cfg39c, level_roles_exclusive=False)
mem39b = Member39([r5])
guild39b = Guild39(mem39b, [r5, r10])
asyncio.run(bot.apply_level_rewards(guild39b, mem39b, cfg39d, [10]))
check("exclusive=False: прежняя роль остаётся", ("rem", 500) not in mem39b.log)

# --- 9. r40: настоящие слова против сообщений -------------------------------- #
e40 = bot.prog_entry(39, 7)
e40["words"] = 0; e40["messages"] = 0; e40["xp"] = 0; e40["last"] = 0
cfg40 = dict(bot.DEFAULT_CONFIG)
cfg40.update({"xp_volume": True, "xp_char_cap": 2000, "xp_cooldown": 0})
asyncio.run(bot.grant_activity(Msg39(g39, g39.get_member(7), "слово " * 12), cfg40))
e40b = bot.prog_entry(39, 7)
check("слова считаются по тексту (12 слов)", e40b["words"] == 12)
check("сообщения считаются отдельно (1)", e40b["messages"] == 1)
legacy = {"xp": 5, "level": 2, "credits": 1.0, "messages": 3, "last": 0}
bot.PROGRESS["39"]["40"] = legacy
check("старая запись без words получает ноль", bot.prog_entry(39, 40).get("words") == 0)
# --- 10. r41: шаговые кривые и перемножение множителей ------------------------ #
cfg41 = dict(bot.DEFAULT_CONFIG)
cfg41["multipliers"] = bot.default_multipliers(0.5)
check("need_xp: старт 100, шаг 10", [bot.need_xp(cfg41, l) for l in (1, 2, 3)] == [100, 110, 120])
check("модификаторы: старт 1, шаг 0.5", bot.default_multipliers(0.5)[:3] == [1.0, 1.5, 2.0])
cfg41["role_multipliers"] = {"777": {"mult": 3.0, "apply": "both"},
                             "888": {"mult": 2.0, "apply": "both"}}
g41 = G(41, [M(5, roles=[777, 888])])
e41 = bot.prog_entry(41, 5)
e41.update(xp=0, credits=0.0, messages=0, words=0, last=0, level=3)
class Msg41:
    guild = g41
    author = g41.get_member(5)
    channel = type("C", (), {"id": 1, "parent_id": None})()
    content = "к" * 2000
    webhook_id = None
asyncio.run(bot.grant_activity(Msg41(), cfg41))
check("множители перемножены: xp = 10×2(уровень)×3(роль)", e41["xp"] == 60)
check("множители перемножены: валюта = 5×2×3", abs(e41["credits"] - 30.0) < 1e-9)
check("из ролей берётся наибольшая", bot.role_mult_for(g41.get_member(5), cfg41, "xp") == 3.0)
bot.CONFIGS["41"] = {"enabled": False, "xp_base": 100, "xp_growth": 1.5,
                     "multipliers": [1.0] * 100, "channels": []}
cfg_m = bot.get_config(41)
check("миграция: xp_growth ушёл, шаги и новая таблица на месте",
      "xp_growth" not in cfg_m and cfg_m["xp_step"] == 10 and cfg_m["mult_step"] == 0.5
      and cfg_m["multipliers"][1] == 1.5)
cfg42 = dict(bot.DEFAULT_CONFIG)
cfg42.update({"xp_mode": "mul", "xp_mul": 1.1})
check("need_xp в режиме множителя: 100, 110, 121", [bot.need_xp(cfg42, l) for l in (1, 2, 3)] == [100, 110, 121])
check("модификаторы в режиме множителя: 1, 1.1, 1.21", bot.default_multipliers(0.5, "mul", 1.1)[:3] == [1.0, 1.1, 1.21])
cfg42b = dict(bot.DEFAULT_CONFIG, mult_mode="mul", mult_mul=1.2)
check("mult_table учитывает режим", bot.mult_table(cfg42b)[2] == 1.44)
bot.CONFIGS.pop("41", None)
bot.PROGRESS.pop("41", None)
bot.save_configs(); bot.save_progress()

bot.PROGRESS.pop("39", None)
bot.save_progress()

bot.PROGRESS.pop("39", None)
bot.save_progress()

print(f"\nИТОГ: {OK} OK, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
