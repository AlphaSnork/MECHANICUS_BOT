#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Adeptus Mechanicus — Discord-бот Кузни Бога-Машины для ролевых серверов Warhammer 40,000.

Возможности:
  * Цитаты (Ересь Хоруса и Империум 40k): автопубликация в активных каналах, предложения
    игроков с модерацией.
  * Уровни 1–100 за активность: опыт, валюта «дата-коги», множители ролей и уровней,
    награды за уровни, топ-лист.
  * Магазин, лутбоксы, розыгрыши, инвентарь с кнопками.
  * Приветствие новичков, расклад Аугура, броски костей (d3–d100, несколько кубиков).
  * Роли за реакции: панели с эмодзи (как Carl-bot) — поставил реакцию, получил роль.
  * Тикеты (как Ticket Tool): панель с кнопкой, приватные каналы, закрытие с транскриптом.
  * Аудит сервера (как Juniper): журнал событий и отчёт безопасности с оценкой 0–100.
  * Перенос ролевых сообщений между серверами и веб-дашборд (Discord OAuth2).

Запуск:  python3 bot.py   (токен: переменная DISCORD_TOKEN или файл token.txt)
Самотест: python3 bot.py --selftest
"""

import json
import logging
import re
import os
import random
import sys
import time
from collections import deque
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks

import aiohttp
from aiohttp import web
import asyncio
import base64
import contextvars
import io
import hashlib
import hmac
import secrets as _secrets
import urllib.parse
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------- #
# Пути и константы
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "configs.json")
CUSTOM_PATH = os.path.join(DATA_DIR, "custom_quotes.json")
STATE_PATH = os.path.join(DATA_DIR, "state.json")
CANON_PATH = os.path.join(DATA_DIR, "quotes_canon.json")
PROGRESS_PATH = os.path.join(DATA_DIR, "progress.json")
SHOP_PATH = os.path.join(DATA_DIR, "shop.json")
LOOT_PATH = os.path.join(DATA_DIR, "loot.json")
INVENTORY_PATH = os.path.join(DATA_DIR, "inventory.json")
GIVEAWAY_PATH = os.path.join(DATA_DIR, "giveaways.json")
AUDIT_PATH = os.path.join(DATA_DIR, "audit.json")
BOARD_PATH = os.path.join(DATA_DIR, "leaderboard.json")
DOSSIER_PATH = os.path.join(DATA_DIR, "dossier.json")
DOSS_SESS_PATH = os.path.join(DATA_DIR, "dossier_sessions.json")
NEWS_PATH = os.path.join(DATA_DIR, "news.json")
AVATAR_PATH = os.path.join(BASE_DIR, "avatar.png")
TOKEN_FILE = os.path.join(BASE_DIR, "token.txt")

BOT_DISPLAY_NAME = "𝙰𝚍𝚎𝚙𝚝𝚞𝚜 𝙼𝚎𝚌𝚑𝚊𝚗𝚒𝚌𝚞𝚜"
EMBED_COLOR = discord.Color.from_rgb(163, 22, 46)  # красный бархат
GOLD_COLOR = discord.Color.from_rgb(200, 162, 74)  # латунь
FOOTER_TEXT = "Adeptus Mechanicus • Кузница Бога-Машины"
BOT_BUILD = "2026-10-23.r44-mechanicus-dossrules"  # маркер сборки: консоль, selftest, футер дашборда

MAX_LEVEL = 100
CREDITS_NAME = "дата-коги"

ERA_LABELS = {
    "heresy": "Ересь Хоруса",
    "imperium": "Империум (40k)",
    "both": "Обе эпохи",
}
ERA_FILTER_LABELS = {
    "any": "Все эпохи",
    "heresy": ERA_LABELS["heresy"],
    "imperium": ERA_LABELS["imperium"],
}
MODE_LABELS = {
    "moderate": "Модерация (проверка админом)",
    "auto": "Авто-одобрение",
    "off": "Цитаты игроков отключены",
}

DEFAULT_WELCOME_TEXT = (
    "Кузница приветствует тебя, {mention}: имя твоё вписано в реестр {server}. "
    "За каждое сообщение в каналах познания — тем больше, чем полнее его объём, — "
    "ты будешь получать ранги и дата-коги Бога-Машины. "
    "Пусть шестерни твои вращаются ровно, а Омниссия хранит твой путь."
)


DEFAULT_TICKET_TEXT = (
    "Опишите вашу проблему или вопрос — магосы кузни ответят в этом канале. "
    "Кнопка «Закрыть тикет» завершит обращение."
)


def default_multipliers(step: float = 0.5, mode: str = "add", mul: float = 1.1):
    """r42: таблица модификаторов на 100 рангов.

    add: 1, 1+step, 1+2·step, … (шаг 0.5 → 1, 1.5, 2…)
    mul: 1, mul, mul², … (множитель 1.1 → 1, 1.1, 1.21…)
    """
    if mode == "mul":
        return [round(float(mul) ** i, 2) for i in range(MAX_LEVEL)]
    return [round(1.0 + float(step) * i, 2) for i in range(MAX_LEVEL)]

def mult_table(cfg: dict) -> list:
    """Пересборка таблицы модификаторов из текущих настроек кривой."""
    return default_multipliers(cfg.get("mult_step", 0.5), cfg.get("mult_mode", "add"),
                               cfg.get("mult_mul", 1.1))

def curve_desc(cfg: dict) -> str:
    """Человеческое описание обеих кривых для ответов и страниц."""
    if cfg.get("xp_mode", "add") == "mul":
        xp_txt = f"опыт: {cfg['xp_base']} × {float(cfg.get('xp_mul', 1.1)):g}^(ранг-1)"
    else:
        xp_txt = f"опыт: {cfg['xp_base']} + {cfg.get('xp_step', 10)} за ранг"
    if cfg.get("mult_mode", "add") == "mul":
        m_txt = f"модификатор: {float(cfg.get('mult_mul', 1.1)):g}^(ранг-1)"
    else:
        m_txt = f"модификатор: 1 + {cfg.get('mult_step', 0.5):g} за ранг"
    return xp_txt + "; " + m_txt


DEFAULT_CONFIG = {
    # литании
    "enabled": False,
    # кто может настраивать бота на этом сервере (список user id);
    # пока пуст — первичная настройка доступна владельцу сервера и «Управление сервером»
    "admin_ids": [],
    "channels": [],
    "interval_minutes": 45,
    "window_minutes": 10,
    "min_messages": 6,
    "min_unique_users": 2,
    "era": "any",
    "quotes_mode": "moderate",
    # прогрессия
    "xp_channels": [],
    "xp_per_message": 10,
    "credits_per_message": 5,
    "xp_cooldown": 60,
    "xp_base": 100,
    "xp_step": 10,
    "xp_mode": "add",
    "xp_mul": 1.1,
    "mult_step": 0.5,
    "mult_mode": "add",
    "mult_mul": 1.1,
    "role_multipliers": {},
    "multipliers": default_multipliers(0.5),
    "announce_levelup": True,
    # r39: объёмная прогрессия без кулдауна и эксклюзивные роли рангов
    "xp_volume": True,
    "xp_char_cap": 2000,
    "level_roles_exclusive": True,
    "level_rewards": [],
    # посвящения
    "welcome_enabled": False,
    "welcome_channel": None,
    "welcome_text": DEFAULT_WELCOME_TEXT,
    # лавка
    "shop_notify_channel": None,
    "shop_notify_role": None,
    # тикеты
    "ticket_category": None,
    "ticket_role": None,
    "ticket_log": None,
    "ticket_text": DEFAULT_TICKET_TEXT,
    # аудит сервера: журнал событий
    "audit_channel": None,
    "audit_events": {"members": True, "messages": True, "server": True},
    # рейтинг активности: циклы день/неделя с наградой в дата-когах
    "board_enabled": False,
    "board_daily": True,
    "board_weekly": True,
    "board_channel": None,
    "board_utc_offset": 3,
    "board_min_messages": 5,
    "board_show": 10,
    "board_rewards_daily": [300, 200, 100],
    "board_rewards_weekly": [800, 500, 300, 200, 100],
    "board_exclude_roles": [],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("lex-veritas")

# --------------------------------------------------------------------------- #
# Хранилище (JSON-файлы в data/)
# --------------------------------------------------------------------------- #
def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.error("Не удалось прочитать %s: %s", path, exc)
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


os.makedirs(DATA_DIR, exist_ok=True)
CANON = _load(CANON_PATH, [])
CONFIGS = _load(CONFIG_PATH, {})
CUSTOM = _load(CUSTOM_PATH, {})
STATE = _load(STATE_PATH, {"last_post": {}, "identity_set": False})
PROGRESS = _load(PROGRESS_PATH, {})
SHOP = _load(SHOP_PATH, {})
LOOT = _load(LOOT_PATH, {})
INVENTORY = _load(INVENTORY_PATH, {})
GIVEAWAYS = _load(GIVEAWAY_PATH, {})
AUDIT = _load(AUDIT_PATH, [])
BOARD = _load(BOARD_PATH, {})
DOSSIER = _load(DOSSIER_PATH, {})
DOSS_SESS = _load(DOSS_SESS_PATH, {})
NEWS = _load(NEWS_PATH, {})
STATE.setdefault("last_post", {})
STATE.setdefault("identity_set", False)

ACTIVITY: dict[int, deque] = {}
RECENT: dict[int, deque] = {}
PROGRESS_DIRTY = False


def save_configs():
    _save(CONFIG_PATH, CONFIGS)


def save_custom():
    _save(CUSTOM_PATH, CUSTOM)

def save_canon():
    _save(CANON_PATH, CANON)

def paginate(items: list, page: int, per_page: int):
    """(кусок страницы, всего страниц, номер страницы) с клампингом."""
    pages = max(1, -(-len(items) // per_page))
    page = min(max(page, 0), pages - 1)
    return items[page * per_page:(page + 1) * per_page], pages, page

def filter_quotes(entries: list, query: str) -> list:
    q = (query or "").strip().lower()
    if not q:
        return list(entries)
    return [e for e in entries if q in (e.get("text") or "").lower()
            or q in (e.get("author") or "").lower() or q in (e.get("source") or "").lower()]


def save_state():
    _save(STATE_PATH, STATE)


def save_progress():
    global PROGRESS_DIRTY
    _save(PROGRESS_PATH, PROGRESS)
    PROGRESS_DIRTY = False


def save_shop():
    _save(SHOP_PATH, SHOP)

def save_loot():
    _save(LOOT_PATH, LOOT)

def save_inventory():
    _save(INVENTORY_PATH, INVENTORY)

def save_giveaways():
    _save(GIVEAWAY_PATH, GIVEAWAYS)

def save_audit():
    _save(AUDIT_PATH, AUDIT)

def save_board():
    _save(BOARD_PATH, BOARD)

def save_dossier():
    _save(DOSSIER_PATH, DOSSIER)

def save_doss_sess():
    _save(DOSS_SESS_PATH, DOSS_SESS)

def save_news():
    _save(NEWS_PATH, NEWS)

def audit(guild_id: int, actor_id: int, action: str, detail: str = ""):
    """Журнал действий (dash + слэш-команды): кольцо на 200 записей."""
    AUDIT.append({"ts": int(time.time()), "guild": str(guild_id), "actor": str(actor_id),
                  "action": action, "detail": detail or ""})
    if len(AUDIT) > 200:
        del AUDIT[:-200]
    save_audit()


def get_config(guild_id: int) -> dict:
    gid = str(guild_id)
    cfg = CONFIGS.setdefault(gid, dict(DEFAULT_CONFIG))
    changed = False
    for key, val in DEFAULT_CONFIG.items():
        if key not in cfg:
            if isinstance(val, list):
                cfg[key] = list(val)
            elif isinstance(val, dict):
                cfg[key] = dict(val)
            else:
                cfg[key] = val
            changed = True
    # r41: миграция степенной кривой на шаговую + пересборка таблицы модификаторов
    if "mult_step" not in cfg or "xp_step" not in cfg or "xp_growth" in cfg:
        cfg.setdefault("xp_step", 10)
        cfg.setdefault("mult_step", 0.5)
        cfg.pop("xp_growth", None)
        cfg["multipliers"] = mult_table(cfg)
        changed = True
    if len(cfg.get("multipliers", [])) != MAX_LEVEL:
        cfg["multipliers"] = mult_table(cfg)
        changed = True
    if changed:
        save_configs()
    return cfg


# --------------------------------------------------------------------------- #
# Прогрессия: ранги и дата-коги
# --------------------------------------------------------------------------- #
def prog_entry(guild_id: int, user_id: int) -> dict:
    g = PROGRESS.setdefault(str(guild_id), {})
    entry = g.setdefault(str(user_id), {"xp": 0, "level": 1, "credits": 0.0, "messages": 0, "words": 0, "last": 0})
    entry.setdefault("xp", 0)
    entry.setdefault("level", 1)
    entry.setdefault("credits", 0.0)
    entry.setdefault("messages", 0)
    entry.setdefault("words", 0)  # r40: настоящие слова, а не сообщения
    entry.setdefault("last", 0)
    return entry


def need_xp(cfg: dict, level: int) -> int:
    """r42: XP до следующего ранга.

    add: старт + шаг × (ранг-1) → 100, 110, 120…
    mul: старт × множитель^(ранг-1) → 100, 110, 121… (шаг-множитель, напр. 1.1)
    """
    if cfg.get("xp_mode", "add") == "mul":
        return max(1, int(round(cfg["xp_base"] * float(cfg.get("xp_mul", 1.1)) ** (level - 1))))
    return max(1, int(cfg["xp_base"] + float(cfg.get("xp_step", 10)) * (level - 1)))


def mult_for(cfg: dict, level: int) -> float:
    table = cfg["multipliers"]
    idx = min(max(level, 1), MAX_LEVEL) - 1
    return float(table[idx])


def role_mult_for(member, cfg: dict, kind: str) -> float:
    """Дополнительный коэффициент роли для 'xp' или 'credits'.

    Если у участника несколько ролей с коэффициентами — действует наибольший.
    """
    roles = getattr(member, "roles", None)
    if not roles or not cfg.get("role_multipliers"):
        return 1.0
    best = 1.0
    for role in roles:
        entry = cfg["role_multipliers"].get(str(role.id))
        if not entry:
            continue
        apply_to = entry.get("apply", "both")
        if apply_to in ("both", kind):
            best = max(best, float(entry.get("mult", 1.0)))
    return best


def fmt_credits(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def progress_bar(xp: int, need: int, width: int = 10) -> str:
    filled = 0 if need <= 0 else min(width, int(xp * width / need))
    return "▰" * filled + "▱" * (width - filled)


def guild_rank(guild_id: int, user_id: int) -> int:
    entries = PROGRESS.get(str(guild_id), {})
    order = sorted(entries.items(), key=lambda kv: (kv[1]["level"], kv[1]["xp"]), reverse=True)
    for i, (uid, _) in enumerate(order, 1):
        if uid == str(user_id):
            return i
    return len(order)

# --------------------------------------------------------------------------- #
# Кладезь, тех-арки кузни, жребии
# --------------------------------------------------------------------------- #
SOURCE_LABELS = {
    "shop": "лавка даров",
    "loot": "тех-арк",
    "giveaway": "жребий кузни",
    "admin": "дар магосов",
    "reward": "дар за ранг",
}

def inv_entries(guild_id: int, user_id: int) -> list:
    g = INVENTORY.setdefault(str(guild_id), {})
    return g.setdefault(str(user_id), [])

def inv_add(guild_id: int, user_id: int, name: str, description: str, source: str) -> int:
    entries = inv_entries(guild_id, user_id)
    iid = max([it.get("iid", 0) for it in entries], default=0) + 1
    entries.append({
        "iid": iid,
        "name": name,
        "description": description or "",
        "source": source,
        "ts": int(time.time()),
    })
    save_inventory()
    return iid

def inv_use(guild_id: int, user_id: int, iid: int):
    """Израсходовать предмет из кладезя. Возвращает предмет или None."""
    entries = inv_entries(guild_id, user_id)
    for it in list(entries):
        if it.get("iid") == iid:
            entries.remove(it)
            save_inventory()
            return it
    return None

def loot_boxes(guild_id: int) -> list:
    return LOOT.setdefault(str(guild_id), [])

def roll_loot_item(box: dict):
    """Жребий предмета ящика по шансам (веса нормируются)."""
    items = box.get("items", [])
    if not items:
        return None
    weights = [max(0.0, float(it.get("chance", 0))) for it in items]
    total = sum(weights)
    if total <= 0:
        return random.choice(items)
    r = random.uniform(0, total)
    acc = 0.0
    for it, w in zip(items, weights):
        acc += w
        if r <= acc:
            return it
    return items[-1]

def chance_percent(box: dict, item: dict) -> float:
    total = sum(max(0.0, float(it.get("chance", 0))) for it in box.get("items", []))
    if total <= 0:
        return 0.0
    return max(0.0, float(item.get("chance", 0))) * 100 / total

def apply_xp_delta(cfg: dict, entry: dict, delta: int) -> list:
    """Изменить XP (пол не ниже 0) с прокачкой вверх; возврат — новые ранги."""
    entry["xp"] = max(0, entry["xp"] + delta)
    new_levels = []
    while entry["level"] < MAX_LEVEL and entry["xp"] >= need_xp(cfg, entry["level"]):
        entry["xp"] -= need_xp(cfg, entry["level"])
        entry["level"] += 1
        new_levels.append(entry["level"])
    return new_levels

def apply_credits_delta(entry: dict, delta: float) -> float:
    entry["credits"] = round(max(0.0, entry["credits"] + delta), 2)
    return entry["credits"]

async def apply_level_rewards(guild: discord.Guild, member, cfg: dict, new_levels: list) -> list:
    """Выдать награды за достигнутые ранги: роль / предмет / дата-коги. Возврат — описания."""
    descs = []
    if not new_levels or not cfg.get("level_rewards"):
        return descs
    granted_role_levels = []
    for lvl in new_levels:
        for r in list(cfg.get("level_rewards", [])):
            if int(r.get("level", 0)) != lvl:
                continue
            kind = r.get("kind")
            if kind == "role":
                m = guild.get_member(getattr(member, "id", 0))
                role = guild.get_role(int(r.get("role_id") or 0))
                if m is None or role is None:
                    continue
                try:
                    await m.add_roles(role, reason=f"Награда за ранг {lvl}")
                    granted_role_levels.append(lvl)
                    descs.append(f"🎖 роль @{role.name}")
                except discord.HTTPException as exc:
                    log.warning("Не удалось выдать роль %s за ранг %s: %s", role.name, lvl, exc)
            elif kind == "item":
                name = r.get("item_name") or "Награда"
                inv_add(guild.id, member.id, name, f"Награда за ранг {lvl}", "reward")
                descs.append(f"🎁 предмет «{name}»")
            elif kind == "credits":
                amt = float(r.get("credits") or 0)
                if amt > 0:
                    e = prog_entry(guild.id, member.id)
                    apply_credits_delta(e, amt)
                    descs.append(f"💰 {fmt_credits(amt)} {CREDITS_NAME}")
    # r39: ранг-роль эксклюзивна — с новым рангом предыдущие ранг-роли снимаются
    if granted_role_levels and cfg.get("level_roles_exclusive", True):
        top = max(granted_role_levels)
        m = guild.get_member(getattr(member, "id", 0))
        removed = 0
        for r in cfg.get("level_rewards", []):
            if r.get("kind") != "role" or int(r.get("level", 0)) >= top:
                continue
            old_role = guild.get_role(int(r.get("role_id") or 0))
            if m is None or old_role is None or old_role not in m.roles:
                continue
            try:
                await m.remove_roles(old_role, reason=f"Ранг {top}: роль прежнего ранга снята")
                removed += 1
            except discord.HTTPException as exc:
                log.warning("Не удалось снять роль %s при ранге %s: %s", old_role.name, top, exc)
        if removed:
            descs.append(f"♻️ ролей прежних рангов снято: {removed}")
    return descs

def draw_giveaway_winners(gw: dict) -> list:
    participants = list(gw.get("participants", []))
    if not participants:
        return []
    n = min(int(gw.get("winners", 1)), len(participants))
    return random.sample(participants, n)

def giveaway_embed(gw: dict) -> discord.Embed:
    status = gw.get("status", "active")
    embed = discord.Embed(title="⚙ Жребий кузни: " + gw["title"], color=GOLD_COLOR)
    if status == "active":
        embed.description = (
            f"Идёт до <t:{int(gw['ends_at'])}:T> (<t:{int(gw['ends_at'])}:R>).\n"
            f"Нажмите кнопку ниже, чтобы участвовать."
        )
    elif status == "finished":
        winners = gw.get("winners_list", [])
        if winners:
            lines = []
            for place, uid in enumerate(winners, 1):
                prize = gw.get("prizes", {}).get(str(place))
                if prize:
                    label = prize.get("name") or f"{fmt_credits(prize.get('credits', 0))} {CREDITS_NAME}"
                    lines.append(f"**{place}.** <@{uid}> — {label}")
                else:
                    lines.append(f"**{place}.** <@{uid}>")
            embed.description = "🏆 Жребий завершён. Победители:\n" + "\n".join(lines)
        else:
            embed.description = "Жребий завершён: участников не было."
    else:
        embed.description = "Жребий отменён."
    prizes = gw.get("prizes", {})
    if prizes:
        def _plabel(pr):
            return pr.get("name") or f"{fmt_credits(pr.get('credits', 0))} {CREDITS_NAME}"
        embed.add_field(
            name="Призы по местам",
            value="\n".join(f"**{p}.** {_plabel(prizes[p])}" for p in sorted(prizes, key=int)),
            inline=False,
        )
    embed.add_field(name="Победителей", value=str(gw.get("winners", 1)), inline=True)
    embed.add_field(name="Участников", value=str(len(gw.get("participants", []))), inline=True)
    embed.set_footer(text=FOOTER_TEXT)
    return embed

async def notify_admin_channel(interaction, embed: discord.Embed, body: str) -> bool:
    """Пинг роли уведомлений лавки в настроенном канале (там же, где уведомления о покупках)."""
    guild = interaction.guild
    cfg = get_config(guild.id)
    channel = guild.get_channel(cfg["shop_notify_channel"]) if cfg["shop_notify_channel"] else None
    role = guild.get_role(cfg["shop_notify_role"]) if cfg["shop_notify_role"] else None
    if channel is None or role is None:
        return False
    toggled = False
    if not role.mentionable:
        try:
            await role.edit(mentionable=True)
            toggled = True
        except discord.HTTPException as exc:
            log.warning(
                "Не удалось сделать роль %s упоминаемой (%s). Пинг может не пройти: "
                "поднимите роль бота выше роли уведомлений или разрешите её упоминание.",
                role.name, exc,
            )
    try:
        await channel.send(
            f"{role.mention} {body}",
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=[role], everyone=False),
        )
    except discord.HTTPException as exc:
        log.warning(
            "Не удалось отправить уведомление совету магосов в #%s: %s. "
            "Проверьте права бота в этом канале и иерархию ролей.",
            channel.name, exc,
        )
        return False
    finally:
        if toggled:
            try:
                await role.edit(mentionable=False)
            except discord.HTTPException:
                pass
    return True

async def perform_purchase(interaction: discord.Interaction, item: dict):
    """Общая логика покупки (команда и кнопка): списание, предмет в кладезь, уведомление."""
    guild = interaction.guild
    cfg = get_config(guild.id)
    entry = prog_entry(guild.id, interaction.user.id)
    if entry["credits"] < item["price"]:
        await interaction.response.send_message(
            f"⛔ Недостаточно {CREDITS_NAME}: нужно {fmt_credits(item['price'])}, "
            f"у вас {fmt_credits(entry['credits'])}. Зарабатывайте активностью в каналах прогрессии."
        )
        return False
    entry["credits"] = round(entry["credits"] - item["price"], 2)
    save_progress()
    inv_add(guild.id, interaction.user.id, item["name"], item.get("description", ""), "shop")
    await interaction.response.send_message(
        "Покупка совершена. Ожидайте, совет магосов скоро выдаст вашу реквизицию."
    )
    purchase_embed = discord.Embed(
        title="⚙ Обет принят",
        description=(
            f"{interaction.user.mention} приобретает: **{item['name']}**\n"
            f"Цена: **{fmt_credits(item['price'])}** {CREDITS_NAME}"
        ),
        color=GOLD_COLOR,
    )
    purchase_embed.set_footer(text=FOOTER_TEXT)
    try:
        public_msg = await interaction.channel.send(embed=purchase_embed)
    except discord.HTTPException:
        public_msg = None
    link = public_msg.jump_url if public_msg else interaction.channel.mention
    sent = await notify_admin_channel(
        interaction, purchase_embed, f"Игрок {interaction.user.mention} совершил покупку: {link}"
    )
    if not sent:
        await interaction.followup.send(
            "⚠️ Канал или роль для уведомлений о покупках не настроены (`/магазин уведомления`). "
            "Уведомление не отправлено."
        )
    return True

async def process_giveaways():
    """Завершение просроченных жребиий (вызывается из ticker)."""
    now = time.time()
    for guild in bot.guilds:
        for gw in guild_giveaways(guild.id):
            if gw.get("status") == "active" and gw.get("ends_at", 0) <= now:
                await finish_giveaway(guild, gw)

async def finish_giveaway(guild: discord.Guild, gw: dict):
    gw["status"] = "finished"
    winners = draw_giveaway_winners(gw)
    gw["winners_list"] = winners
    for place, uid in enumerate(winners, 1):
        prize = gw.get("prizes", {}).get(str(place))
        if not prize:
            continue
        if prize.get("credits"):
            e = prog_entry(guild.id, uid)
            apply_credits_delta(e, float(prize["credits"]))
            save_progress()
        else:
            inv_add(guild.id, uid, prize["name"], prize.get("description", ""), "giveaway")
    save_giveaways()
    channel = guild.get_channel(gw.get("channel_id")) if gw.get("channel_id") else None
    if channel is not None and gw.get("message_id"):
        try:
            msg = await channel.fetch_message(gw["message_id"])
            await msg.edit(embed=giveaway_embed(gw), view=None)
        except discord.HTTPException as exc:
            log.warning("Не удалось обновить сообщение жребия: %s", exc)
    if channel is None:
        log.warning("Жребий %s завершён, но канал не найден", gw.get("id"))
        return
    if winners:
        content = " ".join(f"<@{uid}>" for uid in winners) + f" — победители жребия «{gw['title']}»! Поздравляем."
    else:
        content = f"Жребий «{gw['title']}» завершён: участников не было."
    try:
        await channel.send(content, embed=giveaway_embed(gw))
    except discord.HTTPException as exc:
        log.warning("Не удалось объявить победителей жребия: %s", exc)

def guild_giveaways(guild_id: int) -> list:
    return GIVEAWAYS.setdefault(str(guild_id), [])



# --------------------------------------------------------------------------- #
# Аугур Бога-Машины
# --------------------------------------------------------------------------- #
TAROT_DECK = [
    ("Омниссия", "good", "Бог-Машина внемлет тебе: дело увенчается точностью и успехом."),
    ("Шестерня", "good", "Механизм смазан и гудит ровно: труды окупятся, замыслы свершатся."),
    ("Ржавчина", "mixed", "Металл требует жертв: успех придёт, но не без утрат."),
    ("Страж", "good", "Присяга стража крепка: защита и поддержка обеспечат победу."),
    ("Обет", "good", "Слово, данное Богу-Машине, будет услышано: держись его — и преуспеешь."),
    ("Кузница", "good", "Своды кузни над тобой: надёжный тыл и кров для замыслов."),
    ("Кодекс", "mixed", "Знание откроется не сразу: ищи в архивах — и обрящешь, но не без труда."),
    ("Порча", "bad", "Тень порчи над схемой: искушение близко, не внимай ей."),
    ("Ересь", "bad", "Ересь шепчет в проводах: доверяй лишь проверенным разумам."),
    ("Странник", "mixed", "Долгий путь к архиву: успех придёт, но не скоро."),
    ("Щедрость", "good", "Бог-Машина щедр: помощь придёт оттуда, откуда не ждёшь."),
    ("Гнев", "bad", "Гнев обрушится на головы гордых: не поднимай голоса напрасно."),
    ("Тишина", "mixed", "В тишине слышен гул машины: подожди — и знак придёт."),
    ("Пламя", "bad", "Пламя горна испепелит ветхое: что-то уйдёт безвозвратно, очистив место новому."),
    ("Лавры", "good", "Венец из латуни увенчает труды: признание и награда близки."),
    ("Дорога", "mixed", "Дорога переменчива: держись курса, но будь готов свернуть."),
    ("Масло", "bad", "Масло прольётся в горне: цена успеха высока, взвесь её."),
    ("Свет", "good", "Свет индикатора над схемой: истина откроется, тьма отступит."),
    ("Тень", "bad", "Тень легла на схему: скрытый недоброжелатель помешает планам."),
    ("Чудо", "good", "Чудо Бога-Машины: невозможное станет возможным."),
    ("Суд", "mixed", "Весы суда колеблются: исход решит твоя честность."),
    ("Сплав", "good", "Сплав крепче клятвы: союз разумов принесёт плоды."),
]
OMEN_LABEL = {"good": "✦ благосклонно", "bad": "☠ грозно", "mixed": "◐ туманно"}


def tarot_verdict(good: int, bad: int, mixed: int) -> str:
    if good > bad:
        return (f"Бог-Машина благоволит тебе: благосклонных знаков — {good} против {bad} грозных. "
                f"Намеченное свершится, если сохранишь веру и ясный разум.")
    if bad > good:
        return (f"Бог-Машина молчит: тьма сгущается ({bad} грозных знаков против {good}). "
                f"Отложи рискованное, вознеси литанию Омниссии и не доверяй случайным голосам.")
    return ("Воля Машины колеблется между благосклонностью и испытанием "
            f"({good} благосклонных, {bad} грозных, {mixed} туманных). Исход решит твоя верность.")


# --------------------------------------------------------------------------- #
# Литании: пул и активность
# --------------------------------------------------------------------------- #
def channel_active(channel_id: int, window_minutes: int, min_messages: int, min_users: int) -> bool:
    dq = ACTIVITY.get(channel_id)
    if not dq:
        return False
    cutoff = time.time() - window_minutes * 60
    msgs = [(ts, author) for ts, author in dq if ts >= cutoff]
    if len(msgs) < min_messages:
        return False
    return len({author for _, author in msgs}) >= min_users


def build_pool(guild_id: int, era_pref: str, quotes_mode: str):
    pool = []
    for i, q in enumerate(CANON):
        if era_pref != "any" and q.get("era") not in (era_pref, "both"):
            continue
        pool.append((f"c{i}", q))
    if quotes_mode != "off":
        for q in CUSTOM.get(str(guild_id), []):
            if q.get("status") != "approved":
                continue
            if era_pref != "any" and q.get("era") not in (era_pref, "both"):
                continue
            pool.append((f"g{guild_id}-{q['id']}", q))
    return pool


def pick_quote(guild_id: int, cfg: dict):
    pool = build_pool(guild_id, cfg["era"], cfg["quotes_mode"])
    if not pool:
        return None
    recent = RECENT.setdefault(guild_id, deque(maxlen=12))
    fresh = [(k, q) for k, q in pool if k not in recent]
    if not fresh:
        recent.clear()
        fresh = pool
    key, quote = random.choice(fresh)
    recent.append(key)
    return quote


def quote_embed(q: dict) -> discord.Embed:
    era = ERA_LABELS.get(q.get("era", "both"), ERA_LABELS["both"])
    author = q.get("author") or "Неизвестный автор"
    source = q.get("source") or "Источник не указан"
    embed = discord.Embed(
        description=f">>> {q['text']}\n\n— **{author}**\n*{source} • {era}*",
        color=EMBED_COLOR,
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed


# --------------------------------------------------------------------------- #
# Бот
# --------------------------------------------------------------------------- #
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
# Privileged-интенты: детерминированная схема без автопроб гейтвея.
#   INTENTS_MEMBERS = 1 (по умолчанию) — посвящения новичков; требует Server Members Intent в портале.
intents.members = os.environ.get("INTENTS_MEMBERS", "1") != "0"
# Message Content Intent — opt-in ТОЛЬКО для переноса сообщений (вкладка «Перенос»):
#   INTENTS_CONTENT=1 + включённый intent в портале. По умолчанию выключен (приватность игроков).
intents.message_content = os.environ.get("INTENTS_CONTENT", "0") == "1"

bot = commands.Bot(command_prefix="!lv ", intents=intents, help_command=None)


def is_bot_admin(interaction: discord.Interaction) -> bool:
    """Доступ к командам настройки: только совет магосов.

    Порядок проверки:
      1) владелец приложения-бота (страховка от блокировки);
      2) список cfg['admin_ids'] — если он не пуст, разрешены ТОЛЬКО они;
      3) пока список пуст (первичная настройка): владелец сервера
         или право «Управление сервером».
    """
    if interaction.guild is None:
        return False
    if bot.owner_id and interaction.user.id == bot.owner_id:
        return True
    cfg = get_config(interaction.guild.id)
    ids = cfg.get("admin_ids", [])
    if ids:
        return interaction.user.id in ids
    if interaction.guild.owner_id == interaction.user.id:
        return True
    return bool(interaction.permissions.manage_guild)


@bot.event
async def on_ready():
    log.info("Бот подключён: %s (id=%s), серверов: %d", bot.user, bot.user.id, len(bot.guilds))
    await apply_identity()
    try:
        await bot.tree.sync()
        log.info("Слэш-команды синхронизированы (глобально).")
    except Exception as exc:
        log.error("Ошибка синхронизации команд: %s", exc)
    # Гильдийные команды применяются Discord мгновенно (глобальные кэшируются до часа):
    # синхронизируем дерево в каждую гильдию, чтобы новые имена появились сразу.
    for _g in bot.guilds:
        try:
            await bot.tree.sync(guild=_g)
        except Exception as exc:
            log.warning("Не удалось синхронизировать команды в %s: %s", _g.name, exc)
    log.info("Слэш-команды синхронизированы по гильдиям: %s", len(bot.guilds))
    if not ticker.is_running():
        ticker.start()
    bot.add_view(DossierPanelView())  # кнопка «Создать анкету» переживает Restart
    try:
        await doss_resume_sweep()
    except Exception as exc:
        log.warning("Досье: обход возобновления не удался: %s", exc)
    # Догоняем перекаты рейтинга, пропущенные во время Stop/Restart хостинга.
    for _g in bot.guilds:
        try:
            await board_tick(_g, get_config(_g.id))
        except Exception as exc:
            log.warning("Рейтинг: догон перекатов при старте не удался в %s: %s", _g.name, exc)
    global _DASH_STARTED
    if not _DASH_STARTED:
        _DASH_STARTED = True
        bot.loop.create_task(start_dashboard())
    for _jobs in MIGRATIONS.values():
        for _job in _jobs:
            if _job.get("status") == "running":
                MIG_QUEUES.setdefault(_job["id"], asyncio.Queue())
                if _job["id"] not in MIG_TASKS:
                    MIG_TASKS[_job["id"]] = bot.loop.create_task(mig_worker(_job["id"]))
    for guild in bot.guilds:
        for gw in guild_giveaways(guild.id):
            if gw.get("status") == "active":
                bot.add_view(GiveawayView(guild.id, gw["id"]))
    # Тикеты: персистентные кнопки панелей и открытых тикетов переживают перезапуск
    for guild in bot.guilds:
        tk = TICKETS.get(str(guild.id)) or {}
        if tk.get("panels"):
            bot.add_view(TicketOpenView(guild.id))
        for cid in (tk.get("tickets") or {}):
            bot.add_view(TicketCloseView(guild.id, int(cid)))
    # r23: исцеление «мёртвых» записей эмодзи на панелях ролей — старые версии
    # сайта принимали имя эмодзи словом, и такие записи нельзя было нажать.
    _rr_fixed = 0
    for guild in bot.guilds:
        try:
            _rr_fixed += _rr_heal_guild(guild)
        except Exception as exc:
            log.warning("Реакции-роли: исцеление панелей в %s не удалось: %s", guild.name, exc)
    if _rr_fixed:
        log.info("Реакции-роли: исцелено мёртвых записей эмодзи при старте: %d", _rr_fixed)


async def apply_identity():
    if STATE.get("identity_set"):
        return
    try:
        if bot.user.display_name != BOT_DISPLAY_NAME:
            await bot.user.edit(username=BOT_DISPLAY_NAME)
            log.info("Имя бота установлено: %s", BOT_DISPLAY_NAME)
    except Exception as exc:
        log.warning("Не удалось сменить имя через API (%s). Задайте имя в Developer Portal.", exc)
    try:
        if os.path.exists(AVATAR_PATH):
            with open(AVATAR_PATH, "rb") as f:
                await bot.user.edit(avatar=f.read())
            log.info("Аватар установлен из avatar.png")
    except Exception as exc:
        log.warning("Не удалось установить аватар через API (%s). Загрузите avatar.png в Developer Portal.", exc)
    STATE["identity_set"] = True
    save_state()


@bot.event
async def on_guild_join(guild: discord.Guild):
    get_config(guild.id)
    save_configs()
    try:  # мгновенная регистрация команд на новом сервере
        await bot.tree.sync(guild=guild)
    except Exception as exc:
        log.warning("Не удалось синхронизировать команды в %s: %s", guild.name, exc)
    try:
        await guild.me.edit(nick=BOT_DISPLAY_NAME)
    except Exception:
        pass


@bot.event
async def on_member_join(member: discord.Member):
    cfg = get_config(member.guild.id)
    if not cfg["welcome_enabled"] or not cfg["welcome_channel"]:
        return
    channel = member.guild.get_channel(cfg["welcome_channel"])
    if channel is None:
        return
    text = cfg["welcome_text"].format(
        mention=member.mention,
        user=str(member.display_name),
        server=member.guild.name,
        count=member.guild.member_count,
    )
    try:
        await channel.send(
            text,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except discord.HTTPException as exc:
        log.warning("Не удалось отправить посвящение в #%s: %s", channel.name, exc)


@bot.event
async def on_message(message: discord.Message):
    if message.guild is None or message.author.bot or message.webhook_id is not None:
        return
    dq = ACTIVITY.setdefault(message.channel.id, deque(maxlen=500))
    dq.append((time.time(), message.author.id))
    mig_dispatch(message)

    cfg = get_config(message.guild.id)
    # r28: ветки и посты форумов считаются по родительскому каналу —
    # раньше опыт начислялся только за сообщения в самих каналах списка.
    chan_id = message.channel.id
    parent_id = getattr(message.channel, "parent_id", None)
    if chan_id in cfg["xp_channels"] or (parent_id is not None and parent_id in cfg["xp_channels"]):
        await grant_activity(message, cfg)


def volume_ratio(message: discord.Message, cfg: dict) -> float:
    """r39: доля лимита символов Discord, заполненная сообщением (0..1)."""
    cap = max(100, int(cfg.get("xp_char_cap", 2000)))
    return min(len(message.content or ""), cap) / cap

async def grant_activity(message: discord.Message, cfg: dict):
    """Начисление опыта и дата-когов за сообщение.

    r39: в объёмном режиме (xp_volume, по умолчанию) кулдауна нет — награда
    пропорциональна длине сообщения вплоть до xp_char_cap (лимит Discord),
    накрутка короткими сообщениями обесценивается самой формулой.
    В наследственном режиме (xp_volume=False) — прежние фикс-ставки и кулдаун.
    """
    global PROGRESS_DIRTY
    entry = prog_entry(message.guild.id, message.author.id)
    now = time.time()
    volume = bool(cfg.get("xp_volume", True))
    if not volume and now - entry["last"] < cfg["xp_cooldown"]:
        return
    entry["last"] = now
    rm_xp = role_mult_for(message.author, cfg, "xp")
    rm_cr = role_mult_for(message.author, cfg, "credits")
    ratio = volume_ratio(message, cfg) if volume else 1.0
    lvl_mult = mult_for(cfg, entry["level"])
    # r41: модификаторы перемножаются: уровень × наибольшая роль своего вида
    added_xp = max(1, round(cfg["xp_per_message"] * ratio * lvl_mult * rm_xp))
    entry["xp"] += added_xp
    entry["messages"] += 1
    entry["words"] = entry.get("words", 0) + len((message.content or "").split())
    board_bump(message.guild.id, message.author.id, added_xp, cfg)
    gained = round(max(cfg["credits_per_message"] * ratio, 0.01) * lvl_mult * rm_cr, 2)
    entry["credits"] = round(entry["credits"] + gained, 2)

    new_levels = []
    while entry["level"] < MAX_LEVEL and entry["xp"] >= need_xp(cfg, entry["level"]):
        entry["xp"] -= need_xp(cfg, entry["level"])
        entry["level"] += 1
        new_levels.append(entry["level"])
    PROGRESS_DIRTY = True

    rewards = []
    if new_levels:
        rewards = await apply_level_rewards(message.guild, message.author, cfg, new_levels)
        PROGRESS_DIRTY = True
    if new_levels and cfg["announce_levelup"]:
        lvl = entry["level"]
        desc = (
            f"{message.author.mention} повышает ранг до **{lvl}**!\n"
            f"Множитель {CREDITS_NAME}: **×{mult_for(cfg, lvl):g}**"
        )
        if rewards:
            desc += "\nНаграды: " + ", ".join(rewards)
        embed = discord.Embed(title="⚙ Новый ранг познания", description=desc, color=GOLD_COLOR)
        embed.set_footer(text=FOOTER_TEXT)
        try:
            await message.channel.send(embed=embed)
        except discord.HTTPException:
            pass


# --------------------------------------------------------------------------- #
# Рейтинг активности: циклы «день» и «неделя» с наградой в дата-когах
# --------------------------------------------------------------------------- #
# Счётчики цикла хранятся ОТДЕЛЬНО от накопительного прогресса: в
# data/leaderboard.json по ключу цикла («2026-10-23», «2026-W43»), поэтому
# сброс на границе цикла не трогает опыт/уровни игроков и не требует миграции
# старых данных. Перекат ловит ticker (20 с) и догоняет on_ready: пропущенная
# граница закрывается один раз и идемпотентно (маркер done в том же файле).

def board_utc_offset(cfg: dict) -> int:
    """Сдвиг часового пояса сервера (часы, -12..14): граница циклов по нему."""
    try:
        return max(-12, min(14, int(cfg.get("board_utc_offset", 3))))
    except (TypeError, ValueError):
        return 3

def board_now(cfg: dict) -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=board_utc_offset(cfg))

def _week_id(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"

def board_cycle_ids(cfg: dict):
    """(id дневного цикла, id недельного цикла) на текущий момент."""
    dt = board_now(cfg)
    return dt.strftime("%Y-%m-%d"), _week_id(dt)

def board_prev_ids(cfg: dict):
    dt = board_now(cfg) - timedelta(days=1)
    return dt.strftime("%Y-%m-%d"), _week_id(dt)

def board_next_reset(cfg: dict, period: str) -> int:
    """Unix-время ближайшей границы цикла (для <t:…> в анонсе и таблицах)."""
    dt = board_now(cfg).replace(minute=0, second=0, microsecond=0)
    if period == "d":
        dt = dt.replace(hour=0) + timedelta(days=1)
    else:
        dt = dt.replace(hour=0) + timedelta(days=8 - dt.isocalendar().weekday)
    return int(dt.timestamp())

def _sys_actor() -> int:
    """id актёра для системных записей аудита (вне шлюза bot.user ещё None)."""
    return bot.user.id if bot.user is not None else 0

def board_state(guild_id: int) -> dict:
    st = BOARD.setdefault(str(guild_id), {})
    st.setdefault("done", {})
    st.setdefault("c", {"d": {}, "w": {}})
    st.setdefault("hist", {"d": [], "w": []})
    return st

def board_bump(guild_id: int, user_id: int, xp: int, cfg: dict):
    """+xp и +1 сообщение в счётчики текущих циклов (зовёт grant_activity)."""
    if not cfg.get("board_enabled"):
        return
    day, week = board_cycle_ids(cfg)
    st = board_state(guild_id)
    for period, cid in (("d", day), ("w", week)):
        bucket = st["c"][period].setdefault(cid, {})
        rec = bucket.setdefault(str(user_id), [0, 0])
        rec[0] += int(xp)
        rec[1] += 1

def board_parse_rewards(text: str):
    """Единая грамматика списка наград для команд и сайта: «300,200,100».

    Возвращает (список int, None) либо (None, текст ошибки). Пустая строка —
    отключение наград ([]).
    """
    text = (text or "").strip()
    if not text:
        return [], None
    parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
    out = []
    for p in parts:
        try:
            val = float(p)
        except ValueError:
            return None, f"«{p}» не число"
        if val < 0:
            return None, f"отрицательная награда «{p}»"
        if val > 1_000_000:
            return None, f"награда «{p}» больше 1 000 000"
        out.append(int(val) if float(val).is_integer() else round(val, 2))
    if len(out) > 25:
        return None, "больше 25 мест не награждается"
    return out, None

async def board_top(guild: discord.Guild, cfg: dict, period: str, cycle_id: str):
    """Таблица цикла: [(user_id, xp, messages)] по убыванию xp.

    Вне зачёта: покинувшие сервер, роли из board_exclude_roles и участники
    с сообщениями меньше board_min_messages (порог против накрутки).
    """
    st = board_state(guild.id)
    bucket = st["c"][period].get(cycle_id, {}) or {}
    excl = {int(r) for r in cfg.get("board_exclude_roles", []) if str(r).isdigit()}
    min_msg = int(cfg.get("board_min_messages", 0) or 0)
    rows = []
    for uid, rec in bucket.items():
        if not str(uid).isdigit():
            continue
        member = guild.get_member(int(uid))
        if member is None:
            # кэш пуст (Members Intent выключен) или участник вышел: уточняем запросом
            try:
                member = await guild.fetch_member(int(uid))
            except discord.HTTPException:
                continue  # участника нет на сервере — вне зачёта
        if excl and any(r.id in excl for r in member.roles):
            continue
        xp, msg = (rec + [0, 0])[:2]
        if msg < min_msg:
            continue
        rows.append((int(uid), int(xp), int(msg)))
    rows.sort(key=lambda t: (-t[1], -t[2], t[0]))
    return rows

def board_standings_lines(rows, limit: int, rewards: list) -> list:
    """Строки таблицы/анонса: медаль, упоминание, xp, сообщения, награда."""
    medals = ["\U0001F947", "\U0001F948", "\U0001F949"]
    lines = []
    for i, (uid, xp, msg) in enumerate(rows[:limit], 1):
        mark = medals[i - 1] if i <= 3 else f"**{i}.**"
        prize = rewards[i - 1] if i - 1 < len(rewards) else 0
        tail = f" • 🎁 {fmt_credits(prize)}" if prize else ""
        lines.append(f"{mark} <@{uid}> — {xp} XP • {msg} сообщ.{tail}")
    return lines

async def board_announce(guild: discord.Guild, cfg: dict, period: str, cycle_id: str,
                         rows: list, paid: list):
    """Анонс итогов цикла в board_channel; отказ всегда видим (лог + аудит)."""
    cid = cfg.get("board_channel")
    if not cid:
        return
    channel = guild.get_channel(int(cid))
    if channel is None:
        log.warning("Рейтинг: канал анонса %s не найден в %s", cid, guild.name)
        audit(guild.id, _sys_actor(), "board", f"анонс {cycle_id}: канал не найден")
        return
    rewards = cfg.get("board_rewards_daily" if period == "d" else "board_rewards_weekly", [])
    title = "⚙ Дневной рейтинг кузни" if period == "d" else "⚙ Недельный рейтинг кузни"
    desc = "\n".join(board_standings_lines(rows, int(cfg.get("board_show", 10)), rewards))
    next_ts = board_next_reset(cfg, period)
    embed = discord.Embed(
        title=f"{title} — {cycle_id}",
        description=desc or "В этом цикле никто не отличился.",
        color=GOLD_COLOR,
    )
    embed.add_field(name="До следующего переката", value=f"<t:{next_ts}:R>", inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as exc:
        log.warning("Рейтинг: анонс %s в #%s не отправлен: %s", cycle_id, channel.name, exc)
        audit(guild.id, _sys_actor(), "board", f"анонс {cycle_id}: отказ отправки ({exc})")

async def board_finalize(guild: discord.Guild, cfg: dict, period: str, cycle_id: str):
    """Закрыть цикл: начислить награды, анонсировать, убрать ведро счётчиков."""
    global PROGRESS_DIRTY
    rows = await board_top(guild, cfg, period, cycle_id)
    st = board_state(guild.id)
    rewards = cfg.get("board_rewards_daily" if period == "d" else "board_rewards_weekly", [])
    paid = []
    for i, (uid, _xp, _msg) in enumerate(rows):
        prize = rewards[i] if i < len(rewards) else 0
        if prize:
            entry = prog_entry(guild.id, uid)
            entry["credits"] = round(entry["credits"] + prize, 2)
            paid.append([uid, prize])
    if paid:
        PROGRESS_DIRTY = True
        audit(guild.id, _sys_actor(), "board",
              f"{'день' if period == 'd' else 'неделя'} {cycle_id}: наград {len(paid)}")
    st["hist"][period].append({
        "id": cycle_id, "ts": int(time.time()),
        "rows": [[u, x, m] for u, x, m in rows[:10]],
        "paid": paid,
    })
    del st["hist"][period][:-12]
    st["c"][period].pop(cycle_id, None)
    save_board()
    if rows:
        await board_announce(guild, cfg, period, cycle_id, rows, paid)

async def board_tick(guild: discord.Guild, cfg: dict):
    """Проверка границ циклов: вызывается ticker'ом и при старте бота."""
    if not cfg.get("board_enabled"):
        return
    day, week = board_cycle_ids(cfg)
    st = board_state(guild.id)
    changed = False
    for period, cid in (("d", day), ("w", week)):
        if period == "d" and not cfg.get("board_daily"):
            continue
        if period == "w" and not cfg.get("board_weekly"):
            continue
        done = st["done"].get(period)
        if done is None:
            st["done"][period] = cid
            changed = True
            continue
        if done == cid:
            continue
        # Граница пройдена: закрываем сохранённый цикл. Если бот проспал
        # несколько циклов, промежуточные без данных не анонсируются —
        # закрывается только последний обработанный (done), маркер идёт в «сейчас».
        await board_finalize(guild, cfg, period, done)
        st["done"][period] = cid
        changed = True
    if changed:
        save_board()


# --------------------------------------------------------------------------- #
# Фоновая задача: публикация цитат + сброс прогресса на диск
# --------------------------------------------------------------------------- #
@tasks.loop(seconds=20)
async def ticker():
    global PROGRESS_DIRTY
    if PROGRESS_DIRTY:
        save_progress()
    msg_cache_save()
    await process_giveaways()
    await process_news()
    now = time.time()
    for guild in bot.guilds:
        cfg = get_config(guild.id)
        await board_tick(guild, cfg)
        if not cfg["enabled"]:
            continue
        for cid in list(cfg["channels"]):
            channel = guild.get_channel(cid)
            if channel is None or not isinstance(channel, discord.TextChannel):
                continue
            perms = channel.permissions_for(guild.me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                continue
            if not channel_active(cid, cfg["window_minutes"], cfg["min_messages"], cfg["min_unique_users"]):
                continue
            last = STATE["last_post"].get(str(cid), 0)
            pause = cfg["interval_minutes"] * 60 + random.randint(0, 240)
            if now - last < pause:
                continue
            quote = pick_quote(guild.id, cfg)
            if quote is None:
                continue
            try:
                await channel.send(embed=quote_embed(quote))
                STATE["last_post"][str(cid)] = now
                save_state()
                log.info("Литания отправлена: #%s / канал #%s", guild.name, channel.name)
            except discord.Forbidden:
                log.warning("Нет прав на отправку в #%s (%s)", channel.name, guild.name)
            except discord.HTTPException as exc:
                log.warning("Сбой отправки в #%s: %s", channel.name, exc)


@ticker.before_loop
async def _before_ticker():
    await bot.wait_until_ready()


# --------------------------------------------------------------------------- #
# Группы команд
# --------------------------------------------------------------------------- #
g_quotes = app_commands.Group(name="цитаты", description="Цитаты: автопубликация, каналы, эпохи, цитаты игроков")
g_channels = app_commands.Group(name="каналы", description="Каналы, где публикуются цитаты", parent=g_quotes)
g_admins = app_commands.Group(name="админы", description="Администраторы бота: кто может его настраивать")
g_xp = app_commands.Group(name="уровни", description="Уровни: опыт, каналы, роли, множители, награды, сброс")
g_shop = app_commands.Group(name="магазин", description="Магазин за валюту: товары, покупка, уведомления")
g_loot = app_commands.Group(name="лутбоксы", description="Лутбоксы: создание, наполнение, открытие за валюту")
g_gw = app_commands.Group(name="розыгрыши", description="Розыгрыши: предметы в инвентарь или валюта на баланс")
g_welcome = app_commands.Group(name="приветствие", description="Приветствие новичков: канал, текст, проверка")
g_grant = app_commands.Group(name="выдача", description="Ручная выдача игрокам: предметы, опыт, валюта")
g_reaction = app_commands.Group(name="реакции", description="Роли за реакции: панели с эмодзи")
g_tickets = app_commands.Group(name="тикеты", description="Тикеты: панель создания, закрытие, настройки")
g_audit = app_commands.Group(name="аудит", description="Аудит сервера: журнал событий и отчёт безопасности")

# Разделы внутри групп: путь команды читается как фраза «раздел → объект → действие»
sg_xp_channels = app_commands.Group(name="каналы", description="Каналы, где начисляются опыт и валюта", parent=g_xp)
sg_xp_roles = app_commands.Group(name="роли", description="Множители опыта и валюты для ролей", parent=g_xp)
sg_xp_graces = app_commands.Group(name="множители", description="Множитель валюты по уровням", parent=g_xp)
sg_xp_rewards = app_commands.Group(name="награды", description="Награды за уровни: роль, предмет, валюта", parent=g_xp)
sg_xp_reset = app_commands.Group(name="сброс", description="Сброс прогресса: один игрок или весь сервер", parent=g_xp)
sg_grant_items = app_commands.Group(name="предметы", description="Предметы в инвентарь игрока вручную", parent=g_grant)
sg_grant_balance = app_commands.Group(name="баланс", description="Опыт и валюта игрока вручную", parent=g_grant)



def config_status_embed(guild: discord.Guild, cfg: dict) -> discord.Embed:
    custom = CUSTOM.get(str(guild.id), [])
    approved = sum(1 for q in custom if q.get("status") == "approved")
    pending = sum(1 for q in custom if q.get("status") == "pending")
    channels = ", ".join(f"<#{c}>" for c in cfg["channels"]) or "— не назначены —"
    xp_channels = ", ".join(f"<#{c}>" for c in cfg["xp_channels"]) or "— не назначены —"
    embed = discord.Embed(title="⚙ Устои кузни", color=EMBED_COLOR)
    embed.add_field(name="Литании", value="✅ включены" if cfg["enabled"] else "⛔ выключены", inline=True)
    embed.add_field(name="Эпохи", value=ERA_FILTER_LABELS.get(cfg["era"], cfg["era"]), inline=True)
    embed.add_field(name="Цитаты игроков", value=MODE_LABELS.get(cfg["quotes_mode"], cfg["quotes_mode"]), inline=False)
    embed.add_field(name="Каналы цитат", value=channels, inline=False)
    embed.add_field(
        name="Частота и активность",
        value=(
            f"Пауза между литаниями: **{cfg['interval_minutes']} мин**\n"
            f"Окно активности: **{cfg['window_minutes']} мин**\n"
            f"Минимум сообщений в окне: **{cfg['min_messages']}**\n"
            f"Минимум разных авторов: **{cfg['min_unique_users']}**"
        ),
        inline=False,
    )
    embed.add_field(name="Каналы прогрессии", value=xp_channels, inline=False)
    embed.add_field(
        name="Прогрессия",
        value=(
            f"XP за сообщение: **{cfg['xp_per_message']}** "
            + ("(объёмный режим, без кулдауна)" if cfg.get("xp_volume", True)
               else f"(кулдаун {cfg['xp_cooldown']} с)") + "\n"
            f"{CREDITS_NAME} за сообщение: **{cfg['credits_per_message']}** × множитель ранга × коэффициент роли\n"
            f"Кривые: **{curve_desc(cfg)}**\n"
            "Модификатор уровня перемножается с ролью\n"
            f"Анонс повышений: {'✅' if cfg['announce_levelup'] else '⛔'}"
        ),
        inline=False,
    )
    role_mults = cfg.get("role_multipliers", {})
    if role_mults:
        rm_lines = "\n".join(f"<@&{rid}> — ×{e['mult']:g}" for rid, e in list(role_mults.items())[:8])
        if len(role_mults) > 8:
            rm_lines += f"\n… и ещё {len(role_mults) - 8}"
    else:
        rm_lines = "— не заданы —"
    embed.add_field(name="Ролевые коэффициенты", value=rm_lines, inline=False)
    welcome_state = "✅ включены" if cfg["welcome_enabled"] and cfg["welcome_channel"] else "⛔ выключены"
    shop_ch = f"<#{cfg['shop_notify_channel']}>" if cfg["shop_notify_channel"] else "— не назначен —"
    shop_role = f"<@&{cfg['shop_notify_role']}>" if cfg["shop_notify_role"] else "— не назначена —"
    welcome_channel = f"<#{cfg['welcome_channel']}>" if cfg["welcome_channel"] else "— не назначен —"
    embed.add_field(name="Посвящения", value=f"{welcome_state} • канал: {welcome_channel}", inline=False)
    embed.add_field(name="Лавка: уведомления", value=f"Канал: {shop_ch}\nРоль: {shop_role}", inline=False)
    embed.add_field(name="Цитаты игроков на сервере", value=f"Одобрено: {approved} • На модерации: {pending}", inline=False)
    admins = cfg.get("admin_ids", [])
    if admins:
        admin_line = ", ".join(f"<@{uid}>" for uid in admins)
    else:
        admin_line = ("— не назначены —\n⚠️ Сейчас настройка доступна владельцу сервера и всем "
                      "с правом «Управление сервером». Закрепите одного человека: `/админы добавить`")
    embed.add_field(name="Совет магосов", value=admin_line, inline=False)
    boxes = loot_boxes(guild.id)
    embed.add_field(name="Тех-арки кузни", value=f"Создано: {len(boxes)}", inline=True)
    active_gw = sum(1 for g in guild_giveaways(guild.id) if g.get("status") == "active")
    embed.add_field(name="Активные розыгрыши", value=str(active_gw), inline=True)
    tk_cat = f"<#{cfg['ticket_category']}>" if cfg.get("ticket_category") else "— не настроены —"
    embed.add_field(name="Тикеты", value=f"Категория: {tk_cat} • панель: `/тикеты панель`", inline=False)
    aud_ch = f"<#{cfg['audit_channel']}>" if cfg.get("audit_channel") else "— не задан —"
    embed.add_field(name="Журнал событий (аудит)", value=f"{aud_ch} • настройки: `/аудит настройки`", inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    return embed


# ------------------------------- /цитаты, /админы --------------------------- #
@g_quotes.command(name="настройки", description="Показать текущие настройки бота на этом сервере")
@app_commands.check(is_bot_admin)
async def cfg_status(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    await interaction.response.send_message(embed=config_status_embed(interaction.guild, cfg))


@g_admins.command(name="добавить", description="Добавить администратора бота (кто может его настраивать)")
@app_commands.check(is_bot_admin)
async def cfg_admin_add(interaction: discord.Interaction, user: discord.User):
    cfg = get_config(interaction.guild.id)
    if user.id in cfg["admin_ids"]:
        await interaction.response.send_message(f"{user.mention} уже в совете магосов.")
        return
    cfg["admin_ids"].append(user.id)
    save_configs()
    await interaction.response.send_message(
        f"✅ {user.mention} призван в совет магосов.\n"
        f"Теперь настраивать бота на этом сервере могут только: "
        + ", ".join(f"<@{uid}>" for uid in cfg["admin_ids"])
        + " (и владелец приложения-бота)."
    )


@g_admins.command(name="убрать", description="Убрать администратора бота")
@app_commands.check(is_bot_admin)
async def cfg_admin_remove(interaction: discord.Interaction, user: discord.User):
    cfg = get_config(interaction.guild.id)
    if user.id not in cfg["admin_ids"]:
        await interaction.response.send_message(f"{user.mention} не состоит в совете магосов.")
        return
    cfg["admin_ids"].remove(user.id)
    save_configs()
    warning = ""
    if user.id == interaction.user.id and not cfg["admin_ids"]:
        warning = ("\n⚠️ Список опустел: настройка снова доступна владельцу сервера "
                   "и всем с правом «Управление сервером».")
    await interaction.response.send_message(f"➖ {user.mention} снят с администраторов бота.{warning}")


@g_admins.command(name="список", description="Кто может настраивать бота на этом сервере")
@app_commands.check(is_bot_admin)
async def cfg_admin_list(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    if cfg["admin_ids"]:
        lines = "\n".join(f"• <@{uid}>" for uid in cfg["admin_ids"])
    else:
        lines = ("— список пуст —\nНастройка доступна владельцу сервера и всем с правом "
                 "«Управление сервером», пока вы не назначите администратора через `/админы добавить`.")
    await interaction.response.send_message("🛡️ Совет магосов:\n" + lines)


@g_quotes.command(name="включить", description="Включить автопубликацию цитат")
@app_commands.check(is_bot_admin)
async def cfg_enable(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    cfg["enabled"] = True
    save_configs()
    if not cfg["channels"]:
        await interaction.response.send_message(
            "✅ Автопубликация цитат включена. Добавьте каналы: `/цитаты каналы добавить`."
        )
    else:
        await interaction.response.send_message("✅ Литании звучат и следят за диалогами.")


@g_quotes.command(name="выключить", description="Выключить автопубликацию цитат")
@app_commands.check(is_bot_admin)
async def cfg_disable(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    cfg["enabled"] = False
    save_configs()
    await interaction.response.send_message("⛔ Литании умолкли.")


@g_channels.command(name="добавить", description="Добавить канал, где публикуются цитаты")
@app_commands.check(is_bot_admin)
async def cfg_channels_add(interaction: discord.Interaction, channel: discord.TextChannel):
    cfg = get_config(interaction.guild.id)
    if channel.id in cfg["channels"]:
        await interaction.response.send_message(f"Канал {channel.mention} уже в списке цитат.")
        return
    cfg["channels"].append(channel.id)
    save_configs()
    await interaction.response.send_message(f"➕ Канал {channel.mention} добавлен в список цитат.")


@g_channels.command(name="убрать", description="Убрать канал публикации цитат")
@app_commands.check(is_bot_admin)
async def cfg_channels_remove(interaction: discord.Interaction, channel: discord.TextChannel):
    cfg = get_config(interaction.guild.id)
    if channel.id not in cfg["channels"]:
        await interaction.response.send_message(f"Канала {channel.mention} нет в списке цитат.")
        return
    cfg["channels"].remove(channel.id)
    save_configs()
    await interaction.response.send_message(f"➖ Канал {channel.mention} убран из списка цитат.")


@g_channels.command(name="список", description="Список каналов публикации цитат")
@app_commands.check(is_bot_admin)
async def cfg_channels_list(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    channels = "\n".join(f"<#{c}>" for c in cfg["channels"]) or "— список пуст —"
    await interaction.response.send_message(f"📜 Каналы цитат:\n{channels}")


@g_quotes.command(name="интервал", description="Частота публикации цитат и критерии активности канала")
@app_commands.check(is_bot_admin)
async def cfg_frequency(
    interaction: discord.Interaction,
    interval: Optional[app_commands.Range[int, 5, 10080]] = None,
    window: Optional[app_commands.Range[int, 1, 120]] = None,
    min_messages: Optional[app_commands.Range[int, 2, 200]] = None,
    min_users: Optional[app_commands.Range[int, 2, 100]] = None,
):
    cfg = get_config(interaction.guild.id)
    if interval is not None:
        cfg["interval_minutes"] = interval
    if window is not None:
        cfg["window_minutes"] = window
    if min_messages is not None:
        cfg["min_messages"] = min_messages
    if min_users is not None:
        cfg["min_unique_users"] = min_users
    save_configs()
    await interaction.response.send_message(
        "✅ Настройки частоты обновлены:\n"
        f"Пауза между литаниями: **{cfg['interval_minutes']} мин**\n"
        f"Окно активности: **{cfg['window_minutes']} мин**, "
        f"минимум **{cfg['min_messages']}** сообщ. от **{cfg['min_unique_users']}+** авторов."
    )


@g_quotes.command(name="эпоха", description="Какие эпохи цитат публиковать")
@app_commands.check(is_bot_admin)
@app_commands.choices(era=[
    app_commands.Choice(name="Все эпохи", value="any"),
    app_commands.Choice(name="Ересь Хоруса", value="heresy"),
    app_commands.Choice(name="Империум (40k)", value="imperium"),
])
async def cfg_era(interaction: discord.Interaction, era: app_commands.Choice[str]):
    cfg = get_config(interaction.guild.id)
    cfg["era"] = era.value
    save_configs()
    await interaction.response.send_message(f"✅ Фильтр эпох: **{ERA_FILTER_LABELS[era.value]}**.")


@g_quotes.command(name="режим", description="Режим цитат игроков: проверка, автоодобрение или выкл")
@app_commands.check(is_bot_admin)
@app_commands.choices(mode=[
    app_commands.Choice(name="Модерация (проверка админом)", value="moderate"),
    app_commands.Choice(name="Авто-одобрение", value="auto"),
    app_commands.Choice(name="Отключить цитаты игроков", value="off"),
])
async def cfg_quotes_mode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    cfg = get_config(interaction.guild.id)
    cfg["quotes_mode"] = mode.value
    save_configs()
    await interaction.response.send_message(f"✅ Режим цитат игроков: **{MODE_LABELS[mode.value]}**.")


# ------------------------------- /архив ------------------------------------ #
@g_quotes.command(name="предложить", description="Предложить свою цитату (после проверки попадёт в ротацию)")
@app_commands.choices(era=[
    app_commands.Choice(name="Обе эпохи", value="both"),
    app_commands.Choice(name="Ересь Хоруса", value="heresy"),
    app_commands.Choice(name="Империум (40k)", value="imperium"),
])
async def quote_add(
    interaction: discord.Interaction,
    text: app_commands.Range[str, 3, 1000],
    author: Optional[str] = None,
    source: Optional[str] = None,
    era: Optional[app_commands.Choice[str]] = None,
):
    guild_id = str(interaction.guild.id)
    entries = CUSTOM.setdefault(guild_id, [])
    cfg = get_config(interaction.guild.id)
    if cfg["quotes_mode"] == "off":
        await interaction.response.send_message(
            "⛔ Добавление цитат игроков отключено администрацией сервера."
        )
        return
    status = "approved" if cfg["quotes_mode"] == "auto" else "pending"
    new_id = (max([q["id"] for q in entries], default=0) + 1)
    entries.append({
        "id": new_id,
        "text": text,
        "author": author or "Неизвестный автор",
        "source": source or "Добавлено игроками сервера",
        "era": era.value if era else "both",
        "status": status,
        "added_by": interaction.user.id,
        "ts": int(time.time()),
    })
    save_custom()
    if status == "approved":
        await interaction.response.send_message(f"✅ Литания №{new_id} сохранена и добавлена в ротацию.")
    else:
        await interaction.response.send_message(
            f"📨 Цитата №{new_id} отправлена на модерацию. Совет магосов одобрит её через `/цитаты одобрить`."
        )


@g_quotes.command(name="очередь", description="Цитаты игроков, ожидающие проверки")
@app_commands.check(is_bot_admin)
async def quote_pending(interaction: discord.Interaction):
    view = ModerationView(interaction.guild.id, interaction.user.id)
    view.rebuild()
    await interaction.response.send_message(embed=view.build_embed(), view=view)
    await view.bind(interaction)
@g_quotes.command(name="одобрить", description="Одобрить цитату игрока (добавить в ротацию)")
@app_commands.check(is_bot_admin)
async def quote_approve(interaction: discord.Interaction, quote_id: int):
    entries = CUSTOM.get(str(interaction.guild.id), [])
    for q in entries:
        if q["id"] == quote_id:
            if q["status"] == "approved":
                await interaction.response.send_message(f"Литания №{quote_id} уже одобрена.")
            else:
                q["status"] = "approved"
                save_custom()
                await interaction.response.send_message(f"✅ Литания №{quote_id} одобрена и в ротации.")
            return
    await interaction.response.send_message(f"Литания №{quote_id} не найдена.")


@g_quotes.command(name="отклонить", description="Отклонить и удалить цитату игрока")
@app_commands.check(is_bot_admin)
async def quote_reject(interaction: discord.Interaction, quote_id: int):
    entries = CUSTOM.get(str(interaction.guild.id), [])
    for q in list(entries):
        if q["id"] == quote_id:
            entries.remove(q)
            save_custom()
            await interaction.response.send_message(f"🗑 Литания №{quote_id} отклонена и удалена.")
            return
    await interaction.response.send_message(f"Литания №{quote_id} не найдена.")


@g_quotes.command(name="случайная", description="Показать случайную цитату прямо сейчас")
async def quote_random(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    pool = build_pool(interaction.guild.id, cfg["era"], cfg["quotes_mode"])
    if not pool:
        await interaction.response.send_message("Книга литаний пуста.")
        return
    _, quote = random.choice(pool)
    await interaction.response.send_message(embed=quote_embed(quote))


# ------------------------------- /уровни ----------------------------------- #
async def _xp_channel_set(interaction: discord.Interaction, channel: discord.TextChannel, add: bool):
    """Общий ход для /уровни каналы добавить | убрать."""
    cfg = get_config(interaction.guild.id)
    if add:
        if channel.id in cfg["xp_channels"]:
            await interaction.response.send_message(f"Канал {channel.mention} уже канал прогрессии.")
            return
        cfg["xp_channels"].append(channel.id)
        save_configs()
        await interaction.response.send_message(f"➕ Канал прогрессии добавлен: {channel.mention}")
    else:
        if channel.id not in cfg["xp_channels"]:
            await interaction.response.send_message(f"Канал {channel.mention} не является каналом прогрессии.")
            return
        cfg["xp_channels"].remove(channel.id)
        save_configs()
        await interaction.response.send_message(f"➖ Канал прогрессии убран: {channel.mention}")


@sg_xp_channels.command(name="добавить", description="Добавить канал, где начисляются опыт и валюта")
@app_commands.check(is_bot_admin)
async def xp_channel_add(interaction: discord.Interaction,
                         channel: Union[discord.TextChannel, discord.ForumChannel]):
    await _xp_channel_set(interaction, channel, True)


@sg_xp_channels.command(name="убрать", description="Убрать канал начисления опыта и валюты")
@app_commands.check(is_bot_admin)
async def xp_channel_remove(interaction: discord.Interaction,
                            channel: Union[discord.TextChannel, discord.ForumChannel]):
    await _xp_channel_set(interaction, channel, False)


@sg_xp_channels.command(name="список", description="Список каналов, где начисляются опыт и валюта")
@app_commands.check(is_bot_admin)
async def xp_channels_list(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    channels = "\n".join(f"<#{c}>" for c in cfg["xp_channels"]) or "— список пуст —"
    await interaction.response.send_message(f"Каналы прогрессии:\n{channels}")

@g_xp.command(name="настройки", description="Опыт и валюта за сообщение, кулдаун, кривые уровней")
@app_commands.check(is_bot_admin)
@app_commands.choices(xp_mode=[
    app_commands.Choice(name="шаг: +N опыта за ранг", value="add"),
    app_commands.Choice(name="множитель: ×N за ранг", value="mul"),
], mult_mode=[
    app_commands.Choice(name="шаг: +N к модификатору за ранг", value="add"),
    app_commands.Choice(name="множитель: модификатор ×N за ранг", value="mul"),
])
async def pr_rates(
    interaction: discord.Interaction,
    xp: Optional[app_commands.Range[int, 1, 1000]] = None,
    credits: Optional[app_commands.Range[int, 1, 1000]] = None,
    cooldown: Optional[app_commands.Range[int, 5, 3600]] = None,
    xp_base: Optional[app_commands.Range[int, 10, 100000]] = None,
    xp_step: Optional[app_commands.Range[int, 1, 100000]] = None,
    mult_step: Optional[app_commands.Range[float, 0.01, 10.0]] = None,
    xp_mode: Optional[app_commands.Choice[str]] = None,
    xp_mul: Optional[app_commands.Range[float, 1.01, 5.0]] = None,
    mult_mode: Optional[app_commands.Choice[str]] = None,
    mult_mul: Optional[app_commands.Range[float, 1.01, 5.0]] = None,
    volume: Optional[bool] = None,
    cap: Optional[app_commands.Range[int, 100, 4000]] = None,
    exclusive: Optional[bool] = None,
):
    cfg = get_config(interaction.guild.id)
    if xp is not None:
        cfg["xp_per_message"] = xp
    if credits is not None:
        cfg["credits_per_message"] = credits
    if cooldown is not None:
        cfg["xp_cooldown"] = cooldown
    if xp_base is not None:
        cfg["xp_base"] = xp_base
    if xp_step is not None:
        cfg["xp_step"] = xp_step
    if xp_mode is not None:
        cfg["xp_mode"] = xp_mode
    if xp_mul is not None:
        cfg["xp_mul"] = xp_mul
    rebuild = False
    if mult_step is not None:
        cfg["mult_step"] = mult_step
        rebuild = True
    if mult_mode is not None:
        cfg["mult_mode"] = mult_mode
        rebuild = True
    if mult_mul is not None:
        cfg["mult_mul"] = mult_mul
        rebuild = True
    if rebuild:
        cfg["multipliers"] = mult_table(cfg)
    if volume is not None:
        cfg["xp_volume"] = volume
    if cap is not None:
        cfg["xp_char_cap"] = cap
    if exclusive is not None:
        cfg["level_roles_exclusive"] = exclusive
    save_configs()
    mode = ("объёмный: награда растёт с длиной сообщения до "
            f"{cfg['xp_char_cap']} символов, кулдаун отключён") if cfg.get("xp_volume", True) else \
        f"фиксированный, кулдаун {cfg['xp_cooldown']} с"
    await interaction.response.send_message(
        "✅ Ставки прогрессии обновлены:\n"
        f"Режим: **{mode}**\n"
        f"XP на лимите символов: **{cfg['xp_per_message']}** (короткие сообщения дают меньше, минимум 1)\n"
        f"{CREDITS_NAME} за сообщение: **{cfg['credits_per_message']}** × множитель ранга × коэффициент роли\n"
        f"Кривые: **{curve_desc(cfg)}**\n"
        f"(ур.1→2: {need_xp(cfg, 1)} XP, ур.9→10: {need_xp(cfg, 9)} XP; "
        f"модификаторы ур.1 ×{mult_for(cfg, 1):g}, ур.2 ×{mult_for(cfg, 2):g}, ур.10 ×{mult_for(cfg, 10):g})\n"
        "Множитель уровня перемножается с наибольшим множителем роли"
    )


@g_xp.command(name="таблица", description="Сколько опыта нужно для уровней 1–100")
@app_commands.check(is_bot_admin)
async def pr_xp_show(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    lines = []
    for i in range(0, MAX_LEVEL, 10):
        chunk = ", ".join(f"{i + j + 1}→{i + j + 2}:{need_xp(cfg, i + j + 1)}" for j in range(9))
        lines.append(chunk)
    await interaction.response.send_message(
        f"📈 Требуемый XP ({curve_desc(cfg)}):\n"
        + "```\n" + "\n".join(lines) + "\n```"
    )


@sg_xp_roles.command(name="добавить", description="Задать роли множитель опыта и валюты")
@app_commands.check(is_bot_admin)
@app_commands.choices(apply=[
    app_commands.Choice(name="Опыт и валюта", value="both"),
    app_commands.Choice(name="Только валюта", value="credits"),
    app_commands.Choice(name="Только опыт", value="xp"),
])
async def xp_role_add(
    interaction: discord.Interaction,
    role: discord.Role,
    value: Optional[app_commands.Range[float, 0.1, 100.0]] = None,
    apply: Optional[app_commands.Choice[str]] = None,
):
    cfg = get_config(interaction.guild.id)
    if value is None:
        await interaction.response.send_message("Укажите value:число (и apply при желании).")
        return
    apply_to = apply.value if apply else "both"
    cfg["role_multipliers"][str(role.id)] = {"mult": round(value, 2), "apply": apply_to}
    save_configs()
    where = {"both": "XP и дата-коги", "credits": "дата-коги", "xp": "XP"}[apply_to]
    await interaction.response.send_message(
        f"✅ Роль {role.mention}: дополнительный коэффициент **×{round(value, 2):g}** ({where}).")


@sg_xp_roles.command(name="убрать", description="Убрать множитель у роли")
@app_commands.check(is_bot_admin)
async def xp_role_remove(interaction: discord.Interaction, role: discord.Role):
    cfg = get_config(interaction.guild.id)
    if str(role.id) not in cfg["role_multipliers"]:
        await interaction.response.send_message(f"У роли {role.mention} нет коэффициента.")
        return
    del cfg["role_multipliers"][str(role.id)]
    save_configs()
    await interaction.response.send_message(f"➖ Коэффициент роли {role.mention} удалён.")

@sg_xp_roles.command(name="список", description="Список ролей с множителями")
@app_commands.check(is_bot_admin)
async def xp_role_list(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    if not cfg["role_multipliers"]:
        await interaction.response.send_message("Ролевые коэффициенты не заданы.")
        return
    where = {"both": "XP и дата-коги", "credits": "дата-коги", "xp": "XP"}
    lines = [
        f"<@&{rid}> — ×{e['mult']:g} ({where.get(e.get('apply', 'both'), e.get('apply'))})"
        for rid, e in cfg["role_multipliers"].items()
    ]
    await interaction.response.send_message("⚙️ Ролевые коэффициенты:\n" + "\n".join(lines))

@sg_xp_graces.command(name="задать", description="Задать множитель валюты для конкретного уровня")
@app_commands.check(is_bot_admin)
async def xp_mult_set(
    interaction: discord.Interaction,
    level: app_commands.Range[int, 1, 100],
    value: app_commands.Range[float, 0.1, 10000.0],
):
    cfg = get_config(interaction.guild.id)
    cfg["multipliers"][level - 1] = round(value, 2)
    save_configs()
    await interaction.response.send_message(
        f"✅ Ранг **{level}**: множитель {CREDITS_NAME} = **×{round(value, 2):g}**.")

@sg_xp_graces.command(name="таблица", description="Таблица множителей валюты по уровням 1–100; сброс к стандартной")
@app_commands.check(is_bot_admin)
@app_commands.choices(action=[
    app_commands.Choice(name="Показать", value="show"),
    app_commands.Choice(name="Сбросить", value="reset"),
])
async def xp_mult_show(interaction: discord.Interaction, action: Optional[app_commands.Choice[str]] = None):
    cfg = get_config(interaction.guild.id)
    if action is not None and action.value == "reset":
        cfg["multipliers"] = mult_table(cfg)
        save_configs()
        await interaction.response.send_message("✅ Таблица множителей сброшена к стандартной (×1, ×1.1, ×1.3, …).")
        return
    lines = []
    for i in range(0, MAX_LEVEL, 10):
        chunk = ", ".join(f"{i + j + 1}:×{cfg['multipliers'][i + j]:g}" for j in range(10))
        lines.append(chunk)
    await interaction.response.send_message(
        "📊 Множители дата-когов по рангам:\n```\n" + "\n".join(lines) + "\n```")

@g_xp.command(name="анонсы", description="Сообщать в канал о повышении уровня (вкл/выкл)")
@app_commands.check(is_bot_admin)
@app_commands.choices(mode=[
    app_commands.Choice(name="Включить", value="on"),
    app_commands.Choice(name="Выключить", value="off"),
])
async def pr_levelup_announce(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    cfg = get_config(interaction.guild.id)
    cfg["announce_levelup"] = mode.value == "on"
    save_configs()
    await interaction.response.send_message(
        f"✅ Анонс повышений: {'включён' if cfg['announce_levelup'] else 'выключен'}."
    )


@sg_xp_rewards.command(name="добавить", description="Награда за уровень: роль, предмет или валюта")
@app_commands.check(is_bot_admin)
@app_commands.choices(kind=[
    app_commands.Choice(name="Роль", value="role"),
    app_commands.Choice(name="Предмет в инвентарь", value="item"),
    app_commands.Choice(name="Валюта", value="credits"),
])
async def pr_reward_add(
    interaction: discord.Interaction,
    level: app_commands.Range[int, 1, 100],
    kind: app_commands.Choice[str],
    role: Optional[discord.Role] = None,
    name: Optional[str] = None,
    amount: Optional[app_commands.Range[int, 1, 1_000_000]] = None,
):
    cfg = get_config(interaction.guild.id)
    if kind.value == "role" and role is None:
        await interaction.response.send_message("Для награды ролью укажите role:@роль.")
        return
    if kind.value == "item" and not (name or "").strip():
        await interaction.response.send_message("Для награды предметом укажите name:«название».")
        return
    if kind.value == "credits" and not amount:
        await interaction.response.send_message("Для награды дата-когами укажите amount:число.")
        return
    rewards = cfg.setdefault("level_rewards", [])
    new_id = max([r.get("id", 0) for r in rewards], default=0) + 1
    rewards.append({
        "id": new_id, "level": level, "kind": kind.value,
        "role_id": role.id if role else None,
        "item_name": (name or "").strip()[:80] or None,
        "credits": int(amount or 0),
    })
    save_configs()
    await interaction.response.send_message(f"✅ Награда №{new_id} за ранг {level} добавлена.")

@sg_xp_rewards.command(name="список", description="Список наград за уровни")
@app_commands.check(is_bot_admin)
async def pr_reward_list(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    rewards = cfg.get("level_rewards", [])
    if not rewards:
        await interaction.response.send_message("Награды за ранги не заданы.")
        return
    where = {"role": "роль", "item": "предмет", "credits": "дата-коги"}
    lines = []
    for r in rewards:
        val = ""
        if r["kind"] == "role":
            role = interaction.guild.get_role(int(r.get("role_id") or 0))
            val = f"@{role.name if role else r.get('role_id')}"
        elif r["kind"] == "item":
            val = f"«{r.get('item_name')}»"
        else:
            val = fmt_credits(r.get("credits", 0))
        lines.append(f"**№{r['id']}** ур. {r['level']} → {where.get(r['kind'], r['kind'])}: {val}")
    await interaction.response.send_message("🎖 Награды за ранги:\n" + "\n".join(lines))

@sg_xp_rewards.command(name="убрать", description="Убрать награду за уровень по номеру (reward_id)")
@app_commands.check(is_bot_admin)
async def pr_reward_remove(interaction: discord.Interaction, reward_id: int):
    cfg = get_config(interaction.guild.id)
    rewards = cfg.get("level_rewards", [])
    for r in list(rewards):
        if r.get("id") == reward_id:
            rewards.remove(r)
            save_configs()
            await interaction.response.send_message(f"🗑 Награда №{reward_id} удалена.")
            return
    await interaction.response.send_message(f"Награда №{reward_id} не найдена.")

@sg_xp_reset.command(name="игрока", description="Сбросить уровень и валюту одного игрока")
@app_commands.check(is_bot_admin)
async def pr_reset(interaction: discord.Interaction, user: discord.User):
    g = PROGRESS.get(str(interaction.guild.id), {})
    if str(user.id) not in g:
        await interaction.response.send_message(f"У {user.mention} нет прогресса на этом сервере.")
        return
    g[str(user.id)] = {"xp": 0, "level": 1, "credits": 0.0, "messages": 0, "words": 0, "last": 0}
    save_progress()
    await interaction.response.send_message(
        f"♻️ Прогресс {user.mention} сброшен: ранг 1, 0 {CREDITS_NAME}."
    )


@sg_xp_reset.command(name="всех", description="Сбросить уровень и валюту ВСЕХ игроков сервера")
@app_commands.check(is_bot_admin)
async def pr_reset_all(interaction: discord.Interaction, confirm: bool):
    if not confirm:
        await interaction.response.send_message(
            "⚠️ Это сбросит ранги и дата-коги всех игроков. Повторите с `confirm: True`."
        )
        return
    PROGRESS[str(interaction.guild.id)] = {}
    save_progress()
    await interaction.response.send_message("♻️ Прогресс всех игроков сервера сброшен.")


# ------------------------------- /level, /top ------------------------------ #
@bot.tree.command(name="ранг", description="Ваш уровень, прогресс опыта и баланс валюты")
async def cmd_level(interaction: discord.Interaction, user: Optional[discord.User] = None):
    member = user or interaction.user
    cfg = get_config(interaction.guild.id)
    entry = prog_entry(interaction.guild.id, member.id)
    lvl = entry["level"]
    need = need_xp(cfg, lvl)
    guild_member = interaction.guild.get_member(member.id)
    rm_cr = role_mult_for(guild_member, cfg, "credits")
    rm_xp = role_mult_for(guild_member, cfg, "xp")
    if lvl >= MAX_LEVEL:
        bar_line = f"**{MAX_LEVEL} / {MAX_LEVEL}** — максимум достигнут"
    else:
        bar_line = f"{progress_bar(entry['xp'], need)}  {entry['xp']} / {need} XP"
    embed = discord.Embed(
        title=f"⚙ Ранг познания: {member.display_name}",
        color=EMBED_COLOR,
    )
    embed.add_field(name="Ранг", value=f"**{lvl}** / {MAX_LEVEL}", inline=True)
    embed.add_field(name="Множитель уровня", value=f"×{mult_for(cfg, lvl):g}", inline=True)
    embed.add_field(name="Итог (уровень × роль)",
                    value=f"XP ×{mult_for(cfg, lvl) * rm_xp:g} • {CREDITS_NAME} ×{mult_for(cfg, lvl) * rm_cr:g}",
                    inline=True)
    embed.add_field(name="Место в реестре", value=f"#{guild_rank(interaction.guild.id, member.id)}", inline=True)
    embed.add_field(name="Путь ранга", value=bar_line, inline=False)
    embed.add_field(name=CREDITS_NAME, value=f"**{fmt_credits(entry['credits'])}**", inline=True)
    embed.add_field(name="Слов сказано", value=str(entry.get("words", 0)), inline=True)
    embed.add_field(name="Сообщений", value=str(entry["messages"]), inline=True)
    role_line = "—"
    if rm_cr != 1.0 or rm_xp != 1.0:
        parts = []
        if rm_cr != 1.0:
            parts.append(f"дата-коги ×{rm_cr:g}")
        if rm_xp != 1.0:
            parts.append(f"XP ×{rm_xp:g}")
        role_line = ", ".join(parts)
    embed.add_field(name="Знамя кузни", value=role_line, inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="топ", description="Топ-10 игроков сервера по уровню")
async def cmd_top(interaction: discord.Interaction):
    entries = PROGRESS.get(str(interaction.guild.id), {})
    order = sorted(entries.items(), key=lambda kv: (kv[1]["level"], kv[1]["xp"]), reverse=True)[:10]
    if not order:
        await interaction.response.send_message(
            "Топ пуст: ещё никто не зарабатывал опыт. Пишите в каналах прогрессии!", ephemeral=False
        )
        return
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (uid, e) in enumerate(order, 1):
        mark = medals[i - 1] if i <= 3 else f"**{i}.**"
        lines.append(
            f"{mark} <@{uid}> — ур. **{e['level']}** ({e['xp']} XP) • {fmt_credits(e['credits'])} {CREDITS_NAME}"
        )
    embed = discord.Embed(title="⚙ Ранги кузни", description="\n".join(lines), color=GOLD_COLOR)
    embed.set_footer(text=FOOTER_TEXT)
    await interaction.response.send_message(embed=embed)


# --------------------------------------------------------------------------- #
# Новости: черновики, отложенная публикация, правка на месте (r27)
# --------------------------------------------------------------------------- #
# Два входа (команды и сайт) пользуются ОДНОЙ валидацией news_validate и одним
# издателем news_send — урок r22/r23: грамматика ввода не должна расходиться.
# Отложенные публикации догоняет ticker (20 с), опубликованные хранят id
# сообщений по каналам — правка редактирует embed на месте.

def news_guild(guild_id: int) -> dict:
    g = NEWS.setdefault(str(guild_id), {})
    g.setdefault("cfg", {"channels": [], "ping_role": None})
    g.setdefault("drafts", {})
    g.setdefault("published", [])
    g.setdefault("seq", 1)
    return g

def news_validate(title: str, text: str, image: str = "") -> str:
    """Единая валидация черновика для команд и сайта; '' = годен."""
    title = (title or "").strip()
    text = (text or "").strip()
    image = (image or "").strip()
    if not title:
        return "заголовок пуст"
    if len(title) > 256:
        return "заголовок длиннее 256 символов"
    if not text:
        return "текст новости пуст"
    if len(text) > 4000:
        return "текст длиннее 4000 символов"
    if image and not image.startswith(("http://", "https://")):
        return "ссылка на изображение должна начинаться с http:// или https://"
    return ""

def news_parse_when(when: str, utc_offset: int):
    """«45» = через 45 минут; «2026-10-24 20:00» = момент в поясе сервера.

    Возвращает (unix_ts, None) либо (None, текст ошибки).
    """
    when = (when or "").strip()
    if not when:
        return None, None
    if when.isdigit():
        return int(time.time()) + 60 * int(when), None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(when, fmt)
        except ValueError:
            continue
        dt = dt.replace(tzinfo=timezone(timedelta(hours=utc_offset)))
        return int(dt.timestamp()), None
    return None, "время не распознано: нужно «45» (минуты) или «2026-10-24 20:00»"

def news_build_embed(entry: dict) -> discord.Embed:
    embed = discord.Embed(title=f"📰 {entry['title']}", description=entry["text"], color=GOLD_COLOR)
    if entry.get("image"):
        embed.set_image(url=entry["image"])
    embed.set_footer(text=FOOTER_TEXT)
    return embed

def news_due(gdata: dict, now: float) -> list:
    """Записи, пора публиковать: отложенные с наступившим сроком."""
    return [e for e in gdata["published"]
            if e.get("status") == "scheduled" and float(e.get("publish_at", 0)) <= now]

async def news_send(guild: discord.Guild, entry: dict) -> str:
    """Публикация в настроенные каналы; возвращает '' или текст предупреждения."""
    cfg = news_guild(guild.id)["cfg"]
    chans = [c for c in (guild.get_channel(int(x)) for x in cfg.get("channels", [])) if c is not None]
    if not chans:
        return "не настроены каналы новостей (/новости каналы или вкладка «Новости»)"
    role = guild.get_role(int(cfg["ping_role"])) if cfg.get("ping_role") else None
    mentions = discord.AllowedMentions(roles=[role] if role else [], users=False, everyone=False)
    warn = ""
    entry.setdefault("msgs", {})
    first = True
    for ch in chans:
        prefix = f"{role.mention} " if role and first else ""
        try:
            msg = await ch.send(prefix, embed=news_build_embed(entry), allowed_mentions=mentions)
            entry["msgs"][str(ch.id)] = msg.id
        except discord.HTTPException as exc:
            warn += f"#{ch.name}: отказ ({exc}); "
            log.warning("Новости: публикация в #%s не удалась: %s", ch.name, exc)
        first = False
    entry["status"] = "sent"
    entry["ts"] = int(time.time())
    save_news()
    audit(guild.id, _sys_actor(), "новости", f"опубликовано #{entry['id']} в {len(entry['msgs'])} кан.")
    return warn

async def process_news():
    """Тикер: отложенные публикации по сроку."""
    now = time.time()
    for guild in bot.guilds:
        gdata = news_guild(guild.id)
        for entry in news_due(gdata, now):
            warn = await news_send(guild, entry)
            if warn:
                log.warning("Новости: %s опубликована с предупреждениями: %s", entry.get("id"), warn)

async def news_edit_published(guild: discord.Guild, entry: dict, text: str, title: str = None, image: str = None) -> int:
    """Правка на месте: сколько сообщений обновлено."""
    if text is not None:
        entry["text"] = text
    if title is not None:
        entry["title"] = title
    if image is not None:
        entry["image"] = image
    done = 0
    for ch_id, msg_id in list(entry.get("msgs", {}).items()):
        ch = guild.get_channel(int(ch_id))
        if ch is None:
            continue
        try:
            msg = await ch.fetch_message(int(msg_id))
            await msg.edit(embed=news_build_embed(entry))
            done += 1
        except discord.HTTPException as exc:
            log.warning("Новости: правка сообщения %s в #%s не удалась: %s", msg_id, ch.name, exc)
    save_news()
    return done

# ------------------------------ /новости ----------------------------------- #
g_news = app_commands.Group(name="новости", description="Новости сервера: черновики, публикация сразу или по таймеру")

@g_news.command(name="черновик", description="Создать черновик новости")
@app_commands.check(is_bot_admin)
async def news_draft(interaction: discord.Interaction, title: str, text: str, image: str = ""):
    err = news_validate(title, text, image)
    if err:
        await interaction.response.send_message(f"❌ Черновик не создан: {err}.", ephemeral=True)
        return
    g = news_guild(interaction.guild.id)
    nid = g["seq"]
    g["seq"] += 1
    g["drafts"][str(nid)] = {"id": nid, "title": title.strip(), "text": text.strip(),
                             "image": image.strip(), "created": int(time.time())}
    save_news()
    audit(interaction.guild.id, interaction.user.id, "новости", f"черновик #{nid}")
    await interaction.response.send_message(
        f"✅ Черновик №{nid} сохранён. Публикация: `/новости опубликовать id:{nid} [when:…]`.")

@g_news.command(name="править", description="Править черновик")
@app_commands.check(is_bot_admin)
async def news_edit(interaction: discord.Interaction, id: int, title: str = None,
                    text: str = None, image: str = None):
    d = news_guild(interaction.guild.id)["drafts"].get(str(id))
    if not d:
        await interaction.response.send_message(f"Черновика №{id} нет.", ephemeral=True)
        return
    err = news_validate(title or d["title"], text or d["text"], image if image is not None else d["image"])
    if err:
        await interaction.response.send_message(f"❌ Правка отклонена: {err}.", ephemeral=True)
        return
    if title is not None:
        d["title"] = title.strip()
    if text is not None:
        d["text"] = text.strip()
    if image is not None:
        d["image"] = image.strip()
    save_news()
    await interaction.response.send_message(f"✅ Черновик №{id} обновлён.")

@g_news.command(name="список", description="Черновики новостей")
@app_commands.check(is_bot_admin)
async def news_list(interaction: discord.Interaction):
    drafts = sorted(news_guild(interaction.guild.id)["drafts"].values(), key=lambda d: d["id"])
    if not drafts:
        await interaction.response.send_message("Черновиков нет.")
        return
    await interaction.response.send_message("\n".join(
        f"№{d['id']} **{d['title'][:60]}** — {d['text'][:80]}…" for d in drafts))

@g_news.command(name="удалить", description="Удалить черновик")
@app_commands.check(is_bot_admin)
async def news_del(interaction: discord.Interaction, id: int):
    g = news_guild(interaction.guild.id)
    if g["drafts"].pop(str(id), None) is None:
        await interaction.response.send_message(f"Черновика №{id} нет.", ephemeral=True)
        return
    save_news()
    await interaction.response.send_message(f"🗑 Черновик №{id} удалён.")

@g_news.command(name="опубликовать", description="Опубликовать сразу или when:45 / when:2026-10-24 20:00")
@app_commands.check(is_bot_admin)
async def news_publish(interaction: discord.Interaction, id: int, when: str = ""):
    g = news_guild(interaction.guild.id)
    d = g["drafts"].get(str(id))
    if not d:
        await interaction.response.send_message(f"Черновика №{id} нет.", ephemeral=True)
        return
    if not news_guild(interaction.guild.id)["cfg"].get("channels"):
        await interaction.response.send_message(
            "❌ Публикация невозможна: не настроены каналы новостей. "
            "`/новости каналы добавить channel:#канал` или вкладка «Новости» на сайте. "
            "Черновик сохранён.", ephemeral=True)
        return
    off = board_utc_offset(get_config(interaction.guild.id))
    ts, err = news_parse_when(when, off)
    if err:
        await interaction.response.send_message(f"❌ {err}.", ephemeral=True)
        return
    entry = dict(d, msgs={}, status="scheduled" if ts else "sent", publish_at=ts or 0)
    g["published"].append(entry)
    del g["published"][:-60]
    g["drafts"].pop(str(id), None)
    save_news()
    if ts:
        audit(interaction.guild.id, interaction.user.id, "новости", f"#{id} отложена до <t:{ts}:F>")
        await interaction.response.send_message(f"⏳ Новость №{id} выйдет <t:{ts}:F> (<t:{ts}:R>).")
        return
    warn = await news_send(interaction.guild, entry)
    msg = f"✅ Новость опубликована в {len(entry.get('msgs', {}))} кан."
    if warn:
        msg += f" ⚠️ {warn}"
    await interaction.response.send_message(msg)

@g_news.command(name="отменить", description="Отменить отложенную публикацию")
@app_commands.check(is_bot_admin)
async def news_cancel(interaction: discord.Interaction, id: int):
    g = news_guild(interaction.guild.id)
    entry = next((e for e in g["published"] if e["id"] == id and e.get("status") == "scheduled"), None)
    if not entry:
        await interaction.response.send_message(f"Отложенной новости №{id} нет.", ephemeral=True)
        return
    g["published"].remove(entry)
    g["drafts"][str(id)] = {k: entry[k] for k in ("id", "title", "text", "image") if k in entry}
    g["drafts"][str(id)]["created"] = int(time.time())
    save_news()
    await interaction.response.send_message(f"↩️ Новость №{id} возвращена в черновики.")

@g_news.command(name="каналы", description="Каналы новостей: добавить/убрать/список")
@app_commands.check(is_bot_admin)
@app_commands.choices(action=[
    app_commands.Choice(name="добавить", value="add"),
    app_commands.Choice(name="убрать", value="remove"),
    app_commands.Choice(name="список", value="list"),
])
async def news_channels(interaction: discord.Interaction, action: str, channel: discord.TextChannel = None):
    cfg = news_guild(interaction.guild.id)["cfg"]
    chans = cfg.setdefault("channels", [])
    if action == "list":
        await interaction.response.send_message(
            "\n".join(f"<#{c}>" for c in chans) or "Каналы новостей не настроены.")
        return
    if channel is None:
        await interaction.response.send_message("Укажите канал.", ephemeral=True)
        return
    if action == "add":
        if channel.id in chans:
            await interaction.response.send_message("Канал уже в списке.", ephemeral=True)
            return
        chans.append(channel.id)
    else:
        if channel.id not in chans:
            await interaction.response.send_message("Канала нет в списке.", ephemeral=True)
            return
        chans.remove(channel.id)
    save_news()
    await interaction.response.send_message(f"✅ Каналы новостей: {len(chans)}.")

@g_news.command(name="роль", description="Роль-пинг при публикации (пусто — без пинга)")
@app_commands.check(is_bot_admin)
async def news_role(interaction: discord.Interaction, role: discord.Role = None):
    cfg = news_guild(interaction.guild.id)["cfg"]
    cfg["ping_role"] = role.id if role else None
    save_news()
    await interaction.response.send_message(
        f"✅ Пинг при новостях: {role.mention if role else 'выключен'}.")

@g_news.command(name="история", description="Последние опубликованные новости")
async def news_history(interaction: discord.Interaction):
    pub = [e for e in news_guild(interaction.guild.id)["published"] if e.get("status") == "sent"][-5:]
    if not pub:
        await interaction.response.send_message("Новостей ещё не было.")
        return
    await interaction.response.send_message("\n".join(
        f"№{e['id']} **{e['title'][:60]}** — <t:{e.get('ts', 0)}:D>" for e in reversed(pub)))

@g_news.command(name="правка", description="Править опубликованную новость на месте")
@app_commands.check(is_bot_admin)
async def news_edit_pub(interaction: discord.Interaction, id: int, text: str = None,
                        title: str = None, image: str = None):
    g = news_guild(interaction.guild.id)
    entry = next((e for e in g["published"] if e["id"] == id), None)
    if not entry:
        await interaction.response.send_message(f"Новости №{id} нет в истории.", ephemeral=True)
        return
    err = news_validate(title or entry["title"], text or entry["text"],
                        image if image is not None else entry.get("image", ""))
    if err:
        await interaction.response.send_message(f"❌ Правка отклонена: {err}.", ephemeral=True)
        return
    done = await news_edit_published(interaction.guild, entry, text, title, image)
    audit(interaction.guild.id, interaction.user.id, "новости", f"правка #{id}: {done} сообщ.")
    await interaction.response.send_message(f"✏️ Обновлено сообщений: {done}.")



# --------------------------------------------------------------------------- #
# Досье: конструктор анкет-деревьев с фрагментной сборкой шаблона (r26)
# --------------------------------------------------------------------------- #
# Блок = вопрос + ответы-кнопки. У ответа: фрагмент текста анкеты, следующий
# блок (дерево), флаги «канон»/«самописный» для корневого блока. Ядро — список
# блоков, обязательных для самописного пути. Шаблон собирается из фрагментов
# пройденного пути вокруг настраиваемых шапки/подвала и выдаётся в приватный
# канал анкеты (механика тикетов); канал остаётся открытым, роль проверяющих
# получает пинг о выдаче — дальше люди разбираются сами (решение пользователя).

DEFAULT_DOSS_HEADER = (
    "АНКЕТА ПЕРСОНАЖА — {user}\n"
    "Ниже — собранное ядро и ветви пути. Заполните разделы своим текстом.\n"
    "--------------------------------------------------\n"
)
DEFAULT_DOSS_FOOTER = (
    "\n--------------------------------------------------\n"
    "Опишите характер, внешность и предысторию персонажа своими словами.\n"
    "Готовую анкету опубликуйте в этом канале: проверяющие уже оповещены."
)
# r44: заводские правила анкет — отправляются игроку сразу после формирования
# анкеты (текст редактируется на сайте, вкладка «Досье»). Синхронно с файлом
# ПРАВИЛА_АНКЕТ.md (между маркерами «НАЧАЛО/КОНЕЦ ТЕКСТА ПРАВИЛ»).
DEFAULT_DOSS_RULES = (
    "ПРАВИЛА АНКЕТ «𝐈𝐦𝐩𝐞𝐫𝐢𝐮𝐦 𝐒𝐚𝐧𝐜𝐭𝐮𝐦»\n"
    "критерии отказа и запреты\n"
    "\n"
    "═════════════════════════\n"
    "\n"
    "КАК ВООБЩЕ РАССМАТРИВАЕТСЯ АНКЕТА\n"
    "• Игрок проходит блоки кнопками в приватном канале и получает шаблон.\n"
    "• Канал остаётся открытым; роль проверяющих получает пинг о выдаче шаблона.\n"
    "• Игрок заполняет шаблон и публикует его в том же канале.\n"
    "• Анкетолог выносит вердикт там же: принято / на доработку / отказ.\n"
    "• Каноничные герои дополнительно проходят автопроверку бота ЕЩЁ до шаблона:\n"
    "  нет роли допуска или ранга — бот откажет сразу, с причиной в канал.\n"
    "\n"
    "═════════════════════════\n"
    "\n"
    "КАКИЕ МОГУТ БЫТЬ КРИТЕРИИ ОТКАЗА?\n"
    "\n"
    "═════════════════════════\n"
    "АВТООТКАЗ (бот, до человека)\n"
    "• Каноничный герой без допуска: нет роли допуска и не достигнут ранг\n"
    "  (допуск настраивает администратум; без него путь канона закрыт)\n"
    "• Анкета подана не через бота (свой формат вместо шаблона)\n"
    "\n"
    "═════════════════════════\n"
    "АВТООТКАЗ (анкетолог, формальная проверка)\n"
    "• Отсутствует Ядро или вырезаны его строки (имя, возраст, внешность,\n"
    "  характер, мировоззрение, биография, снаряжение)\n"
    "• Несколько фракционных блоков в одной анкете: путь бота даёт ровно одну\n"
    "  фракцию; смешение фракций в биографии — тоже отказ\n"
    "• Пустые поля шаблона\n"
    "• Изменённые заголовки и пункты шаблона\n"
    "• Псайкер без обоснования: ответ «да» в финальном вопросе при пустом блоке\n"
    "  способностей или без объяснения дара в биографии\n"
    "\n"
    "═════════════════════════\n"
    "ОТПРАВКА НА ДОРАБОТКУ\n"
    "• Биография короче нужного объёма и/или не раскрывает персонажа\n"
    "• Цели размытые («стать сильнее»)\n"
    "• Присутствуют противоречия и расхождения\n"
    "• Снаряжение не по лору\n"
    "• Мировоззрение не соответствует фракции\n"
    "• Анахронизмы (события или явления одной эпохи применены в другой) в анкете\n"
    "\n"
    "═════════════════════════\n"
    "ОТКАЗ С ПОЯСНЕНИЕМ\n"
    "• Мэри Сью / неуязвимый персонаж\n"
    "• Копия канонического героя\n"
    "• Нарушение канонов вселенной\n"
    "• Персонаж ломает баланс RP\n"
    "• Пси-сила не по рейтингу (см. запреты ниже)\n"
    "\n"
    "Доработка: анкетолог перечисляет пункты в канале анкеты; игрок правит шаблон\n"
    "и публикует повторно в тот же канал. Отказ: причина обязательна, канал\n"
    "закрывается игроком или по команде /анкета закрыть.\n"
    "\n"
    "═════════════════════════\n"
    "\n"
    "ЧТО ЗАПРЕЩЕНО В АНКЕТЕ?\n"
    "Нарушение любого пункта = ОТКАЗ\n"
    "\n"
    "═════════════════════════\n"
    "ПСАЙКЕРЫ\n"
    "Запреты действуют и для фракции «Псайкер Империума», и для финального\n"
    "блока «Является ли персонаж псайкером?» у любой фракции.\n"
    "• Рейтинг сильнее Эпсилон (ε): допустимы только Дзета (ζ) и Эпсилон (ε);\n"
    "  Дельта (δ), Гамма (γ), Бета (β), Альфа (α) — лишь личным решением администратума\n"
    "• Владение 2+ дисциплинами (в поле «Дисциплины» — ровно одна)\n"
    "• Иммунитет к Варпу\n"
    "• Контроль без риска (в поле «Уровень контроля» обязан быть риск)\n"
    "\n"
    "═════════════════════════\n"
    "МЭРИ СЬЮ И ДИСБАЛАНС\n"
    "• Персонаж без слабостей и уязвимостей\n"
    "• Всегда побеждает, невозможно ранить\n"
    "• Знает и умеет всё\n"
    "• «Избранный», один в поле воин\n"
    "• Уникальное оружие / непробиваемая броня\n"
    "• Необоснованная сила\n"
    "• Способности, ломающие сюжет\n"
    "• Неограниченные ресурсы\n"
    "• Реликвии Примархов / технологии Тёмной Эры\n"
    "• Влияние на сюжет без одобрения администратума\n"
    "\n"
    "═════════════════════════\n"
    "ЛОРНЫЕ ГЕРОИ\n"
    "• Любые канонические герои в самописном пути — запрет\n"
    "\n"
    "Исключение: каноничный путь с допуском\n"
    "• Роль допуска, выданная администратумом, ИЛИ ранг прогрессии не ниже\n"
    "  установленного (допуск бот проверяет автоматически)\n"
    "• Ролевой стаж без нарушений\n"
    "• Одобрение администратума\n"
    "• Согласование с текущим сюжетом\n"
    "\n"
    "Без прогрессии и роли допуска — нет канонического героя: бот откажет путь\n"
    "канона на первом же шаге, с пояснением в канал.\n"
    "\n"
    "═════════════════════════\n"
    "\n"
    "ПАМЯТКА ПРОВЕРЯЮЩИМ\n"
    "• Автоотказы бота не пересматриваются: игрок идёт качать прогресс или\n"
    "  выбирать самописный путь.\n"
    "• Формальные автоотказы анкетолога не обсуждаются: шаблон либо целый, либо нет.\n"
    "• Доработка даётся неограниченное число раз, но канал анкеты не является\n"
    "  чатом: обсуждение вердикта — только по существу пунктов.\n"
    "• Спор по вердикту — жалоба по правилам сервера, а не спор в канале анкеты.\n"
    "\n"
    "Слава Богу-Машине.\n"
)

def doss_guild(guild_id: int) -> dict:
    g = DOSSIER.setdefault(str(guild_id), {})
    g.setdefault("cfg", {})
    g.setdefault("blocks", {})
    g.setdefault("arch", [])
    cfg = g["cfg"]
    for key, val in (("enabled", False), ("category", None), ("review_role", None),
                     ("canon_role", None), ("canon_level", 0), ("root_block", None),
                     ("core_blocks", []), ("header", DEFAULT_DOSS_HEADER),
                     ("footer", DEFAULT_DOSS_FOOTER), ("rules_send", True),
                     ("rules_text", DEFAULT_DOSS_RULES)):
        cfg.setdefault(key, val)
    return g

def doss_cfg(guild_id: int) -> dict:
    return doss_guild(guild_id)["cfg"]

def doss_blocks(guild_id: int) -> dict:
    return doss_guild(guild_id)["blocks"]

def doss_block(guild_id: int, block_id):
    return doss_blocks(guild_id).get(str(block_id))

def doss_new_block_id(guild_id: int) -> int:
    ids = [int(k) for k in doss_blocks(guild_id) if str(k).isdigit()]
    return (max(ids) + 1) if ids else 1

def doss_validate_block(guild_id: int, block: dict, ignore_id=None) -> str:
    """Единая валидация блока (сайт — единственный вход правки блоков)."""
    if not (block.get("title") or "").strip():
        return "у блока нет заголовка"
    if not (block.get("text") or "").strip():
        return "у блока нет текста вопроса"
    answers = block.get("answers") or []
    if not answers:
        return "у блока нет ни одного ответа"
    if len(answers) > 25:
        return "ответов больше 25 — не поместятся в кнопки"
    blocks = doss_blocks(guild_id)
    seen = set()
    for a in answers:
        lab = (a.get("label") or "").strip()
        if not lab:
            return "ответ без подписи"
        if len(lab) > 80:
            return f"подпись «{lab[:20]}…» длиннее 80 символов"
        if lab.lower() in seen:
            return f"дубликат подписи «{lab[:20]}»"
        seen.add(lab.lower())
        nxt = a.get("next")
        if nxt not in (None, "", "0", 0):
            if str(nxt) not in blocks or str(nxt) == str(ignore_id):
                return f"ответ «{lab[:20]}» ведёт в несуществующий блок {nxt}"
    after = block.get("after") or ""
    if after:
        bpart, _, apart = after.partition(":")
        tgt = blocks.get(bpart)
        if not tgt or not any(str(a2.get("id")) == apart for a2 in tgt.get("answers", [])):
            return f"зависимость «после {after}» указывает на несуществующий ответ"
    return ""

def doss_find_cycles(guild_id: int) -> list:
    """Блоки, участвующие в циклах переходов: обход с запоминанием стека."""
    blocks = doss_blocks(guild_id)
    bad = set()

    deps_of = {}
    for b in blocks.values():
        aft = str(b.get("after") or "")
        if aft:
            deps_of.setdefault(aft, []).append(str(b.get("id")))

    def walk(bid, stack):
        if bid in stack:
            bad.update(stack[stack.index(bid):])
            return
        blk = blocks.get(str(bid))
        if not blk:
            return
        for a in blk.get("answers", []):
            targets = list(deps_of.get(f"{bid}:{a.get('id')}", []))
            nxt = a.get("next")
            if nxt not in (None, "", "0", 0):
                targets.append(str(nxt))
            for t in targets:
                walk(t, stack + [str(bid)])

    for bid in blocks:
        walk(bid, [])
    return sorted(bad, key=lambda x: int(x) if str(x).isdigit() else 0)

def doss_canon_ok(member, cfg: dict):
    """Допуск к каноничным героям: роль ИЛИ ранг прогрессии (достаточно одного)."""
    role_id = cfg.get("canon_role")
    level = int(cfg.get("canon_level") or 0)
    if not role_id and not level:
        return True, ""
    if role_id and any(r.id == int(role_id) for r in member.roles):
        return True, ""
    if level:
        got = prog_entry(member.guild.id, member.id)["level"]
        if got >= level:
            return True, ""
        return False, (f"нужна роль допуска или ранг {level}+ (у вас {got})")
    return False, "нужна роль допуска (её выдаёт администратум)"

def doss_next_block(guild_id: int, block_id, answer: dict):
    """Базовая линия после ответа: явный next, иначе следующий блок ядра, иначе финиш."""
    nxt = answer.get("next")
    if nxt not in (None, "", "0", 0):
        return str(nxt)
    core = [str(b) for b in doss_cfg(guild_id).get("core_blocks", [])]
    cur = str(block_id)
    if cur in core:
        i = core.index(cur)
        if i + 1 < len(core):
            return core[i + 1]
    return None

def doss_dep_blocks(guild_id: int, trigger: str, path: list) -> list:
    """Блоки с зависимостью after=trigger, ещё не пройденные в этой анкете."""
    done = {str(b) for b, _a in path}
    out = []
    for b in doss_blocks(guild_id).values():
        if str(b.get("after") or "") == trigger and str(b.get("id")) not in done:
            out.append(b)
    return sorted(out, key=lambda b: int(b.get("id")) if str(b.get("id")).isdigit() else 0)

def doss_flow_next(guild_id: int, sess: dict, block: dict, answer: dict):
    """Маршрут с зависимостями: сперва триггерные блоки, потом базовая линия.

    Базовая линия ответа запоминается в стеке сессии: цепочка триггерных
    блоков после финиша возвращает анкету на основную линию (или к финишу).
    """
    bid, aid = str(block.get("id")), str(answer.get("id"))
    base = doss_next_block(guild_id, bid, answer)
    deps = doss_dep_blocks(guild_id, f"{bid}:{aid}", sess.get("path", []))
    if deps:
        sess.setdefault("stack", []).append(base)
        return str(deps[0]["id"])
    if base:
        return base
    stack = sess.setdefault("stack", [])
    while stack:
        cont = stack.pop()
        if cont:
            return cont
    return None

def doss_assemble(guild_id: int, display_name: str, path: list) -> str:
    """Шаблон анкеты: шапка + фрагменты пути по порядку + подвал."""
    cfg = doss_cfg(guild_id)
    blocks = doss_blocks(guild_id)
    parts = []
    for bid, aid in path:
        blk = blocks.get(str(bid)) or {}
        ans = next((a for a in blk.get("answers", []) if str(a.get("id")) == str(aid)), None)
        frag = (ans or {}).get("fragment") or ""
        if frag.strip():
            parts.append(frag.strip())
    head = (cfg.get("header") or DEFAULT_DOSS_HEADER).replace("{user}", display_name)
    foot = cfg.get("footer") or DEFAULT_DOSS_FOOTER
    return head + "\n\n".join(parts) + "\n" + foot

def doss_purge_dead(guild) -> int:
    """r38: сессии, чьи каналы удалены вручную, больше не учитываются."""
    gone = 0
    for chid, sess in list(DOSS_SESS.items()):
        if sess.get("gid") != guild.id:
            continue
        if guild.get_channel(int(chid)) is None:
            DOSS_SESS.pop(chid, None)
            gone += 1
    if gone:
        save_doss_sess()
    return gone

def doss_session(channel_id: int) -> dict:
    return DOSS_SESS.get(str(channel_id))

def doss_set_session(channel_id: int, data: dict):
    DOSS_SESS[str(channel_id)] = data
    save_doss_sess()

def doss_drop_session(channel_id: int):
    DOSS_SESS.pop(str(channel_id), None)
    save_doss_sess()

def doss_block_embed(guild_id: int, block: dict) -> discord.Embed:
    embed = discord.Embed(title=f"📜 {block['title']}", description=block.get("text", ""), color=GOLD_COLOR)
    core = [str(b) for b in doss_cfg(guild_id).get("core_blocks", [])]
    if str(block.get("id")) in core:
        embed.set_footer(text=f"{FOOTER_TEXT} • блок ядра: обязателен для самописных героев")
    else:
        embed.set_footer(text=FOOTER_TEXT)
    return embed

class DossierFlowView(discord.ui.View):
    """Кнопки ответов текущего блока; меню живёт 12 часов, дальше /анкета продолжить."""

    def __init__(self, guild_id: int, owner_id: int, block: dict):
        super().__init__(timeout=3600 * 12)
        self.gid = guild_id
        self.owner_id = owner_id
        self.block = block
        for i, a in enumerate(block.get("answers", [])[:25]):
            btn = discord.ui.Button(label=a.get("label", "?")[:80],
                                    style=discord.ButtonStyle.primary, row=i // 5)
            btn.callback = self._make_cb(a)
            self.add_item(btn)

    def _make_cb(self, answer: dict):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message(
                    "Эту анкету заполняет другой игрок.", ephemeral=True)
                return
            sess = doss_session(interaction.channel.id)
            if not sess or sess.get("status") != "flow":
                await interaction.response.send_message(
                    "Анкета уже завершена или сброшена. `/анкета продолжить` не нужен — создайте новую.",
                    ephemeral=True)
                return
            gid = interaction.guild.id
            cfg = doss_cfg(gid)
            # Каноничный путь: проверка допуска и немедленный финиш с пингом.
            if answer.get("canon"):
                ok, reason = doss_canon_ok(interaction.user, cfg)
                if not ok:
                    audit(gid, _sys_actor(), "досье", f"отказ канона: {interaction.user.id} ({reason})")
                    await interaction.response.send_message(
                        f"❌ Каноничный герой недоступен: {reason}. Выберите самописного героя "
                        "или обратитесь к администратуму.", ephemeral=False)
                    return
                sess["path"].append([str(self.block.get("id")), str(answer.get("id"))])
                sess["status"] = "done"
                sess["kind"] = "canon"
                doss_set_session(interaction.channel.id, sess)
                self.stop()
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(
                    embed=discord.Embed(title="⚜ Каноничный герой", color=GOLD_COLOR,
                                        description="Путь завершён: проверяющие оповещены, канал остаётся открытым."),
                    view=self)
                await doss_send_rules(interaction.channel, cfg, interaction.user)  # r44: правила сразу после финиша
                await doss_ping_review(interaction.channel, cfg,
                                       f"{interaction.user.mention} подаёт **каноничного героя**. Проверьте допуск и анкету.")
                await doss_archive(gid, interaction.user, interaction.channel, sess, "канон")
                return
            # Обычный ответ: пишем путь и идём дальше по дереву.
            sess["path"].append([str(self.block.get("id")), str(answer.get("id"))])
            nxt = doss_flow_next(gid, sess, self.block, answer)
            if nxt is None:
                sess["status"] = "done"
                sess["kind"] = "self"
                doss_set_session(interaction.channel.id, sess)
                self.stop()
                for item in self.children:
                    item.disabled = True
                await interaction.response.edit_message(
                    embed=discord.Embed(title="📜 Путь пройден", color=GOLD_COLOR,
                                        description="Собираю шаблон анкеты…"),
                    view=self)
                await doss_emit_template(interaction, sess)
                return
            sess["cur"] = nxt
            doss_set_session(interaction.channel.id, sess)
            nblock = doss_block(gid, nxt)
            view = DossierFlowView(gid, self.owner_id, nblock)
            await interaction.response.edit_message(embed=doss_block_embed(gid, nblock), view=view)
        return cb

def doss_rules_chunks(text: str, limit: int = 1800) -> list:
    """r44: нарезка правил анкет под лимит Discord 2000 знаков — по границам строк."""
    text = (text or "").strip()
    if not text:
        return []
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + (1 if cur else 0) > limit:
            if cur:
                out.append(cur)
            cur = line
            while len(cur) > limit:  # одна строка длиннее лимита — режем жёстко
                out.append(cur[:limit])
                cur = cur[limit:]
        else:
            cur = cur + "\n" + line if cur else line
    if cur:
        out.append(cur)
    return out

async def doss_send_rules(channel, cfg: dict, user=None) -> int:
    """r44: правила анкет в канал сразу после формирования анкеты (до пинга проверяющих)."""
    if not cfg.get("rules_send"):
        return 0
    chunks = doss_rules_chunks(cfg.get("rules_text") or "")
    if not chunks:
        return 0
    prefix = (f"{user.mention}, правила рассмотрения анкет — ознакомьтесь до заполнения:\n"
              if user is not None else "")
    sent = 0
    for i, part in enumerate(chunks):
        try:
            await channel.send((prefix + part)[:2000] if i == 0 else part[:2000])
            sent += 1
        except discord.HTTPException as exc:
            log.warning("Досье: правила не отправлены в #%s (часть %d): %s", channel.name, i + 1, exc)
            audit(channel.guild.id, _sys_actor(), "досье", f"правила не отправлены ({exc})")
            break
    return sent

async def doss_ping_review(channel, cfg: dict, text: str):
    """Пинг роли проверяющих с явным allowed_mentions (уроки партий №7/№9)."""
    role = None
    if cfg.get("review_role"):
        role = channel.guild.get_role(int(cfg["review_role"]))
    mentions = discord.AllowedMentions(roles=[role] if role else [], users=False, everyone=False)
    prefix = f"{role.mention} " if role else ""
    try:
        await channel.send(prefix + text, allowed_mentions=mentions)
    except discord.HTTPException as exc:
        log.warning("Досье: пинг проверяющих в #%s не отправлен: %s", channel.name, exc)
        audit(channel.guild.id, _sys_actor(), "досье", f"пинг проверяющих не отправлен ({exc})")

async def doss_emit_template(interaction: discord.Interaction, sess: dict):
    """Выдача шаблона: копируемый текст (файлом, если длинный) + пинг проверяющих."""
    gid = interaction.guild.id
    cfg = doss_cfg(gid)
    template = doss_assemble(gid, interaction.user.display_name, sess["path"])
    if len(template) <= 1800:
        await interaction.channel.send(
            content=f"{interaction.user.mention}, ваш шаблон анкеты:",
            embed=discord.Embed(title="📜 Шаблон анкеты", description=f"```\n{template}\n```",
                                color=GOLD_COLOR).set_footer(text=FOOTER_TEXT))
    else:
        await interaction.channel.send(
            content=f"{interaction.user.mention}, ваш шаблон анкеты (файлом: длиннее лимита сообщения):",
            file=discord.File(io.BytesIO(template.encode("utf-8")), filename="anketa_template.txt"),
            embed=discord.Embed(title="📜 Шаблон анкеты", color=GOLD_COLOR,
                                description=template[:1000] + "\n…(полный текст в файле)")
            .set_footer(text=FOOTER_TEXT))
    sess["template"] = template
    doss_set_session(interaction.channel.id, sess)
    await doss_send_rules(interaction.channel, cfg, interaction.user)  # r44: правила сразу после шаблона
    await doss_ping_review(interaction.channel, cfg,
                           f"шаблон анкеты выдан: {interaction.user.mention}. Канал остаётся открытым.")
    await doss_archive(gid, interaction.user, interaction.channel, sess, "самописный")

async def doss_archive(gid: int, user, channel, sess: dict, kind: str):
    g = doss_guild(gid)
    g["arch"].append({"uid": user.id, "chan": channel.id, "kind": kind,
                      "ts": int(time.time()), "path": sess["path"]})
    del g["arch"][:-50]
    save_dossier()

class DossierPanelView(discord.ui.View):
    """Персистентная кнопка «Создать анкету» (переживает Restart бота)."""

    def __init__(self):
        super().__init__(timeout=None)
        btn = discord.ui.Button(label="Создать анкету", style=discord.ButtonStyle.primary,
                                custom_id="doss:new")
        btn.callback = self.cb_new
        self.add_item(btn)

    async def cb_new(self, interaction: discord.Interaction):
        gid = interaction.guild.id
        cfg = doss_cfg(gid)
        if not cfg.get("enabled"):
            await interaction.response.send_message("Анкеты на этом сервере выключены.", ephemeral=True)
            return
        # r38: лимита активных анкет нет; мёртвые сессии удалённых каналов вычищаем
        purged = doss_purge_dead(interaction.guild)
        if purged:
            log.info("Досье: вычищено мёртвых сессий удалённых каналов: %d", purged)
        if not cfg.get("root_block") or not doss_block(gid, cfg["root_block"]):
            await interaction.response.send_message(
                "Администратум не настроил корневой блок анкет. Напомните им про вкладку «Досье».",
                ephemeral=True)
            return
        if doss_find_cycles(gid):
            await interaction.response.send_message(
                "В блоках анкеты найден цикл переходов — администратуму нужно исправить схему.",
                ephemeral=True)
            return
        cat = interaction.guild.get_channel(int(cfg["category"])) if cfg.get("category") else None
        over = {
            interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                          read_message_history=True, attach_files=True),
            interaction.guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True,
                                                              manage_channels=True, attach_files=True),
        }
        if cfg.get("review_role"):
            role = interaction.guild.get_role(int(cfg["review_role"]))
            if role:
                over[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True,
                                                         send_messages=True)
        try:
            chan = await interaction.guild.create_text_channel(
                f"anketa-{interaction.user.name}"[:90], category=cat, overwrites=over,
                reason="Анкета персонажа (досье)")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Боту не хватило прав создать приватный канал анкеты (нужно «Управление каналами»).",
                ephemeral=True)
            return
        doss_set_session(chan.id, {"gid": gid, "uid": interaction.user.id, "path": [],
                                   "stack": [], "cur": str(cfg["root_block"]), "status": "flow",
                                   "created": int(time.time())})
        audit(gid, interaction.user.id, "досье", f"открыта анкета #{chan.id}")
        root = doss_block(gid, cfg["root_block"])
        await interaction.response.send_message(
            f"✅ Канал анкеты создан: {chan.mention}. Приветствие и первый блок уже там.",
            ephemeral=True)
        await chan.send(embed=discord.Embed(
            title="🕯 Ритуал досье начат",
            description=f"{interaction.user.mention}, отвечайте на блоки кнопками. "
                        "Каноничный герой требует допуска; самописный пройдёт ядро обязательных блоков. "
                        "В конце бот соберёт шаблон анкеты из ваших ответов и пришлёт правила рассмотрения.",
            color=GOLD_COLOR).set_footer(text=FOOTER_TEXT))
        await chan.send(embed=doss_block_embed(gid, root), view=DossierFlowView(gid, interaction.user.id, root))


@bot.tree.command(name="диагностика", description="Состояние модулей и сборки бота одним экраном")
@app_commands.check(is_bot_admin)
async def diagnostics(interaction: discord.Interaction):
    gid = interaction.guild.id
    cfg = get_config(gid)
    day, week = board_cycle_ids(cfg)
    bstate = board_state(gid)
    b_count_d = len(bstate["c"]["d"].get(day, {}) or {})
    dg = doss_guild(gid)
    cycles = doss_find_cycles(gid)
    ng = news_guild(gid)
    lines = [
        f"сборка: {BOT_BUILD}",
        f"цитаты: {'вкл' if cfg['enabled'] else 'выкл'}, каналов {len(cfg['channels'])}",
        f"прогрессия: каналов {len(cfg['xp_channels'])} (ветки и форумы считаются по родителю)",
        f"рейтинг: {'вкл' if cfg.get('board_enabled') else 'выкл'}, цикл {day}, "
        f"участников за цикл: {b_count_d}, порог {cfg.get('board_min_messages')}, "
        f"канал анонса {'есть' if cfg.get('board_channel') else 'нет'}",
        f"новости: каналов {len(ng['cfg'].get('channels', []))}, черновиков {len(ng['drafts'])}, "
        f"в ленте {len([e for e in ng['published'] if e.get('status') == 'sent'])}",
        f"досье: {'вкл' if dg['cfg'].get('enabled') else 'выкл'}, блоков {len(dg['blocks'])}, "
        f"корень {'есть' if dg['cfg'].get('root_block') else 'НЕТ'}, "
        f"ядро {len(dg['cfg'].get('core_blocks', []))}, циклы схемы: {cycles or 'нет'}",
        f"дашборд: {'вкл' if DISCORD_CLIENT_SECRET else 'выкл (нет DISCORD_CLIENT_SECRET)'}",
        f"данные: {DATA_DIR} (запись: {'ок' if os.access(DATA_DIR, os.W_OK) else 'НЕТ'})",
    ]
    await interaction.response.send_message(
        embed=discord.Embed(title="🛠 Диагностика кузни", description="\n".join(lines),
                            color=GOLD_COLOR).set_footer(text=FOOTER_TEXT), ephemeral=True)

# ------------------------------ /анкета ------------------------------------ #
g_doss = app_commands.Group(name="анкета", description="Анкеты персонажей: приватный канал, блоки-кнопки, сборка шаблона")

@g_doss.command(name="панель", description="Опубликовать панель с кнопкой «Создать анкету»")
@app_commands.check(is_bot_admin)
async def doss_panel(interaction: discord.Interaction, channel: discord.TextChannel):
    cfg = doss_cfg(interaction.guild.id)
    if not cfg.get("enabled"):
        await interaction.response.send_message(
            "Сначала включите модуль: `/анкета включить`.", ephemeral=True)
        return
    await channel.send(embed=discord.Embed(
        title="📜 Анкеты персонажей",
        description="Нажмите «Создать анкету» — бот откроет приватный канал и проведёт вас "
                    "по блокам: каноничный герой или самописный, сторона, фракция, псайкер… "
                    "В конце вы получите собранный шаблон анкеты.",
        color=GOLD_COLOR).set_footer(text=FOOTER_TEXT), view=DossierPanelView())
    audit(interaction.guild.id, interaction.user.id, "досье", f"панель в #{channel.name}")
    await interaction.response.send_message(f"✅ Панель анкет опубликована в {channel.mention}.")

@g_doss.command(name="включить", description="Включить модуль анкет")
@app_commands.check(is_bot_admin)
async def doss_enable(interaction: discord.Interaction):
    cfg = doss_cfg(interaction.guild.id)
    cfg["enabled"] = True
    save_dossier()
    audit(interaction.guild.id, interaction.user.id, "досье", "включен")
    hint = "" if cfg.get("root_block") else " Настройте блоки во вкладке «Досье» на сайте и задайте корневой блок."
    await interaction.response.send_message(f"✅ Анкеты включены.{hint}")

@g_doss.command(name="выключить", description="Выключить модуль анкет")
@app_commands.check(is_bot_admin)
async def doss_disable(interaction: discord.Interaction):
    cfg = doss_cfg(interaction.guild.id)
    cfg["enabled"] = False
    save_dossier()
    audit(interaction.guild.id, interaction.user.id, "досье", "выключен")
    await interaction.response.send_message("⏸ Анкеты выключены: новые каналы не создаются.")

@g_doss.command(name="настройки", description="Категория каналов, роль проверяющих, допуск канона")
@app_commands.check(is_bot_admin)
async def doss_settings(interaction: discord.Interaction,
                        category: discord.CategoryChannel = None,
                        review_role: discord.Role = None,
                        canon_role: discord.Role = None,
                        canon_level: app_commands.Range[int, 0, 100] = None):
    cfg = doss_cfg(interaction.guild.id)
    if category is not None:
        cfg["category"] = category.id
    if review_role is not None:
        cfg["review_role"] = review_role.id
    if canon_role is not None:
        cfg["canon_role"] = canon_role.id
    if canon_level is not None:
        cfg["canon_level"] = canon_level
    save_dossier()
    audit(interaction.guild.id, interaction.user.id, "досье", "настройки обновлены")
    await interaction.response.send_message(
        f"✅ Настройки анкет: категория — {category.name if category else 'не задана'}, "
        f"проверяющие — {review_role.mention if review_role else 'не заданы'}, "
        f"допуск канона — роль {canon_role.mention if canon_role else '—'} или ранг {cfg['canon_level'] or '—'}."
    )

@g_doss.command(name="список", description="Активные анкеты сервера")
@app_commands.check(is_bot_admin)
async def doss_list(interaction: discord.Interaction):
    doss_purge_dead(interaction.guild)
    rows = []
    for chid, sess in DOSS_SESS.items():
        if sess.get("gid") != interaction.guild.id or sess.get("status") != "flow":
            continue
        rows.append(f"<#{chid}> — <@{sess.get('uid')}> • блоков пройдено: {len(sess.get('path', []))}")
    await interaction.response.send_message(
        "\n".join(rows) if rows else "Активных анкет нет.")

@g_doss.command(name="продолжить", description="Вернуть кнопки текущего блока в канал анкеты")
async def doss_resume_cmd(interaction: discord.Interaction):
    sess = doss_session(interaction.channel.id)
    if not sess or sess.get("uid") != interaction.user.id:
        await interaction.response.send_message(
            "Здесь нет вашей активной анкеты.", ephemeral=True)
        return
    if sess.get("status") != "flow":
        await interaction.response.send_message(
            "Эта анкета завершена: шаблон выдан выше. `/анкета шаблон` покажет его снова.", ephemeral=True)
        return
    blk = doss_block(interaction.guild.id, sess.get("cur"))
    if not blk:
        await interaction.response.send_message(
            "Текущий блок не найден в схеме — позовите администратум.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=doss_block_embed(interaction.guild.id, blk),
        view=DossierFlowView(interaction.guild.id, interaction.user.id, blk))

@g_doss.command(name="закрыть", description="Закрыть канал анкеты (владелец анкеты или админ)")
async def doss_close(interaction: discord.Interaction, channel: discord.TextChannel = None):
    chan = channel or interaction.channel
    sess = doss_session(chan.id)
    is_admin = is_bot_admin(interaction)
    if not sess or (sess.get("uid") != interaction.user.id and not is_admin):
        await interaction.response.send_message(
            "Это не канал вашей анкеты, и вы не администратор бота.", ephemeral=True)
        return
    gid = interaction.guild.id
    doss_drop_session(chan.id)
    audit(gid, interaction.user.id, "досье", f"закрыт канал #{chan.name}")
    try:
        await interaction.response.send_message("🕯 Канал анкеты закрыт. Слава Богу-Машине.")
    except discord.HTTPException:
        pass
    try:
        await chan.delete(reason="Анкета закрыта")
    except discord.HTTPException as exc:
        log.warning("Досье: канал #%s не удалён: %s", chan.name, exc)

@g_doss.command(name="шаблон", description="Показать мой последний собранный шаблон анкеты")
async def doss_my_template(interaction: discord.Interaction):
    g = doss_guild(interaction.guild.id)
    mine = [a for a in g["arch"] if a.get("uid") == interaction.user.id and a.get("kind") == "самописный"]
    sess = doss_session(interaction.channel.id)
    tmpl = (sess or {}).get("template")
    if not tmpl and mine:
        # шаблон храним в сессии; из архива пересобираем по пути
        if mine:
            tmpl = doss_assemble(interaction.guild.id, interaction.user.display_name, mine[-1]["path"])
    if not tmpl:
        await interaction.response.send_message("У вас ещё нет собранного шаблона анкеты.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=discord.Embed(title="📜 Ваш последний шаблон", description=f"```\n{tmpl[:3900]}\n```",
                            color=GOLD_COLOR).set_footer(text=FOOTER_TEXT))

@g_doss.command(name="правила", description="Правила анкет: текст и отправка игроку после формирования анкеты")
@app_commands.check(is_bot_admin)
async def doss_rules_cmd(interaction: discord.Interaction, send_rules: bool = None):
    """r44: заводской текст правил живёт в cfg['rules_text'], правится на сайте (вкладка «Досье»)."""
    gid = interaction.guild.id
    cfg = doss_cfg(gid)
    if send_rules is not None:
        cfg["rules_send"] = bool(send_rules)
        save_dossier()
        audit(gid, interaction.user.id, "досье",
              f"отправка правил после анкеты: {'вкл' if send_rules else 'выкл'}")
    text = (cfg.get("rules_text") or "").strip()
    chunks = doss_rules_chunks(text)
    status = (f"отправка после формирования анкеты: {'да' if cfg.get('rules_send') else 'нет'}; "
              f"текст {len(text)} знаков = {len(chunks)} сообщ. по лимитам Discord. "
              "Правится на сайте, вкладка «Досье» → «Правила анкет».")
    if not text:
        await interaction.response.send_message(
            f"📜 {status}\nТекст правил пуст — отправляться нечему.", ephemeral=True)
        return
    await interaction.response.send_message(
        content=f"📜 {status}\n\nПервое сообщение预览:\n{text[:900]}…",
        file=discord.File(io.BytesIO(text.encode("utf-8")), filename="anketa_rules.txt"),
        ephemeral=True)

@bot.event
async def on_raw_guild_channel_delete(payload):
    """r38: канал анкеты удалён вручную — сессия закрывается сразу, анкета не «висит»."""
    sid = str(payload.channel_id)
    if DOSS_SESS.get(sid):
        doss_drop_session(int(sid))
        audit(payload.guild_id, _sys_actor(), "досье",
              f"канал анкеты удалён вручную — сессия закрыта (#{sid})")

async def doss_resume_sweep():
    """После Restart: вернуть кнопки в живые, но брошенные каналы анкет."""
    for chid, sess in list(DOSS_SESS.items()):
        if sess.get("status") != "flow":
            continue
        guild = bot.get_guild(sess.get("gid", 0))
        chan = guild.get_channel(int(chid)) if guild else None
        if chan is None:
            doss_drop_session(int(chid))
            continue
        blk = doss_block(guild.id, sess.get("cur"))
        if not blk:
            continue
        try:
            await chan.send(
                content=f"<@{sess.get('uid')}>, бот перезапущен: анкета продолжается с текущего блока.",
                embed=doss_block_embed(guild.id, blk),
                view=DossierFlowView(guild.id, sess.get("uid", 0), blk))
        except discord.HTTPException as exc:
            log.warning("Досье: возобновление в #%s не удалось: %s", chan.name, exc)


# ------------------------------ /рейтинг ----------------------------------- #
g_board = app_commands.Group(name="рейтинг", description="Рейтинг активности: циклы день и неделя с наградой в дата-когах")

async def _board_embed(guild: discord.Guild, cfg: dict, period: str) -> discord.Embed:
    day, week = board_cycle_ids(cfg)
    cid = day if period == "d" else week
    rows = await board_top(guild, cfg, period, cid)
    bucket = board_state(guild.id)["c"][period].get(cid, {}) or {}
    accounted = sum(int((v + [0, 0])[1]) for v in bucket.values())
    rewards = cfg.get("board_rewards_daily" if period == "d" else "board_rewards_weekly", [])
    limit = int(cfg.get("board_show", 10))
    title = "⚙ Дневной рейтинг (текущий цикл)" if period == "d" else "⚙ Недельный рейтинг (текущий цикл)"
    desc = "\n".join(board_standings_lines(rows, limit, rewards)) or \
        f"Пока пусто: нужно минимум {int(cfg.get('board_min_messages', 0))} сообщ. за цикл в каналах прогрессии."
    embed = discord.Embed(title=title, description=desc, color=GOLD_COLOR)
    embed.add_field(name="Цикл", value=f"`{cid}`", inline=True)
    embed.add_field(name="Перекат", value=f"<t:{board_next_reset(cfg, period)}:R>", inline=True)
    embed.add_field(
        name="Учтено сообщений за цикл",
        value=f"{accounted} (порог участия {int(cfg.get('board_min_messages', 0))})", inline=False)
    if not cfg.get("xp_channels"):
        embed.add_field(
            name="Почему пусто",
            value="Не настроены каналы прогрессии: опыт и счётчики циклов начисляются только там "
                  "(`/уровни каналы добавить`, форумы и ветки считаются по родительскому каналу).",
            inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    return embed

@g_board.command(name="день", description="Текущая таблица дневного цикла")
async def board_day(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    if not cfg.get("board_enabled"):
        await interaction.response.send_message(
            "Рейтинг выключен. Админам: `/рейтинг включить`.", ephemeral=True)
        return
    await interaction.response.send_message(embed=await _board_embed(interaction.guild, cfg, "d"))

@g_board.command(name="неделя", description="Текущая таблица недельного цикла")
async def board_week(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    if not cfg.get("board_enabled"):
        await interaction.response.send_message(
            "Рейтинг выключен. Админам: `/рейтинг включить`.", ephemeral=True)
        return
    await interaction.response.send_message(embed=await _board_embed(interaction.guild, cfg, "w"))

@g_board.command(name="архив", description="Итоги закрытых циклов (последние три)")
@app_commands.choices(period=[
    app_commands.Choice(name="день", value="d"),
    app_commands.Choice(name="неделя", value="w"),
])
async def board_archive(interaction: discord.Interaction, period: str):
    hist = board_state(interaction.guild.id)["hist"][period][-3:]
    if not hist:
        await interaction.response.send_message("Архив пуст: циклы ещё не закрывались.")
        return
    blocks = []
    for h in reversed(hist):
        lines = []
        for i, (uid, xp, msg) in enumerate(h["rows"][:3], 1):
            lines.append(f"{['🥇', '🥈', '🥉'][i - 1]} <@{uid}> — {xp} XP • {msg} сообщ.")
        paid = h.get("paid") or []
        tail = f"\nНаграды: {len(paid)}" if paid else ""
        blocks.append(f"**{h['id']}** (<t:{h['ts']}:D>)\n" + "\n".join(lines) + tail)
    embed = discord.Embed(
        title="⚙ Архив рейтинга: " + ("день" if period == "d" else "неделя"),
        description="\n\n".join(blocks), color=GOLD_COLOR)
    embed.set_footer(text=FOOTER_TEXT)
    await interaction.response.send_message(embed=embed)

@g_board.command(name="включить", description="Включить рейтинг активности")
@app_commands.check(is_bot_admin)
async def board_enable(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    cfg["board_enabled"] = True
    save_configs()
    audit(interaction.guild.id, interaction.user.id, "board", "включен")
    hint = "" if cfg.get("board_channel") else " Добавьте канал анонса: `/рейтинг канал`."
    await interaction.response.send_message(f"✅ Рейтинг активности включён.{hint}")

@g_board.command(name="выключить", description="Выключить рейтинг активности")
@app_commands.check(is_bot_admin)
async def board_disable(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    cfg["board_enabled"] = False
    save_configs()
    audit(interaction.guild.id, interaction.user.id, "board", "выключен")
    await interaction.response.send_message("⏸ Рейтинг выключен: счётчики циклов не пополняются.")

@g_board.command(name="канал", description="Канал анонса итогов цикла (пусто — убрать)")
@app_commands.check(is_bot_admin)
async def board_channel_cmd(interaction: discord.Interaction, channel: discord.TextChannel = None):
    cfg = get_config(interaction.guild.id)
    cfg["board_channel"] = channel.id if channel else None
    save_configs()
    audit(interaction.guild.id, interaction.user.id, "board",
          f"канал анонса: {channel.name if channel else 'убран'}")
    await interaction.response.send_message(
        f"✅ Анонсы рейтинга: {channel.mention if channel else 'канал не задан'}."
    )

@g_board.command(name="награды", description="Награды за места: «300,200,100» (пусто — без наград)")
@app_commands.check(is_bot_admin)
@app_commands.choices(period=[
    app_commands.Choice(name="день", value="d"),
    app_commands.Choice(name="неделя", value="w"),
])
async def board_rewards(interaction: discord.Interaction, period: str, list: str):
    vals, err = board_parse_rewards(list)
    if err:
        await interaction.response.send_message(f"❌ Не разобрал список наград: {err}.", ephemeral=True)
        return
    cfg = get_config(interaction.guild.id)
    cfg["board_rewards_daily" if period == "d" else "board_rewards_weekly"] = vals
    save_configs()
    audit(interaction.guild.id, interaction.user.id, "board",
          f"награды {'день' if period == 'd' else 'неделя'}: {vals or 'выкл'}")
    await interaction.response.send_message(
        f"✅ Награды ({'день' if period == 'd' else 'неделя'}): "
        + (", ".join(f"{i}. {fmt_credits(v)}" for i, v in enumerate(vals, 1)) or "выключены")
    )

@g_board.command(name="порог", description="Минимум сообщений за цикл для участия в рейтинге")
@app_commands.check(is_bot_admin)
async def board_minmsg(interaction: discord.Interaction, value: app_commands.Range[int, 0, 1000]):
    cfg = get_config(interaction.guild.id)
    cfg["board_min_messages"] = value
    save_configs()
    audit(interaction.guild.id, interaction.user.id, "board", f"порог сообщений: {value}")
    await interaction.response.send_message(f"✅ Порог участия: {value} сообщ. за цикл.")

@g_board.command(name="показ", description="Сколько строк показывать в таблицах и анонсах (3–25)")
@app_commands.check(is_bot_admin)
async def board_show(interaction: discord.Interaction, value: app_commands.Range[int, 3, 25]):
    cfg = get_config(interaction.guild.id)
    cfg["board_show"] = value
    save_configs()
    await interaction.response.send_message(f"✅ В таблицах и анонсах: {value} строк.")

@g_board.command(name="пояс", description="Часовой пояс границ циклов: сдвиг от UTC в часах (-12..14)")
@app_commands.check(is_bot_admin)
async def board_tz(interaction: discord.Interaction, value: app_commands.Range[int, -12, 14]):
    cfg = get_config(interaction.guild.id)
    cfg["board_utc_offset"] = value
    save_configs()
    audit(interaction.guild.id, interaction.user.id, "board", f"пояс: UTC{value:+d}")
    await interaction.response.send_message(
        f"✅ Границы циклов считаются по UTC{value:+d}. Текущий перекат: "
        f"<t:{board_next_reset(cfg, 'd')}:T>."
    )

@g_board.command(name="исключения", description="Роли вне рейтинга (добавить/убрать)")
@app_commands.check(is_bot_admin)
@app_commands.choices(action=[
    app_commands.Choice(name="добавить", value="add"),
    app_commands.Choice(name="убрать", value="remove"),
])
async def board_exclude(interaction: discord.Interaction, action: str, role: discord.Role):
    cfg = get_config(interaction.guild.id)
    roles = cfg.setdefault("board_exclude_roles", [])
    if action == "add":
        if role.id in roles:
            await interaction.response.send_message(f"{role.mention} уже вне рейтинга.", ephemeral=True)
            return
        roles.append(role.id)
        msg = f"✅ {role.mention} вне рейтинга."
    else:
        if role.id not in roles:
            await interaction.response.send_message(f"{role.mention} не значится в исключениях.", ephemeral=True)
            return
        roles.remove(role.id)
        msg = f"✅ {role.mention} снова участвует в рейтинге."
    save_configs()
    audit(interaction.guild.id, interaction.user.id, "board",
          f"исключение {action}: роль {role.id}")
    await interaction.response.send_message(msg)


# ------------------------------- /shop ------------------------------------- #
def shop_items(guild_id: int):
    return SHOP.setdefault(str(guild_id), [])


@g_shop.command(name="список", description="Показать товары магазина")
async def shop_view(interaction: discord.Interaction):
    view = ShopView(interaction.guild.id, interaction.user.id)
    view.rebuild()
    await interaction.response.send_message(embed=view.build_embed(), view=view)
    await view.bind(interaction)
@g_shop.command(name="купить", description="Купить товар за валюту")
async def shop_buy(interaction: discord.Interaction, item: int):
    items = shop_items(interaction.guild.id)
    target = next((it for it in items if it["id"] == item), None)
    if target is None:
        await interaction.response.send_message(f"Товар №{item} не найден в лавкае.")
        return
    await perform_purchase(interaction, target)
@g_shop.command(name="добавить", description="Добавить товар в магазин")
@app_commands.check(is_bot_admin)
async def shop_add(
    interaction: discord.Interaction,
    name: app_commands.Range[str, 2, 80],
    price: app_commands.Range[int, 1, 10_000_000],
    description: Optional[str] = None,
):
    items = shop_items(interaction.guild.id)
    new_id = max([it["id"] for it in items], default=0) + 1
    items.append({"id": new_id, "name": name, "price": price, "description": description or ""})
    save_shop()
    await interaction.response.send_message(
        f"➕ Товар №{new_id} «{name}» за {fmt_credits(price)} {CREDITS_NAME} добавлен в лавка."
    )


@g_shop.command(name="убрать", description="Убрать товар из магазина")
@app_commands.check(is_bot_admin)
async def shop_remove(interaction: discord.Interaction, item: int):
    items = shop_items(interaction.guild.id)
    for it in list(items):
        if it["id"] == item:
            items.remove(it)
            save_shop()
            await interaction.response.send_message(f"🗑 Товар №{item} удалён из лавки.")
            return
    await interaction.response.send_message(f"Товар №{item} не найден.")


@g_shop.command(name="уведомления", description="Настроить канал и роль для уведомлений о покупках")
@app_commands.check(is_bot_admin)
async def shop_notify(interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
    cfg = get_config(interaction.guild.id)
    cfg["shop_notify_channel"] = channel.id
    cfg["shop_notify_role"] = role.id
    save_configs()
    await interaction.response.send_message(
        f"✅ Уведомления о покупках: канал {channel.mention}, роль {role.mention}."
    )


# ------------------------------- /приветствие -------------------------------- #
@g_welcome.command(name="включить", description="Включить приветствие новых участников")
@app_commands.check(is_bot_admin)
async def welcome_enable(interaction: discord.Interaction, channel: discord.TextChannel):
    cfg = get_config(interaction.guild.id)
    cfg["welcome_enabled"] = True
    cfg["welcome_channel"] = channel.id
    save_configs()
    await interaction.response.send_message(
        f"✅ Посвящения включены в {channel.mention}. Текст: `/приветствие текст`."
    )


@g_welcome.command(name="выключить", description="Выключить приветствие новых участников")
@app_commands.check(is_bot_admin)
async def welcome_disable(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    cfg["welcome_enabled"] = False
    save_configs()
    await interaction.response.send_message("⛔ Посвящения выключены.")


@g_welcome.command(name="текст", description="Задать текст приветствия (плейсхолдеры: {mention} {user} {server} {count})")
@app_commands.check(is_bot_admin)
async def welcome_text(interaction: discord.Interaction, text: app_commands.Range[str, 10, 1500]):
    cfg = get_config(interaction.guild.id)
    cfg["welcome_text"] = text
    save_configs()
    await interaction.response.send_message("✅ Слово посвящения обновлено.")


@g_welcome.command(name="тест", description="Показать, как будет выглядеть приветствие")
@app_commands.check(is_bot_admin)
async def welcome_preview(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    text = cfg["welcome_text"].format(
        mention=interaction.user.mention,
        user=str(interaction.user.display_name),
        server=interaction.guild.name,
        count=interaction.guild.member_count,
    )
    await interaction.response.send_message("👋 Пример посвящения:\n" + text)


# ------------------------------- /tarot ------------------------------------ #
def tarot_embed(display_name: str, count: int) -> discord.Embed:
    drawn = random.sample(TAROT_DECK, count)
    good = sum(1 for _, o, _ in drawn if o == "good")
    bad = sum(1 for _, o, _ in drawn if o == "bad")
    mixed = count - good - bad
    embed = discord.Embed(
        title=f"⚙ Аугур Бога-Машины — расклад для {display_name}",
        description="Знаки вытянуты из книги Бога-Машины. Читайте их, но не забывайте о бдительности.",
        color=EMBED_COLOR,
    )
    for name, omen, meaning in drawn:
        embed.add_field(name=f"{OMEN_LABEL[omen]} — {name}", value=meaning, inline=False)
    embed.add_field(name="⚖️ Предсказание", value=tarot_verdict(good, bad, mixed), inline=False)
    embed.set_footer(text=FOOTER_TEXT + " • голос Аугура развлекателен")
    return embed

TAROT_LAST: dict[int, float] = {}

@bot.tree.command(name="таро", description="Расклад Аугура (22 карты) с предсказанием")
@app_commands.checks.cooldown(1, 15)
async def cmd_tarot(interaction: discord.Interaction, cards: Optional[app_commands.Range[int, 1, 5]] = None):
    count = cards or 3
    TAROT_LAST[interaction.user.id] = time.time()
    await interaction.response.send_message(
        embed=tarot_embed(interaction.user.display_name, count), view=TarotAgainView(count)
    )
# --------------------------------------------------------------------------- #
# Интерактивные меню (кнопки)
# --------------------------------------------------------------------------- #
class MenuMixin:
    per_page = 4
    host_msg = None  # сообщение, к которому прикреплено меню (для уборки кнопок)

    async def bind(self, interaction: discord.Interaction):
        """Запомнить сообщение меню: у discord.ui.View нет атрибута message во всех версиях."""
        try:
            self.host_msg = await interaction.original_response()
        except discord.HTTPException:
            self.host_msg = None
        return self

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Это меню открыто другим игроком.")
            return False
        return True

    def _nav_buttons(self, pages: int, row: int = 4):
        if pages <= 1:
            return
        prev_b = discord.ui.Button(label="◀ Назад", style=discord.ButtonStyle.secondary, row=row)
        next_b = discord.ui.Button(label="Вперёд ▶", style=discord.ButtonStyle.secondary, row=row)
        prev_b.callback = self._make_page(-1)
        next_b.callback = self._make_page(1)
        self.add_item(prev_b)
        self.add_item(next_b)

    def _make_page(self, step: int):
        async def cb(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.page = max(0, self.page + step)
            self.rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        return cb

    def _close_button(self, row: int):
        close = discord.ui.Button(label="Закрыть", style=discord.ButtonStyle.grey, row=row)
        close.callback = self._on_close
        self.add_item(close)

    async def _on_close(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.edit_message(view=None)

    async def on_timeout(self):
        try:
            if self.host_msg is not None:
                await self.host_msg.edit(view=None)
        except discord.HTTPException:
            pass

class InventoryView(MenuMixin, discord.ui.View):
    """Меню кладезя: страницы -> выбор предмета -> «Применить дар» / «Назад»."""

    def __init__(self, guild_id: int, user_id: int, owner_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.owner_id = owner_id
        self.page = 0
        self.selected = None

    def entries(self) -> list:
        return inv_entries(self.guild_id, self.user_id)

    def build_embed(self) -> discord.Embed:
        entries = self.entries()
        embed = discord.Embed(title="⚙ Кладезь", color=EMBED_COLOR)
        if self.selected is None:
            if not entries:
                embed.description = (
                    "Кладезь пуст. Покупайте в лавке, вскрывайте тех-арки кузни "
                    "и участвуйте в жребиях — предметы появятся здесь."
                )
                embed.set_footer(text=FOOTER_TEXT)
                return embed
            pages = max(1, -(-len(entries) // self.per_page))
            self.page = min(self.page, pages - 1)
            chunk = entries[self.page * self.per_page:(self.page + 1) * self.per_page]
            embed.description = "\n".join(
                f"**{it['iid']}.** {it['name']} — *{SOURCE_LABELS.get(it.get('source'), '—')}*"
                for it in chunk
            )
            embed.set_footer(text=f"Стр. {self.page + 1}/{pages} • предметов: {len(entries)} • {FOOTER_TEXT}")
        else:
            it = self.selected
            embed.title = f"⚙ Дар: {it['name']}"
            embed.description = it.get("description") or "Описание отсутствует."
            embed.add_field(name="Источник", value=SOURCE_LABELS.get(it.get("source"), "—"), inline=True)
            embed.add_field(name="№ предмета", value=str(it.get("iid")), inline=True)
            embed.set_footer(text="«Применить дар» израсходует его и оповестит совет магосов. • " + FOOTER_TEXT)
        return embed

    def rebuild(self):
        self.clear_items()
        entries = self.entries()
        if self.selected is None:
            pages = max(1, -(-len(entries) // self.per_page))
            self.page = min(self.page, pages - 1)
            chunk = entries[self.page * self.per_page:(self.page + 1) * self.per_page]
            for row, it in enumerate(chunk):
                b = discord.ui.Button(label=it["name"][:60], style=discord.ButtonStyle.secondary, row=row)
                b.callback = self._make_select(it)
                self.add_item(b)
            self._nav_buttons(pages)
            self._close_button(4)
        else:
            use_b = discord.ui.Button(label="Применить дар", style=discord.ButtonStyle.primary, row=0)
            use_b.callback = self._on_use
            back_b = discord.ui.Button(label="Назад", style=discord.ButtonStyle.secondary, row=0)
            back_b.callback = self._on_back
            self.add_item(use_b)
            self.add_item(back_b)
            self._close_button(1)

    def _make_select(self, it: dict):
        async def cb(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            self.selected = it
            self.rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        return cb

    async def _on_back(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        self.selected = None
        self.rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_use(self, interaction: discord.Interaction):
        if not await self._guard(interaction):
            return
        it = inv_use(self.guild_id, self.user_id, self.selected["iid"])
        self.selected = None
        self.rebuild()
        if it is None:
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send("Предмет уже был использован.")
            return
        # Отвечаем на взаимодействие МГНОВЕННО (Discord ждёт ~3 секунды):
        # уведомление роли лавки — медленная сетевая работа, идёт после ack.
        await interaction.response.edit_message(embed=self.build_embed(), view=self)
        embed = discord.Embed(
            title="⚙ Дар востребован",
            description=(
                f"{interaction.user.mention} использует предмет «**{it['name']}**».\n"
                f"Администратум, выдайте реквизицию владельцу."
            ),
            color=GOLD_COLOR,
        )
        embed.set_footer(text=FOOTER_TEXT)
        sent = await notify_admin_channel(
            interaction, embed, f"Игрок {interaction.user.mention} использовал предмет «{it['name']}»."
        )
        if sent:
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send(
                "⚠️ Канал или роль для уведомлений лавки не настроены (`/магазин уведомления`): "
                "предмет израсходован, но пинг не отправлен."
            )

class ShopView(MenuMixin, discord.ui.View):
    """Витрина лавки с кнопками покупки и листанием страниц."""

    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.user_id = user_id
        self.owner_id = user_id
        self.page = 0

    def items(self) -> list:
        return SHOP.setdefault(str(self.guild_id), [])

    def build_embed(self) -> discord.Embed:
        items = self.items()
        entry = prog_entry(self.guild_id, self.user_id)
        embed = discord.Embed(title="⚙ Лавка даров", color=GOLD_COLOR)
        if not items:
            embed.description = "Лавка пуста: администрация ещё не добавила дары командой `/магазин добавить`."
        else:
            pages = max(1, -(-len(items) // self.per_page))
            self.page = min(self.page, pages - 1)
            chunk = items[self.page * self.per_page:(self.page + 1) * self.per_page]
            embed.description = "\n\n".join(
                f"**№{it['id']}** {it['name']} — **{fmt_credits(it['price'])}** {CREDITS_NAME}"
                + (f"\n└ {it['description']}" if it.get("description") else "")
                for it in chunk
            )
            embed.set_footer(text=f"Стр. {self.page + 1}/{pages} • {FOOTER_TEXT}")
        embed.add_field(name="Твои дата-коги", value=f"{fmt_credits(entry['credits'])} {CREDITS_NAME}", inline=False)
        return embed

    def rebuild(self):
        self.clear_items()
        items = self.items()
        pages = max(1, -(-len(items) // self.per_page))
        for row, it in enumerate(items[self.page * self.per_page:(self.page + 1) * self.per_page]):
            b = discord.ui.Button(label=f"Купить: {it['name']}"[:60], style=discord.ButtonStyle.primary, row=row)
            b.callback = self._make_buy(it)
            self.add_item(b)
        self._nav_buttons(pages)
        self._close_button(4)

    def _make_buy(self, it: dict):
        async def cb(interaction: discord.Interaction):
            if not await self._guard(interaction):
                return
            await perform_purchase(interaction, it)
            self.rebuild()
            try:
                if self.host_msg is not None:
                    await self.host_msg.edit(embed=self.build_embed(), view=self)
            except discord.HTTPException:
                pass
        return cb

class ModerationView(discord.ui.View):
    """Очередь цитат на модерации с кнопками «одобрить / отклонить»."""

    per_page = 2

    def __init__(self, guild_id: int, owner_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.page = 0

    def pending(self) -> list:
        return [q for q in CUSTOM.get(str(self.guild_id), []) if q.get("status") == "pending"]

    def build_embed(self) -> discord.Embed:
        entries = self.pending()
        embed = discord.Embed(title="📋 Литании на модерации", color=EMBED_COLOR)
        if not entries:
            embed.description = "Очередь модерации пуста."
            embed.set_footer(text=FOOTER_TEXT)
            return embed
        pages = max(1, -(-len(entries) // self.per_page))
        self.page = min(self.page, pages - 1)
        chunk = entries[self.page * self.per_page:(self.page + 1) * self.per_page]
        embed.description = "\n\n".join(
            f"**№{q['id']}** «{q['text'][:120]}»\n— {q['author']} • <@{q.get('added_by', 0)}>"
            for q in chunk
        )
        embed.set_footer(text=f"Стр. {self.page + 1}/{pages} • в очереди: {len(entries)} • {FOOTER_TEXT}")
        return embed

    def rebuild(self):
        self.clear_items()
        entries = self.pending()
        pages = max(1, -(-len(entries) // self.per_page))
        for row, q in enumerate(entries[self.page * self.per_page:(self.page + 1) * self.per_page]):
            ok_b = discord.ui.Button(label=f"⚙ Одобрить №{q['id']}", style=discord.ButtonStyle.success, row=row)
            ok_b.callback = self._make_act(q, True)
            no_b = discord.ui.Button(label=f"🔥 Предать огню №{q['id']}", style=discord.ButtonStyle.danger, row=row)
            no_b.callback = self._make_act(q, False)
            self.add_item(ok_b)
            self.add_item(no_b)
        if pages > 1:
            prev_b = discord.ui.Button(label="◀ Назад", style=discord.ButtonStyle.secondary, row=4)
            next_b = discord.ui.Button(label="Вперёд ▶", style=discord.ButtonStyle.secondary, row=4)
            prev_b.callback = self._make_page(-1)
            next_b.callback = self._make_page(1)
            self.add_item(prev_b)
            self.add_item(next_b)
        close = discord.ui.Button(label="Закрыть", style=discord.ButtonStyle.grey, row=4)
        close.callback = self._on_close
        self.add_item(close)

    def _make_page(self, step: int):
        async def cb(interaction: discord.Interaction):
            if not is_bot_admin(interaction):
                await interaction.response.send_message("Недостаточно прав.")
                return
            self.page = max(0, self.page + step)
            self.rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        return cb

    async def _on_close(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=None)

    def _make_act(self, q: dict, approve: bool):
        async def cb(interaction: discord.Interaction):
            if not is_bot_admin(interaction):
                await interaction.response.send_message("Недостаточно прав.")
                return
            entries = CUSTOM.get(str(self.guild_id), [])
            target = next((x for x in entries if x["id"] == q["id"] and x.get("status") == "pending"), None)
            if target is None:
                self.rebuild()
                await interaction.response.edit_message(embed=self.build_embed(), view=self)
                await interaction.followup.send(f"Литания №{q['id']} уже обработана.")
                return
            if approve:
                target["status"] = "approved"
                save_custom()
                msg = f"✅ Литания №{q['id']} одобрена и в ротации."
            else:
                entries.remove(target)
                save_custom()
                msg = f"🗑 Литания №{q['id']} отклонена и удалена."
            self.rebuild()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
            await interaction.followup.send(msg)
        return cb

class TarotAgainView(discord.ui.View):
    def __init__(self, count: int):
        super().__init__(timeout=60)
        self.count = count
        b = discord.ui.Button(label="⚙ Новый расклад", style=discord.ButtonStyle.secondary, row=0)
        b.callback = self.on_again
        self.add_item(b)

    async def on_again(self, interaction: discord.Interaction):
        last = TAROT_LAST.get(interaction.user.id, 0)
        waited = time.time() - last
        if waited < 15:
            await interaction.response.send_message(
                f"⏳ Повторить можно через {int(15 - waited) + 1} с."
            )
            return
        TAROT_LAST[interaction.user.id] = time.time()
        await interaction.response.edit_message(
            embed=tarot_embed(interaction.user.display_name, self.count), view=TarotAgainView(self.count)
        )

class HelpView(discord.ui.View):
    def __init__(self, owner_id: int):
        super().__init__(timeout=300)
        self.owner_id = owner_id
        self.page = 0
        self.host_msg = None
        prev_b = discord.ui.Button(label="◀ Назад", style=discord.ButtonStyle.secondary, row=0)
        next_b = discord.ui.Button(label="Вперёд ▶", style=discord.ButtonStyle.secondary, row=0)
        prev_b.callback = self._make_page(-1)
        next_b.callback = self._make_page(1)
        self.add_item(prev_b)
        self.add_item(next_b)

    async def bind(self, interaction: discord.Interaction):
        try:
            self.host_msg = await interaction.original_response()
        except discord.HTTPException:
            self.host_msg = None
        return self

    def build_embed(self) -> discord.Embed:
        title, text = HELP_PAGES[self.page]
        embed = discord.Embed(title=f"⚙ Adeptus Mechanicus — помощь: {title}", description=text, color=EMBED_COLOR)
        embed.set_footer(text=f"Стр. {self.page + 1}/{len(HELP_PAGES)} • {FOOTER_TEXT}")
        return embed

    def _make_page(self, step: int):
        async def cb(interaction: discord.Interaction):
            if interaction.user.id != self.owner_id:
                await interaction.response.send_message("Это меню открыто другим игроком.")
                return
            self.page = (self.page + step) % len(HELP_PAGES)
            await interaction.response.edit_message(embed=self.build_embed(), view=self)
        return cb

    async def on_timeout(self):
        try:
            if self.host_msg is not None:
                await self.host_msg.edit(view=None)
        except discord.HTTPException:
            pass

class GiveawayView(discord.ui.View):
    """Персистентные кнопки жребия (переживают перезапуск бота)."""

    def __init__(self, guild_id: int, gw_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.gw_id = gw_id
        b = discord.ui.Button(
            label="⚙ Испросить жребий",
            style=discord.ButtonStyle.primary,
            custom_id=f"gv_join:{guild_id}:{gw_id}",
        )
        b.callback = self.on_join
        self.add_item(b)

    def gw(self):
        for g in guild_giveaways(self.guild_id):
            if g.get("id") == self.gw_id:
                return g
        return None

    async def on_join(self, interaction: discord.Interaction):
        gw = self.gw()
        if gw is None or gw.get("status") != "active":
            await interaction.response.send_message("Жребий уже завершён или отменён.")
            return
        uid = interaction.user.id
        if uid in gw.get("participants", []):
            await interaction.response.send_message("Вы уже участвуете в этом жребии.")
            return
        gw.setdefault("participants", []).append(uid)
        save_giveaways()
        await interaction.response.send_message(
            f"✅ Ты в жребии «{gw['title']}»! Бог-Машина огласит избранных <t:{int(gw['ends_at'])}:R>."
        )
        try:
            if interaction.message is not None:
                await interaction.message.edit(embed=giveaway_embed(gw), view=self)
        except discord.HTTPException:
            pass

class LootConfirmView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int, box_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.owner_id = user_id
        self.box_id = box_id
        open_b = discord.ui.Button(label="⚙ Вскрыть тех-арк", style=discord.ButtonStyle.primary, row=0)
        open_b.callback = self.on_open
        cancel_b = discord.ui.Button(label="Отмена", style=discord.ButtonStyle.grey, row=0)
        cancel_b.callback = self.on_cancel
        self.add_item(open_b)
        self.add_item(cancel_b)

    def box(self):
        return next((b for b in loot_boxes(self.guild_id) if b["id"] == self.box_id), None)

    async def on_cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Это меню открыто другим игроком.")
            return
        await interaction.response.edit_message(view=None)

    async def on_open(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Это меню открыто другим игроком.")
            return
        box = self.box()
        if box is None or not box.get("items"):
            await interaction.response.edit_message(view=None)
            await interaction.followup.send("Тех-арк недоступен или пуст.")
            return
        entry = prog_entry(self.guild_id, self.user_id)
        if entry["credits"] < box["price"]:
            await interaction.response.edit_message(view=None)
            await interaction.followup.send(
                f"⛔ Недостаточно {CREDITS_NAME}: нужно {fmt_credits(box['price'])}, "
                f"у вас {fmt_credits(entry['credits'])}."
            )
            return
        entry["credits"] = round(entry["credits"] - box["price"], 2)
        save_progress()
        item = roll_loot_item(box)
        iid = inv_add(self.guild_id, self.user_id, item["name"], item.get("description", ""), "loot")
        embed = discord.Embed(title="⚙ Тех-арк открыт", color=GOLD_COLOR)
        embed.description = (
            f"{interaction.user.mention} вскрывает тех-арк «**{box['name']}**»…\n\n"
            f"🎁 Выпало: **{item['name']}** (шанс {chance_percent(box, item):.1f}%)\n"
            f"{item.get('description') or ''}\n\nПредмет №{iid} добавлен в кладезь."
        )
        embed.set_footer(text=FOOTER_TEXT)
        await interaction.response.edit_message(
            embed=embed, view=LootResultView(self.guild_id, self.user_id, self.box_id)
        )

class LootResultView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int, box_id: int):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.owner_id = user_id
        self.box_id = box_id
        inv_b = discord.ui.Button(label="🎒 Открыть кладезь", style=discord.ButtonStyle.secondary, row=0)
        inv_b.callback = self.on_inventory
        again_b = discord.ui.Button(label="🎁 Вскрыть ещё", style=discord.ButtonStyle.primary, row=0)
        again_b.callback = self.on_again
        self.add_item(inv_b)
        self.add_item(again_b)

    async def on_inventory(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Это меню открыто другим игроком.")
            return
        view = InventoryView(self.guild_id, self.user_id, self.owner_id)
        view.rebuild()
        await interaction.response.send_message(embed=view.build_embed(), view=view)

    async def on_again(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Это меню открыто другим игроком.")
            return
        box = next((b for b in loot_boxes(self.guild_id) if b["id"] == self.box_id), None)
        if box is None:
            await interaction.response.send_message("Тех-арк больше не доступен.")
            return
        view = LootConfirmView(self.guild_id, self.user_id, self.box_id)
        await interaction.response.edit_message(embed=loot_box_embed(box), view=view)

def loot_box_embed(box: dict) -> discord.Embed:
    embed = discord.Embed(title=f"⚙ Тех-арк: {box['name']}", color=GOLD_COLOR)
    embed.description = (
        f"Цена вскрытия: **{fmt_credits(box['price'])}** {CREDITS_NAME}\n"
        f"Предметов внутри: **{len(box.get('items', []))}**\n\n"
        f"Подтвердите вскрытие кнопкой ниже."
    )
    embed.set_footer(text=FOOTER_TEXT)
    return embed

# ------------------------------- /лутбоксы ------------------------------------ #
@g_loot.command(name="создать", description="Создать лутбокс (набор случайных предметов)")
@app_commands.check(is_bot_admin)
async def loot_box_create(
    interaction: discord.Interaction,
    name: app_commands.Range[str, 2, 80],
    price: app_commands.Range[int, 1, 10_000_000],
):
    boxes = loot_boxes(interaction.guild.id)
    if any(b["name"].lower() == name.lower() for b in boxes):
        await interaction.response.send_message("Тех-арк с таким названием уже есть.")
        return
    new_id = max([b["id"] for b in boxes], default=0) + 1
    boxes.append({"id": new_id, "name": name, "price": price, "items": []})
    save_loot()
    await interaction.response.send_message(
        f"➕ Тех-арк №{new_id} «{name}» создан (цена {fmt_credits(price)} {CREDITS_NAME}). "
        f"Наполните её: `/лутбоксы добавить`."
    )

@g_loot.command(name="список", description="Список лутбоксов")
async def loot_box_list(interaction: discord.Interaction):
    boxes = loot_boxes(interaction.guild.id)
    if not boxes:
        await interaction.response.send_message(
            "Лутбоксов пока нет: администрация создаст их командой `/лутбоксы создать`."
        )
        return
    lines = [f"**№{b['id']}** {b['name']} — **{fmt_credits(b['price'])}** {CREDITS_NAME} • предметов: {len(b.get('items', []))}" for b in boxes]
    await interaction.response.send_message("🧬 Тех-арки кузни:\n" + "\n".join(lines))

@g_loot.command(name="состав", description="Состав лутбокса: предметы и шансы")
async def loot_box_show(interaction: discord.Interaction, box: int):
    b = next((x for x in loot_boxes(interaction.guild.id) if x["id"] == box), None)
    if b is None:
        await interaction.response.send_message(f"Тех-арк №{box} не найден.")
        return
    embed = discord.Embed(title=f"⚙ Тех-арк №{b['id']}: {b['name']}", color=GOLD_COLOR)
    if b.get("items"):
        embed.description = "\n\n".join(
            f"**№{it['id']}** {it['name']} — шанс **{chance_percent(b, it):.1f}%**"
            + (f"\n└ {it['description']}" if it.get("description") else "")
            for it in b["items"]
        )
    else:
        embed.description = "Тех-арк пуст: администрация ещё не добавила предметы."
    embed.add_field(name="Цена", value=f"{fmt_credits(b['price'])} {CREDITS_NAME}", inline=True)
    embed.set_footer(text=FOOTER_TEXT)
    await interaction.response.send_message(embed=embed)

@g_loot.command(name="удалить", description="Удалить лутбокс")
@app_commands.check(is_bot_admin)
async def loot_box_remove(interaction: discord.Interaction, box: int):
    boxes = loot_boxes(interaction.guild.id)
    for b in list(boxes):
        if b["id"] == box:
            boxes.remove(b)
            save_loot()
            await interaction.response.send_message(f"🗑 Тех-арк №{box} удалён.")
            return
    await interaction.response.send_message(f"Тех-арк №{box} не найден.")

@g_loot.command(name="добавить", description="Добавить предмет в лутбокс с шансом выпадения")
@app_commands.check(is_bot_admin)
async def loot_item_add(
    interaction: discord.Interaction,
    box: int,
    name: app_commands.Range[str, 2, 80],
    chance: app_commands.Range[float, 0.01, 1000.0],
    description: Optional[str] = None,
):
    b = next((x for x in loot_boxes(interaction.guild.id) if x["id"] == box), None)
    if b is None:
        await interaction.response.send_message(f"Тех-арк №{box} не найден.")
        return
    new_id = max([it["id"] for it in b.get("items", [])], default=0) + 1
    b.setdefault("items", []).append(
        {"id": new_id, "name": name, "chance": round(chance, 2), "description": description or ""}
    )
    save_loot()
    await interaction.response.send_message(
        f"➕ Предмет №{new_id} «{name}» добавлен в тех-арк №{box} "
        f"(шанс {chance_percent(b, b['items'][-1]):.1f}%)."
    )

@g_loot.command(name="убрать", description="Убрать предмет из лутбокса")
@app_commands.check(is_bot_admin)
async def loot_item_remove(interaction: discord.Interaction, box: int, item: int):
    b = next((x for x in loot_boxes(interaction.guild.id) if x["id"] == box), None)
    if b is None:
        await interaction.response.send_message(f"Тех-арк №{box} не найден.")
        return
    for it in list(b.get("items", [])):
        if it["id"] == item:
            b["items"].remove(it)
            save_loot()
            await interaction.response.send_message(f"🗑 Предмет №{item} убран из тех-арка №{box}.")
            return
    await interaction.response.send_message(f"Предмет №{item} не найден в тех-арке №{box}.")

@g_loot.command(name="открыть", description="Открыть лутбокс за валюту")
async def loot_open(interaction: discord.Interaction, box: int):
    b = next((x for x in loot_boxes(interaction.guild.id) if x["id"] == box), None)
    if b is None:
        await interaction.response.send_message(f"Тех-арк №{box} не найден.")
        return
    if not b.get("items"):
        await interaction.response.send_message(f"Тех-арк №{box} пуст — вскрытие невозможно.")
        return
    view = LootConfirmView(interaction.guild.id, interaction.user.id, box)
    await interaction.response.send_message(embed=loot_box_embed(b), view=view)

# ------------------------------- /inventory -------------------------------- #
@bot.tree.command(name="инвентарь", description="Ваш инвентарь: предметы, применение, страницы")
async def cmd_inventory(interaction: discord.Interaction, user: Optional[discord.User] = None):
    target = user or interaction.user
    view = InventoryView(interaction.guild.id, target.id, interaction.user.id)
    view.rebuild()
    if target.id != interaction.user.id:
        await interaction.response.send_message(embed=view.build_embed())
        return
    await interaction.response.send_message(embed=view.build_embed(), view=view)
    await view.bind(interaction)

# ------------------------------- /розыгрыши -------------------------------- #
@g_gw.command(name="создать", description="Запустить розыгрыш: предметы в инвентарь или валюта на баланс")
@app_commands.check(is_bot_admin)
@app_commands.describe(
    title="Название розыгрыша",
    minutes="Сколько минут длится розыгрыш",
    winners="Число победителей",
    prize1="Приз за 1 место (название предмета ИЛИ сумма валюты при kind=валюта)",
    prize2="Приз за 2 место",
    prize3="Приз за 3 место",
)
@app_commands.choices(kind=[
    app_commands.Choice(name="Предметы в инвентарь", value="items"),
    app_commands.Choice(name="Валюта на баланс", value="credits"),
])
async def gw_create(
    interaction: discord.Interaction,
    title: app_commands.Range[str, 3, 100],
    minutes: app_commands.Range[int, 1, 10080],
    winners: app_commands.Range[int, 1, 10],
    kind: Optional[app_commands.Choice[str]] = None,
    prize1: Optional[str] = None,
    prize2: Optional[str] = None,
    prize3: Optional[str] = None,
):
    gws = guild_giveaways(interaction.guild.id)
    new_id = max([g["id"] for g in gws], default=0) + 1
    k = kind.value if kind else "items"
    prizes = {}
    for place, p in enumerate((prize1, prize2, prize3), 1):
        p = (p or "").strip()
        if not p:
            continue
        if k == "credits":
            try:
                v = int(p)
            except ValueError:
                v = 0
            if v > 0:
                prizes[str(place)] = {"credits": v}
        else:
            prizes[str(place)] = {"name": p[:80], "description": f"Приз жребия «{title}»"}
    gw = {
        "id": new_id,
        "title": title,
        "channel_id": interaction.channel.id,
        "message_id": None,
        "ends_at": time.time() + minutes * 60,
        "winners": winners,
        "prizes": prizes,
        "kind": k,
        "participants": [],
        "status": "active",
    }
    gws.append(gw)
    save_giveaways()
    view = GiveawayView(interaction.guild.id, new_id)
    msg = await interaction.channel.send(embed=giveaway_embed(gw), view=view)
    gw["message_id"] = msg.id
    save_giveaways()
    kind_lbl = "валюта на баланс" if k == "credits" else "предметы в кладезь"
    await interaction.response.send_message(
        f"✅ Жребий №{new_id} «{title}» запущен на {minutes} мин. Победителей: {winners}. Призы: {kind_lbl}."
    )

@g_gw.command(name="список", description="Активные розыгрыши сервера")
async def gw_list(interaction: discord.Interaction):
    active = [g for g in guild_giveaways(interaction.guild.id) if g.get("status") == "active"]
    if not active:
        await interaction.response.send_message("Активных жребиий нет.")
        return
    lines = [
        f"**№{g['id']}** {g['title']} — до <t:{int(g['ends_at'])}:R> • победителей: {g['winners']} • участников: {len(g['participants'])}"
        for g in active
    ]
    await interaction.response.send_message("🎁 Активные жребии:\n" + "\n".join(lines))

@g_gw.command(name="завершить", description="Досрочно завершить розыгрыш и определить победителей")
@app_commands.check(is_bot_admin)
async def gw_end(interaction: discord.Interaction, giveaway_id: int):
    gw = next((g for g in guild_giveaways(interaction.guild.id) if g["id"] == giveaway_id), None)
    if gw is None or gw.get("status") != "active":
        await interaction.response.send_message("Активный жребий с таким номером не найден.")
        return
    await finish_giveaway(interaction.guild, gw)
    await interaction.response.send_message(f"🏁 Жребий №{giveaway_id} завершён досрочно.")

@g_gw.command(name="отменить", description="Отменить розыгрыш без подведения итогов")
@app_commands.check(is_bot_admin)
async def gw_cancel(interaction: discord.Interaction, giveaway_id: int):
    gw = next((g for g in guild_giveaways(interaction.guild.id) if g["id"] == giveaway_id), None)
    if gw is None or gw.get("status") != "active":
        await interaction.response.send_message("Активный жребий с таким номером не найден.")
        return
    gw["status"] = "cancelled"
    save_giveaways()
    channel = interaction.guild.get_channel(gw.get("channel_id")) if gw.get("channel_id") else None
    if channel is not None and gw.get("message_id"):
        try:
            msg = await channel.fetch_message(gw["message_id"])
            await msg.edit(embed=giveaway_embed(gw), view=None)
        except discord.HTTPException:
            pass
    await interaction.response.send_message(f"⛔ Жребий №{giveaway_id} отменён.")

# ------------------------------- /выдача (ручная) -------------------------- #
@sg_grant_items.command(name="выдать", description="Выдать игроку предмет в инвентарь")
@app_commands.check(is_bot_admin)
async def adm_grant_item(
    interaction: discord.Interaction,
    user: discord.User,
    name: app_commands.Range[str, 2, 80],
    description: Optional[str] = None,
):
    iid = inv_add(interaction.guild.id, user.id, name, description or "", "admin")
    await interaction.response.send_message(
        f"✅ {user.mention} получает предмет №{iid} «{name}» в кладезь."
    )

@sg_grant_items.command(name="забрать", description="Забрать предмет из инвентаря игрока по номеру")
@app_commands.check(is_bot_admin)
async def adm_take_item(interaction: discord.Interaction, user: discord.User, iid: int):
    it = inv_use(interaction.guild.id, user.id, iid)
    if it is None:
        await interaction.response.send_message(f"Предмет №{iid} у {user.mention} не найден.")
        return
    await interaction.response.send_message(
        f"🗑 Предмет №{iid} «{it['name']}» изъят у {user.mention}."
    )

@sg_grant_items.command(name="список", description="Показать инвентарь игрока с номерами предметов")
@app_commands.check(is_bot_admin)
async def adm_inventory(interaction: discord.Interaction, user: discord.User):
    entries = inv_entries(interaction.guild.id, user.id)
    if not entries:
        await interaction.response.send_message(f"Кладезь {user.mention} пуст.")
        return
    lines = [
        f"**№{it['iid']}** {it['name']} — *{SOURCE_LABELS.get(it.get('source'), '—')}*"
        + (f" └ {it['description'][:60]}" if it.get("description") else "")
        for it in entries[:40]
    ]
    if len(entries) > 40:
        lines.append(f"… и ещё {len(entries) - 40}.")
    await interaction.response.send_message(f"🎒 Кладезь {user.mention}:\n" + "\n".join(lines))

@sg_grant_balance.command(name="опыт", description="Начислить или снять опыт: отрицательное amount снимает")
@app_commands.check(is_bot_admin)
async def grant_xp_signed(interaction: discord.Interaction, user: discord.User, amount: app_commands.Range[int, -1_000_000, 1_000_000]):
    cfg = get_config(interaction.guild.id)
    entry = prog_entry(interaction.guild.id, user.id)
    if amount >= 0:
        new_levels = apply_xp_delta(cfg, entry, amount)
        rewards = await apply_level_rewards(interaction.guild, user, cfg, new_levels)
        save_progress()
        extra = f" Повышение до ранга **{entry['level']}**!" if new_levels else ""
        if rewards:
            extra += " Награды: " + ", ".join(rewards) + "."
        await interaction.response.send_message(
            f"✅ {user.mention}: +{amount} XP (теперь ур. **{entry['level']}**, {entry['xp']} XP до следующего).{extra}")
    else:
        apply_xp_delta(cfg, entry, amount)
        save_progress()
        await interaction.response.send_message(
            f"➖ {user.mention}: {amount} XP (осталось {entry['xp']} XP на ур. {entry['level']}).")

@sg_grant_balance.command(name="валюта", description="Начислить или снять валюту: отрицательное amount снимает")
@app_commands.check(is_bot_admin)
async def grant_credits_signed(interaction: discord.Interaction, user: discord.User, amount: app_commands.Range[float, -10_000_000.0, 10_000_000.0]):
    entry = prog_entry(interaction.guild.id, user.id)
    apply_credits_delta(entry, amount)
    save_progress()
    sign = "+" if amount >= 0 else ""
    await interaction.response.send_message(
        f"{'✅' if amount >= 0 else '➖'} {user.mention}: {sign}{fmt_credits(amount)} {CREDITS_NAME} "
        f"(баланс {fmt_credits(entry['credits'])}).")

HELP_PAGES = [
    ("цитаты", (
        "**Автопубликация цитат:**\n"
        "`/цитаты включить` • `/цитаты выключить` — включить или выключить публикацию\n"
        "`/цитаты каналы добавить channel:#канал` • `убрать` • `список` — где публиковать\n"
        "`/цитаты интервал interval:45 window:10 min_messages:6 min_users:2` — частота и критерии активности канала\n"
        "`/цитаты эпоха` — какие эпохи цитат публиковать • `/цитаты режим` — режим цитат игроков\n"
        "`/цитаты настройки` — все настройки бота одним экраном\n\n"
        "**Цитаты игроков:**\n"
        "`/цитаты предложить text:… author:… source:…` — предложить свою цитату\n"
        "`/цитаты очередь` • `/цитаты одобрить quote_id:№` • `/цитаты отклонить quote_id:№` — проверка (для админов)\n"
        "`/цитаты случайная` — случайная цитата прямо сейчас"
    )),
    ("уровни", (
        "`/уровни каналы добавить channel:#канал` • `убрать` • `список` — каналы, где начисляются опыт и валюта\n"
        "`/уровни настройки xp:10 credits:5 xp_base:100 xp_step:10 mult_step:0.5` — начисление\n"
        "и кривые: старт опыта, шаг необходимого опыта за ранг, шаг роста модификаторов\n"
        "`/уровни настройки volume:True cap:2000 exclusive:True` — объёмный режим (награда растёт\n"
        "с длиной сообщения до лимита символов, без кулдауна) и снятие прежних ранг-ролей\n"
        "`/уровни таблица` — сколько опыта нужно для уровней 1–100 • `/уровни анонсы` — сообщения о повышении\n"
        "`/уровни множители задать level:5 value:2` • `/уровни множители таблица` — множитель валюты по уровням\n"
        "`/уровни роли добавить role:@роль value:1.25 apply:<…>` • `убрать` • `список` — множители для ролей\n"
        "`/уровни награды добавить level:5 kind:<Роль|Предмет в инвентарь|Валюта>` • `убрать reward_id:№` • `список`\n"
        "`/уровни сброс игрока user:@игрок` • `/уровни сброс всех confirm:True`"
    )),
    ("игрокам", (
        "`/ранг [user]` — ваш уровень, прогресс опыта и баланс валюты\n"
        "`/топ` — топ-10 игроков сервера по уровню\n"
        "`/рейтинг день` • `/рейтинг неделя` — текущие таблицы циклов активности\n"
        "`/рейтинг архив` — итоги закрытых циклов с наградами\n"
        "`/новости история` — последние опубликованные новости сервера\n"
        "`/кости dice:d20 count:3 modifier:1` — бросок костей (d3, d4, d5, d6, d8, d10, d20, d100)\n"
        "`/таро [cards]` — расклад Аугура с предсказанием (кнопка «Новый расклад»)\n"
        "`/инвентарь` — ваши предметы с кнопками: страницы, выбор, «Применить»\n"
        "`/магазин список` — товары • `/магазин купить item:№` — покупка за валюту\n"
        "`/лутбоксы список` • `/лутбоксы состав box:№` • `/лутбоксы открыть box:№` — открытие за валюту\n"
        "Участие в розыгрышах — кнопкой под сообщением розыгрыша\n"
        "Роли за реакции — ставьте эмодзи под панелями (сняли реакцию — роль убрана)\n"
        "Тикет в поддержку — кнопка «Создать тикет» на панели"
    )),
    ("роли, тикеты, аудит", (
        "**Роли за реакции (панели с эмодзи):**\n"
        "`/реакции панель channel:#канал title:… text:…` — создать панель\n"
        "`/реакции добавить panel_id:№ role:@роль emoji:🔥 label:…` — эмодзи = роль\n"
        "`/реакции убрать panel_id:№ role:@роль` • `/реакции список` • `/реакции удалить panel_id:№`\n"
"`/реакции проверка` — почему роли не выдаются: права, иерархия, починка реакций\n"
        "`/реакции тест panel_id:№ role:@роль` — живая проверка: бот выдаст и снимет роль на вас\n\n"
        "**Тикеты (как Ticket Tool):**\n"
        "`/тикеты настройки category:… support:@роль log_channel:#канал text:…`\n"
        "`/тикеты панель channel:#канал` — кнопка «Создать тикет» для игроков\n"
        "`/тикеты закрыть [reason]` (или кнопка в тикете) — закрытие с транскриптом в лог-канал\n"
        "`/тикеты добавить user:@игрок` — дать участнику доступ в тикет\n\n"
        "**Аудит сервера (как Juniper):**\n"
        "`/аудит настройки channel:#канал members:… messages:… server:…` — журнал событий\n"
        "`/аудит отчёт` — отчёт безопасности с оценкой 0–100 и рекомендациями"
    )),
    ("админам: экономика", (
        "`/магазин добавить • убрать • уведомления` — товары и уведомления о покупках\n"
        "`/лутбоксы создать name:… price:…` • `добавить box:№` • `убрать` • `состав` • `удалить` — лутбоксы\n"
        "`/розыгрыши создать title:… minutes:… winners:… kind:<Предметы|Валюта> prize1:…` — призы в инвентарь или на баланс\n"
        "`/розыгрыши список • завершить • отменить`\n"
        "`/выдача предметы выдать • забрать • список` — предметы вручную\n"
        "`/выдача баланс опыт user:@ amount:±…` • `/выдача баланс валюта user:@ amount:±…`\n"
        "`/приветствие включить • выключить • текст • тест` — приветствие новичков\n"
        "**Рейтинг активности (циклы день/неделя, награда — дата-коги):**\n"
        "`/рейтинг включить • выключить` • `/рейтинг канал channel:#канал` — анонсы итогов\n"
        "`/рейтинг награды period:<день|неделя> list:300,200,100` — таблица наград за места\n"
        "`/рейтинг порог value:5` • `/рейтинг показ value:10` • `/рейтинг пояс value:3`\n"
        "`/рейтинг исключения action:<добавить|убрать> role:@роль` — роли вне рейтинга\n\n"
        "**Новости сервера:**\n"
        "`/новости черновик title:… text:… [image:https…]` • `править` • `список` • `удалить`\n"
        "`/новости опубликовать id:№ [when:45 или 2026-10-24 20:00]` • `отменить`\n"
        "`/новости каналы action:<добавить|убрать|список>` • `/новости роль [role]`\n"
        "`/новости правка id:№ text:…` — правка опубликованного на месте"
    )),
    ("прочее", (
        "`/админы список • добавить • убрать` — кто может настраивать бота\n"
        "`/перенос список • статус • начать • остановить • удалить` — перенос сообщений между серверами\n"
        "`/цитаты очередь • одобрить • отклонить` — модерация цитат игроков\n"
        "`/помощь` — эти страницы • `/аватар` — только владелец бота"
    )),
]

@bot.tree.command(name="помощь", description="Помощь по командам бота")
async def lex_help(interaction: discord.Interaction):
    view = HelpView(interaction.user.id)
    await interaction.response.send_message(embed=view.build_embed(), view=view)
    await view.bind(interaction)
async def _is_bot_owner(interaction: discord.Interaction) -> bool:
    if bot.owner_id is None:
        app = await bot.application_info()
        bot.owner_id = app.owner.id
    return interaction.user.id == bot.owner_id


@bot.tree.command(name="аватар", description="[Владелец бота] Обновить аватар бота из приложенного файла")
@app_commands.checks.check(_is_bot_owner)
async def set_avatar(interaction: discord.Interaction, file: discord.Attachment):
    data = await file.read()
    await bot.user.edit(avatar=data)
    await interaction.response.send_message("✅ Аватар обновлён.")


# --------------------------------------------------------------------------- #
# Модуль: кости (броски d3–d100)
# --------------------------------------------------------------------------- #
DICE_SIDES = (3, 4, 5, 6, 8, 10, 20, 100)
DICE_MAX_COUNT = 20


def roll_dice(sides: int, count: int = 1, modifier: int = 0):
    """Честный бросок костей: (список результатов, сумма с модификатором)."""
    sides = int(sides)
    if sides not in DICE_SIDES:
        raise ValueError(f"недопустимая кость: d{sides}")
    count = max(1, min(int(count), DICE_MAX_COUNT))
    rolls = [random.randint(1, sides) for _ in range(count)]
    return rolls, sum(rolls) + int(modifier)


@bot.tree.command(name="кости", description="Бросить кости: d3, d4, d5, d6, d8, d10, d20 или d100")
@app_commands.choices(dice=[app_commands.Choice(name=f"d{s}", value=s) for s in DICE_SIDES])
@app_commands.describe(
    dice="Какая кость (d3–d100)",
    count="Сколько костей бросить за раз (1–20)",
    modifier="Прибавка к сумме (можно отрицательную)",
)
@app_commands.checks.cooldown(2, 5.0)
async def cmd_dice(
    interaction: discord.Interaction,
    dice: app_commands.Choice[int],
    count: Optional[app_commands.Range[int, 1, 20]] = None,
    modifier: Optional[app_commands.Range[int, -1000, 1000]] = None,
):
    rolls, total = roll_dice(dice.value, count or 1, modifier or 0)
    mod = modifier or 0
    shown = " • ".join(f"**{r}**" for r in rolls)
    mod_txt = f"\nМодификатор: **{'+' if mod >= 0 else '−'}{abs(mod)}**" if mod else ""
    embed = discord.Embed(
        title=f"🎲 Бросок: d{dice.value} ×{len(rolls)}",
        description=f"{shown}{mod_txt}\n\nИтого: **{total}**",
        color=GOLD_COLOR,
    )
    embed.set_footer(text=f"{interaction.user.display_name} • {FOOTER_TEXT}")
    await interaction.response.send_message(embed=embed)


# --------------------------------------------------------------------------- #
# Модуль: роли за реакции (панели с эмодзи, как Carl-bot)
# --------------------------------------------------------------------------- #
REACTIONS_PATH = os.path.join(DATA_DIR, "reaction_roles.json")
RROLES = _load(REACTIONS_PATH, {})


def save_rroles():
    _save(REACTIONS_PATH, RROLES)


def rr_guild(guild_id: int) -> dict:
    g = RROLES.setdefault(str(guild_id), {"next": 1, "panels": []})
    g.setdefault("next", 1)
    g.setdefault("panels", [])
    return g


def rr_panel(guild_id: int, panel_id: int):
    for p in rr_guild(guild_id)["panels"]:
        if p.get("id") == panel_id:
            return p
    return None


def emoji_key(emoji) -> str:
    """Единый ключ эмодзи: юникод — как есть, кастомный — '<:name:id>' / '<a:name:id>'."""
    if isinstance(emoji, str):
        return emoji.strip()
    if getattr(emoji, "id", None):
        return f"<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>"
    return emoji.name or ""


def emoji_from_str(text: str):
    """PartialEmoji из строки ('🔥' или '<:name:123>'); None, если не распознано."""
    try:
        return discord.PartialEmoji.from_str((text or "").strip()) or None
    except Exception:
        return None


_CUSTOM_KEY_RE = re.compile(r"^<a?:[^:>]*:(\d+)>$")


def _vs_strip(text: str) -> str:
    """Юникод-эмодзи без вариатора U+FE0F: клиенты шлют его то с ним, то без."""
    return (text or "").replace("\ufe0f", "")


def emoji_match(stored: str, other: str) -> bool:
    """Один и тот же эмодзи или нет (для ключей emoji_key).

    Точное совпадение, либо: кастомные — по id (эмодзи можно переименовать,
    id неизменен; раньше переименование тихо рвало панель), юникод — игнорируя
    вариатор U+FE0F ('▶' и '▶\ufe0f' — одна и та же кнопка для игрока).
    """
    if not stored or not other:
        return False
    if stored == other:
        return True
    m1, m2 = _CUSTOM_KEY_RE.match(stored), _CUSTOM_KEY_RE.match(other)
    if m1 and m2:
        return m1.group(1) == m2.group(1)
    if m1 or m2:
        return False  # кастом и юникод — разные эмодзи
    return _vs_strip(stored) == _vs_strip(other)


def _rr_resolve_emoji(guild, text: str):
    """Эмодзи из ввода админа: '🔥', '<:имя:id>' или имя эмодзи сервера (pepe).

    from_str() принимает за юникод любой текст, поэтому ASCII-имя ('pepe',
    ':pepe:') ищем среди эмодзи сервера — иначе на панели появлялась мёртвая
    запись, которую никто не мог нажать.
    """
    text = (text or "").strip()
    e = emoji_from_str(text)
    if e is not None and getattr(e, "id", None) is not None:
        return e  # '<:имя:id>' — настоящий кастомный эмодзи
    if e is not None and not text.isascii() and not any(ch.isalpha() for ch in text):
        return e  # юникод-эмодзи (без букв — отсечь слово «огонь» вместо 🔥)
    name = text.strip(":")
    if name:
        return discord.utils.get(getattr(guild, "emojis", []) or [], name=name)
    return None


def _rr_key_is_dead(key: str) -> bool:
    """Ключ эмодзи, который не может прийти от настоящего клика.

    Артефакт старых версий сайта: эмодзи вписывали именем («pepe», «:pepe:»),
    и from_str() принимал слово за «юникод-эмодзи». Настоящий кастом — всегда
    «<:имя:id>», а настоящие юникод-эмодзи не бывают чистым ASCII.
    """
    if not key:
        return True
    if _CUSTOM_KEY_RE.match(key):
        return False
    return key.isascii()


def _rr_heal_guild(guild) -> int:
    """Исцелить «мёртвые» записи эмодзи на панелях гильдии; вернуть число правок.

    «pepe» / «:pepe:» → настоящий серверный «<:pepe:id>». Записи, которые
    вылечить нельзя (эмодзи удалён с сервера, имя уже занято живой записью),
    не трогаем — их покажет диагностика /реакции проверка.
    """
    fixed = 0
    for p in rr_guild(getattr(guild, "id", 0))["panels"]:
        entries = p.get("entries", [])
        for e in entries:
            key = e.get("emoji") or ""
            if not _rr_key_is_dead(key):
                continue
            pe = _rr_resolve_emoji(guild, key)
            if pe is None:
                continue
            new = emoji_key(pe)
            if new == key or any(emoji_match(x.get("emoji") or "", new)
                                 for x in entries if x is not e):
                continue
            log.info("Реакции-роли: запись панели №%s исцелена: %r → %s",
                     p.get("id"), key, new)
            e["emoji"] = new
            fixed += 1
    if fixed:
        save_rroles()
    return fixed


def rr_find(guild_id: int, message_id: int, key: str):
    """(панель, запись) по сообщению и ключу эмодзи; (None, None), если нет.

    Два прохода: сначала точное совпадение ключа, затем «тот же эмодзи»
    (emoji_match) — переименованные кастомные и юникод-варианты U+FE0F
    больше не теряются тихо.
    """
    panels = [p for p in rr_guild(guild_id)["panels"] if p.get("message") == message_id]
    for p in panels:
        for e in p.get("entries", []):
            if e.get("emoji") == key:
                return p, e
    for p in panels:
        for e in p.get("entries", []):
            if emoji_match(e.get("emoji") or "", key):
                return p, e
    return None, None


def rr_panel_embed(panel: dict) -> discord.Embed:
    title = panel.get("title") or "Роли за реакции"
    text = (panel.get("text")
            or "Нажмите на эмодзи под этим сообщением, чтобы получить роль. Снимите реакцию — роль уберётся.")
    embed = discord.Embed(title=title, description=text, color=EMBED_COLOR)
    lines = [f"{e['emoji']} → <@&{e['role']}>" + (f" — {e['label']}" if e.get("label") else "")
             for e in panel.get("entries", [])]
    if lines:
        embed.add_field(name="Доступные роли", value="\n".join(lines[:25]), inline=False)
    embed.set_footer(text=f"Панель №{panel.get('id')} • {FOOTER_TEXT}")
    return embed


@g_reaction.command(name="панель", description="Создать панель ролей за реакции в канале")
@app_commands.check(is_bot_admin)
@app_commands.describe(
    channel="Канал, где появится панель",
    title="Заголовок панели",
    text="Текст-пояснение на панели",
)
async def rr_create_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: Optional[app_commands.Range[str, 2, 80]] = None,
    text: Optional[app_commands.Range[str, 2, 300]] = None,
):
    perms = channel.permissions_for(interaction.guild.me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.add_reactions):
        await interaction.response.send_message(
            "⛔ Боту нужны права в канале: просмотр, отправка сообщений, встраивание ссылок, добавление реакций.")
        return
    await interaction.response.defer()
    data = rr_guild(interaction.guild.id)
    panel = {"id": data["next"], "channel": channel.id, "message": 0,
             "title": (title or "")[:80], "text": (text or "")[:300], "entries": []}
    try:
        msg = await channel.send(embed=rr_panel_embed(panel))
    except discord.HTTPException as exc:
        await interaction.followup.send(f"⛔ Не удалось отправить панель в канал: {exc}")
        return
    panel["message"] = msg.id
    data["panels"].append(panel)
    data["next"] += 1
    save_rroles()
    await interaction.followup.send(
        f"✅ Панель ролей №{panel['id']} создана: {msg.jump_url}\n"
        f"Добавьте роли: `/реакции добавить panel_id:{panel['id']} role:@роль emoji:🔥`.")


@g_reaction.command(name="добавить", description="Добавить роль на панель: эмодзи = роль")
@app_commands.check(is_bot_admin)
@app_commands.describe(
    panel_id="Номер панели (смотрите /реакции список)",
    role="Роль для выдачи",
    emoji="Эмодзи: символ (🔥) или <:имя:id>",
    label="Короткое пояснение к роли (необязательно)",
)
async def rr_add(
    interaction: discord.Interaction,
    panel_id: int,
    role: discord.Role,
    emoji: str,
    label: Optional[app_commands.Range[str, 1, 60]] = None,
):
    guild = interaction.guild
    panel = rr_panel(guild.id, panel_id)
    if panel is None:
        await interaction.response.send_message(f"⛔ Панель №{panel_id} не найдена. Список: `/реакции список`.")
        return
    e = _rr_resolve_emoji(guild, emoji)
    if e is None:
        await interaction.response.send_message(
            "⛔ Не удалось распознать эмодзи. Примеры: 🔥, <:имя:123456789012345678> "
            "или имя эмодзи сервера (pepe).")
        return
    key = emoji_key(e)
    if any(emoji_match(x.get("emoji") or "", key) for x in panel["entries"]):
        await interaction.response.send_message(
            "⛔ Этот эмодзи уже используется на панели (возможно, в другом написании).")
        return
    if any(x["role"] == role.id for x in panel["entries"]):
        await interaction.response.send_message("⛔ Эта роль уже есть на панели.")
        return
    me = guild.me
    if role.managed or role >= me.top_role or role is guild.default_role:
        await interaction.response.send_message(
            "⛔ Эту роль нельзя выдать: она управляется интеграцией или стоит выше роли бота в иерархии.")
        return
    await interaction.response.defer()
    panel["entries"].append({"emoji": key, "role": role.id, "label": (label or "").strip()[:60]})
    save_rroles()
    channel = guild.get_channel(panel["channel"])
    msg = None
    if channel is not None:
        try:
            msg = await channel.fetch_message(panel["message"])
            await msg.edit(embed=rr_panel_embed(panel))
            await msg.add_reaction(e)
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"⚠️ Роль сохранена, но обновить панель не удалось (проверьте права бота): {exc}")
            return
    if msg is None:
        await interaction.followup.send(
            f"⚠️ Роль сохранена, но сообщение панели №{panel_id} не найдено — создайте панель заново.")
        return
    await interaction.followup.send(f"✅ Панель №{panel_id}: {key} → {role.mention}.")


@g_reaction.command(name="убрать", description="Убрать роль с панели ролей за реакции")
@app_commands.check(is_bot_admin)
@app_commands.describe(panel_id="Номер панели", role="Роль, которую убрать")
async def rr_remove(interaction: discord.Interaction, panel_id: int, role: discord.Role):
    guild = interaction.guild
    panel = rr_panel(guild.id, panel_id)
    if panel is None:
        await interaction.response.send_message(f"⛔ Панель №{panel_id} не найдена. Список: `/реакции список`.")
        return
    entry = next((x for x in panel["entries"] if x["role"] == role.id), None)
    if entry is None:
        await interaction.response.send_message("⛔ Этой роли нет на панели.")
        return
    await interaction.response.defer()
    panel["entries"].remove(entry)
    save_rroles()
    channel = guild.get_channel(panel["channel"])
    if channel is not None:
        try:
            msg = await channel.fetch_message(panel["message"])
            await msg.edit(embed=rr_panel_embed(panel))
            await msg.clear_reaction(entry["emoji"])
        except discord.HTTPException as exc:
            await interaction.followup.send(f"⚠️ Роль убрана, но обновить панель не удалось: {exc}")
            return
    await interaction.followup.send(f"✅ Роль {role.mention} убрана с панели №{panel_id}.")


@g_reaction.command(name="список", description="Список панелей ролей за реакции")
@app_commands.check(is_bot_admin)
async def rr_list(interaction: discord.Interaction):
    guild = interaction.guild
    panels = rr_guild(guild.id)["panels"]
    if not panels:
        await interaction.response.send_message("Панелей пока нет. Создайте: `/реакции панель channel:#канал`.")
        return
    lines = []
    for p in panels:
        link = f"https://discord.com/channels/{guild.id}/{p['channel']}/{p['message']}"
        roles = ", ".join(f"<@&{e['role']}>" for e in p.get("entries", [])[:6]) or "— пусто —"
        lines.append(f"**№{p['id']}** • <#{p['channel']}> • ролей: {len(p.get('entries', []))}\n{roles}\n[открыть сообщение]({link})")
    embed = discord.Embed(title="🎭 Панели ролей за реакции", description="\n\n".join(lines)[:4000], color=EMBED_COLOR)
    embed.set_footer(text=FOOTER_TEXT)
    await interaction.response.send_message(embed=embed)


@g_reaction.command(name="удалить", description="Удалить панель ролей за реакции")
@app_commands.check(is_bot_admin)
@app_commands.describe(panel_id="Номер панели")
async def rr_delete(interaction: discord.Interaction, panel_id: int):
    guild = interaction.guild
    data = rr_guild(guild.id)
    panel = rr_panel(guild.id, panel_id)
    if panel is None:
        await interaction.response.send_message(f"⛔ Панель №{panel_id} не найдена. Список: `/реакции список`.")
        return
    await interaction.response.defer()
    data["panels"].remove(panel)
    save_rroles()
    channel = guild.get_channel(panel["channel"])
    if channel is not None:
        try:
            msg = await channel.fetch_message(panel["message"])
            await msg.delete()
        except discord.HTTPException:
            pass
    await interaction.followup.send(f"✅ Панель №{panel_id} удалена.")


RR_FAIL_TEXTS = {
    "hier": ("⚙️ Механикус не смог выдать роль «{role}»: она стоит выше верхней роли бота "
             "или управляется интеграцией. Попросите администрацию поднять роль бота "
             "выше выдаваемой роли в настройках сервера."),
    "perm": ("⚙️ Механикус не смог выдать роль «{role}»: боту не хватает права "
             "«Управление ролями» на сервере."),
    "fail": ("⚙️ Механикус не смог выдать роль «{role}»: сбой обращения к Discord. "
             "Попробуйте ещё раз чуть позже."),
}


@g_reaction.command(name="проверка", description="Почему роли за реакции не выдаются: диагностика бота и панелей")
@app_commands.check(is_bot_admin)
async def rr_check(interaction: discord.Interaction):
    guild = interaction.guild
    await interaction.response.defer()
    me = guild.me
    dead_fixed = _rr_heal_guild(guild)
    healed = heal_fail = 0
    lines = [
        f"Сборка бота: `{BOT_BUILD}`",
        f"Право «Управление ролями» у бота: "
        f"{'есть' if me.guild_permissions.manage_roles else 'НЕТ — выдача ролей невозможна'}",
        f"Интент участников: "
        f"{'включён' if bot.intents.members else 'выключен — кэш пуст, бот запрашивает участника через API'}",
        f"Верхняя роль бота: {me.top_role.name} (позиция {me.top_role.position})",
    ]
    if dead_fixed:
        lines.append(f"Исцелено мёртвых записей эмодзи: {dead_fixed} "
                     f"(имя вместо эмодзи — артефакт старых версий сайта)")
    panels = rr_guild(guild.id)["panels"]
    if not panels:
        lines.append("Панелей нет: создайте — /реакции панель channel:#канал.")
    for p in panels:
        ch = guild.get_channel(p.get("channel"))
        lines.append(f"Панель №{p['id']}: канал {'#' + ch.name if ch is not None else 'УДАЛЁН'}"
                     f" • ролей: {len(p.get('entries', []))} • сообщение {p.get('message')}")
        if ch is None:
            continue
        cp = ch.permissions_for(me)
        miss = [n for n, v in (("просмотр канала", cp.view_channel),
                               ("отправка сообщений", cp.send_messages),
                               ("добавление реакций", cp.add_reactions)) if not v]
        if miss:
            lines.append(f"— канал: боту не хватает прав: {', '.join(miss)}")
        msg = None
        try:
            msg = await ch.fetch_message(p["message"])
        except discord.HTTPException:
            lines.append("— сообщение панели удалено: создайте панель заново")
        reacted = {emoji_key(r.emoji) for r in getattr(msg, "reactions", [])} if msg is not None else set()
        if not p.get("entries"):
            lines.append("— на панели нет ролей: /реакции добавить panel_id:… role:@роль emoji:🔥")
        for e in p.get("entries", []):
            role = guild.get_role(e["role"])
            if role is None:
                lines.append(f"— {e['emoji']}: роль удалена, запись устарела")
            elif role.managed:
                lines.append(f"— {e['emoji']} → @{role.name}: роль управляется интеграцией, выдача невозможна")
            elif role >= me.top_role:
                lines.append(f"— {e['emoji']} → @{role.name}: роль выше роли бота — поднимите роль бота выше")
            elif any(emoji_match(e.get("emoji") or "", k) for k in reacted):
                lines.append(f"— {e['emoji']} → @{role.name}: выдача возможна; реакция стоит на сообщении")
            else:
                # Самолечение: потерянную реакцию (сбой прав при создании, чистка
                # реакций модератором) ставим заново — игрокам снова есть что жать.
                pe = _rr_resolve_emoji(guild, e.get("emoji") or "")
                ok = False
                if pe is not None and msg is not None:
                    try:
                        await msg.add_reaction(pe)
                        reacted.add(e.get("emoji") or "")
                        ok = True
                    except discord.HTTPException:
                        ok = False
                if ok:
                    healed += 1
                    lines.append(f"— {e['emoji']} → @{role.name}: выдача возможна; "
                                 f"реакции НЕ БЫЛО — поставлена заново ✅")
                else:
                    heal_fail += 1
                    lines.append(f"— {e['emoji']} → @{role.name}: выдача возможна; реакции НЕТ "
                                 f"и поставить не удалось (права бота в канале)")
    if healed or heal_fail:
        lines.append(f"Итог починки: реакций поставлено — {healed}, не удалось — {heal_fail}.")
    lines.append("Живая проверка выдачи: /реакции тест panel_id:… role:@роль")
    embed = discord.Embed(title="⚙️ Диагностика ролей за реакции",
                          description="\n".join(lines)[:4000], color=EMBED_COLOR)
    embed.set_footer(text=FOOTER_TEXT)
    await interaction.followup.send(embed=embed)


@g_reaction.command(name="тест", description="Живая проверка: бот выдаст и снимет роль панели на вас")
@app_commands.check(is_bot_admin)
@app_commands.describe(panel_id="Номер панели (/реакции список)", role="Роль с этой панели")
async def rr_test(interaction: discord.Interaction, panel_id: int, role: discord.Role):
    """Полный цикл выдачи на вызвавшем админе: сразу видно, что именно мешает."""
    guild = interaction.guild
    panel = rr_panel(guild.id, panel_id)
    if panel is None:
        await interaction.response.send_message(f"⛔ Панель №{panel_id} не найдена. Список: `/реакции список`.")
        return
    entry = next((x for x in panel.get("entries", []) if x.get("role") == role.id), None)
    if entry is None:
        await interaction.response.send_message("⛔ Этой роли нет на панели.")
        return
    me = guild.me
    bad = []
    if not me.guild_permissions.manage_roles:
        bad.append("боту не хватает права «Управление ролями»")
    if role.managed:
        bad.append("роль управляется интеграцией (бот/подписка/связанная роль)")
    if role is guild.default_role:
        bad.append("это @everyone — выдача через панель запрещена")
    elif role >= me.top_role:
        bad.append(f"роль выше роли бота ({me.top_role.name}) — поднимите роль бота "
                   f"в Настройки сервера → Роли")
    if bad:
        await interaction.response.send_message("⛔ Выдача невозможна:\n" + "\n".join(f"— {b}" for b in bad))
        return
    await interaction.response.defer()
    member = interaction.user
    had = role in getattr(member, "roles", [])
    try:
        await member.add_roles(role, reason=f"Проверка /реакции тест (панель №{panel_id})")
        if not had:
            await member.remove_roles(role, reason=f"Проверка /реакции тест (панель №{panel_id})")
    except discord.Forbidden:
        await interaction.followup.send(
            "⛔ Discord отклонил выдачу (Forbidden): прав «Управление ролями» или иерархии "
            "не хватило уже в момент выдачи. Перепроверьте: `/реакции проверка`.")
        return
    except discord.HTTPException as exc:
        await interaction.followup.send(f"⚠️ Сбой выдачи: {exc}")
        return
    note = ""
    ch = guild.get_channel(panel.get("channel"))
    if ch is not None:
        try:
            msg = await ch.fetch_message(panel.get("message"))
            have = {emoji_key(r.emoji) for r in getattr(msg, "reactions", [])}
            if not any(emoji_match(entry.get("emoji") or "", k) for k in have):
                pe = emoji_from_str(entry.get("emoji") or "")
                if pe is not None:
                    await msg.add_reaction(pe)
                note = f"\n🔧 Реакции {entry.get('emoji')} под сообщением панели не было — поставил заново."
        except discord.HTTPException:
            note = ("\n⚠️ Не смог проверить реакцию под сообщением панели — проверьте "
                    "права бота в канале («Просмотр», «Добавление реакций»).")
    await interaction.followup.send(
        f"✅ Конвейер выдачи работает: роль {role.mention} выдана вам"
        + (" (она уже была — не снимал)" if had else " и затем снята")
        + ".\nЕсли у игроков роль всё равно не выдаётся — запустите `/реакции проверка`: "
        "она покажет панели и эмодзи и заново поставит потерянные реакции." + note)


RR_AUDIT_CAUSES = {
    "hier": "роль выше роли бота или управляется интеграцией",
    "perm": "боту не хватает права «Управление ролями»",
    "fail": "сбой обращения к Discord",
}


async def rr_refuse(guild, payload, member, role, cause: str):
    """Отказ выдачи не должен быть тихим: снять реакцию, пояснить в ЛС, записать в аудит."""
    channel = guild.get_channel(getattr(payload, "channel_id", 0))
    if channel is not None:
        try:
            msg = await channel.fetch_message(payload.message_id)
            await msg.remove_reaction(payload.emoji, member)
        except discord.HTTPException:
            pass
    try:
        await member.send(RR_FAIL_TEXTS.get(cause, RR_FAIL_TEXTS["fail"]).format(role=role.name))
    except discord.HTTPException:
        log.debug("Реакции-роли: ЛС закрыты, отказ не доставлен (user=%s)", payload.user_id)
    try:
        # ЛС у участника могут быть закрыты — причина отказа должна остаться
        # видимой и для сервера: пишем строку в журнал аудита (группа «Сервер»).
        await alog_send(guild, "server", alog_embed(
            "⚙️ Роли за реакции — отказ выдачи",
            f"{getattr(member, 'mention', member)} • реакция `{emoji_key(payload.emoji)}` "
            f"в <#{getattr(payload, 'channel_id', 0)}>\n"
            f"Роль **{role.name}**: {RR_AUDIT_CAUSES.get(cause, RR_AUDIT_CAUSES['fail'])}",
            GOLD_COLOR))
    except Exception as exc:  # аудит не должен ронять обработку реакций
        log.debug("Реакции-роли: отказ не записан в аудит: %s", exc)


async def rr_apply(payload, adding: bool):
    """Выдать/снять роль по реакции на панели (raw-события Discord)."""
    if payload.guild_id is None or (bot.user is not None and payload.user_id == bot.user.id):
        return
    key = emoji_key(payload.emoji)
    panel, entry = rr_find(payload.guild_id, payload.message_id, key)
    guild = bot.get_guild(payload.guild_id)
    if entry is None and guild is not None and any(
            p.get("message") == payload.message_id
            for p in rr_guild(payload.guild_id)["panels"]):
        # Клик по сообщению панели незнакомым эмодзи. Последний шанс: запись
        # панели может быть «мёртвой» — слово вместо эмодзи (так сохраняли
        # старые версии сайта). Лечим данные и повторяем поиск.
        if _rr_heal_guild(guild):
            panel, entry = rr_find(payload.guild_id, payload.message_id, key)
        if entry is None:
            # След для логов хостинга: эмодзи не привязан к роли (опечатка админа).
            log.info("Реакции-роли: эмодзи %s на сообщении панели %s не привязан к роли "
                     "(user=%s) — проверьте /реакции список", key, payload.message_id, payload.user_id)
            return
    if entry is None or guild is None:
        return
    role = guild.get_role(entry["role"])
    if role is None:  # роль удалена — чистим панель
        panel["entries"].remove(entry)
        save_rroles()
        return
    member = guild.get_member(payload.user_id)
    if member is None:
        # Кэш участников может быть пуст (интент members выключен, сервер не дочитан):
        # без запроса через API выдача молча прерывалась — главная причина жалоб
        # «панель создана, а роли не выдаются».
        try:
            member = await guild.fetch_member(payload.user_id)
        except discord.HTTPException:
            member = None
    if member is None or member.bot:
        return
    if role.managed or role >= guild.me.top_role or role is guild.default_role:
        log.warning("Реакции-роли: %s нельзя выдать (иерархия/интеграция) в %s", role, guild)
        await rr_refuse(guild, payload, member, role, "hier")
        return
    try:
        if adding:
            if role not in member.roles:
                await member.add_roles(role, reason=f"Роль за реакцию (панель №{panel['id']})")
        elif role in member.roles:
            await member.remove_roles(role, reason=f"Реакция снята (панель №{panel['id']})")
    except discord.Forbidden:
        log.warning("Реакции-роли: нет прав выдать %s в %s (нужно «Управление ролями»)", role, guild)
        await rr_refuse(guild, payload, member, role, "perm")
    except discord.HTTPException as exc:
        log.warning("Реакции-роли: сбой выдачи %s: %s", role, exc)
        await rr_refuse(guild, payload, member, role, "fail")


@bot.listen("on_raw_reaction_add")
async def rr_listen_add(payload):
    await rr_apply(payload, True)


@bot.listen("on_raw_reaction_remove")
async def rr_listen_remove(payload):
    await rr_apply(payload, False)


# --------------------------------------------------------------------------- #
# Модуль: тикеты (как Ticket Tool)
# --------------------------------------------------------------------------- #
TICKETS_PATH = os.path.join(DATA_DIR, "tickets.json")
TICKETS = _load(TICKETS_PATH, {})


def save_tickets():
    _save(TICKETS_PATH, TICKETS)


def tk_guild(guild_id: int) -> dict:
    g = TICKETS.setdefault(str(guild_id), {"next": 1, "panels": [], "tickets": {}})
    g.setdefault("next", 1)
    g.setdefault("panels", [])
    g.setdefault("tickets", {})
    return g


def ticket_channel_name(number: int, user) -> str:
    """Имя канала тикета: 'тикет-3-ник' (допустимые символы, не длиннее 32)."""
    raw = str(getattr(user, "name", user) or "user").lower()
    base = re.sub(r"[^0-9a-zа-яё_\-]+", "-", raw).strip("-") or "user"
    return f"тикет-{number}-{base}"[:32].rstrip("-")


def build_transcript(rows) -> str:
    """Транскрипт тикета: строки '[31.12.2025 23:59] автор: текст'."""
    lines = []
    for author, ts, text in rows:
        when = time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))
        lines.append(f"[{when}] {author}: {text}")
    return "\n".join(lines) + ("\n" if lines else "")


class TicketOpenView(discord.ui.View):
    """Персистентная кнопка «Создать тикет» на панели."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        b = discord.ui.Button(label="📩 Создать тикет", style=discord.ButtonStyle.success,
                              custom_id=f"tk_open:{guild_id}")
        b.callback = self.on_open
        self.add_item(b)

    async def on_open(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            return
        cfg = get_config(guild.id)
        if not cfg.get("ticket_category"):
            await interaction.response.send_message(
                "⛔ Тикеты не настроены: `/тикеты настройки` (категория, роль поддержки, лог-канал).")
            return
        category = guild.get_channel(int(cfg["ticket_category"]))
        if category is None or not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message("⛔ Категория тикетов не найдена — обновите `/тикеты настройки`.")
            return
        data = tk_guild(guild.id)
        for cid, t in data["tickets"].items():
            if t.get("owner") == interaction.user.id and guild.get_channel(int(cid)) is not None:
                await interaction.response.send_message(f"У вас уже есть открытый тикет: <#{cid}>.")
                return
        perms = category.permissions_for(guild.me)
        if not (perms.view_channel and perms.manage_channels and perms.send_messages):
            await interaction.response.send_message(
                "⛔ Боту нужны права «Просмотр каналов» и «Управление каналами» в категории тикетов.")
            return
        await interaction.response.defer()
        number = data["next"]
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                read_message_history=True, manage_channels=True, manage_messages=True),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                read_message_history=True),
        }
        support = guild.get_role(int(cfg["ticket_role"])) if cfg.get("ticket_role") else None
        if support is not None:
            overwrites[support] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                read_message_history=True, manage_messages=True)
        try:
            channel = await guild.create_text_channel(
                name=ticket_channel_name(number, interaction.user), category=category,
                overwrites=overwrites, reason=f"Тикет №{number} от {interaction.user}")
        except discord.Forbidden:
            await interaction.followup.send("⛔ Не удалось создать канал: боту нужно «Управление каналами».")
            return
        except discord.HTTPException as exc:
            await interaction.followup.send(f"⛔ Не удалось создать канал тикета: {exc}")
            return
        data["next"] = number + 1
        data["tickets"][str(channel.id)] = {
            "id": number, "owner": interaction.user.id, "created": int(time.time())}
        save_tickets()
        text = cfg.get("ticket_text") or DEFAULT_TICKET_TEXT
        desc = f"{interaction.user.mention}, тикет №**{number}** создан.\n\n{text}"
        embed = discord.Embed(title=f"🎫 Тикет №{number}", description=desc, color=EMBED_COLOR)
        embed.set_footer(text=FOOTER_TEXT)
        ping = interaction.user.mention if support is None else f"{interaction.user.mention} {support.mention}"
        try:
            await channel.send(content=ping, embed=embed, view=TicketCloseView(guild.id, channel.id))
        except discord.HTTPException as exc:
            log.warning("Тикеты: не удалось отправить приветствие в #%s: %s", channel.name, exc)
        # r21: публичное оповещение о создании тикета убрано по просьбе администрации:
        # приветствие с пингом остаётся только внутри канала тикета.


class TicketCloseView(discord.ui.View):
    """Персистентная кнопка «Закрыть тикет» внутри канала тикета."""

    def __init__(self, guild_id: int, channel_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.channel_id = channel_id
        b = discord.ui.Button(label="🔒 Закрыть тикет", style=discord.ButtonStyle.danger,
                              custom_id=f"tk_close:{guild_id}:{channel_id}")
        b.callback = self.on_close
        self.add_item(b)

    async def on_close(self, interaction: discord.Interaction):
        await ticket_close_flow(interaction, None)


async def ticket_close_flow(interaction: discord.Interaction, reason: Optional[str]):
    """Закрытие тикета: транскрипт в лог-канал, снятие с учёта, удаление канала."""
    guild, channel = interaction.guild, interaction.channel
    if guild is None or channel is None:
        await interaction.response.send_message("⛔ Работает только в канале тикета на сервере.")
        return
    data = tk_guild(guild.id)
    ticket = data["tickets"].get(str(channel.id))
    if ticket is None:
        await interaction.response.send_message("⛔ Этот канал не зарегистрирован как тикет.")
        return
    member = guild.get_member(interaction.user.id)
    allowed = (ticket.get("owner") == interaction.user.id or is_bot_admin(interaction)
               or (member is not None and member.guild_permissions.manage_channels))
    if not allowed:
        await interaction.response.send_message("⛔ Закрыть тикет может его владелец или администратор.")
        return
    await interaction.response.defer()
    rows = []
    try:
        async for m in channel.history(limit=300, oldest_first=True):
            body = m.content or ""
            if not body and m.attachments:
                body = "[вложения: " + ", ".join(a.filename or "файл" for a in m.attachments[:3]) + "]"
            if not body and m.embeds:
                body = "[эмбед]"
            rows.append((m.author.display_name, m.created_at.timestamp(), body or "…"))
    except discord.HTTPException:
        pass
    cfg = get_config(guild.id)
    log_ch = guild.get_channel(int(cfg["ticket_log"])) if cfg.get("ticket_log") else None
    if log_ch is not None:
        owner = guild.get_member(ticket.get("owner"))
        emb = discord.Embed(
            title=f"🎫 Тикет №{ticket.get('id')} закрыт", color=GOLD_COLOR,
            description=(f"Автор: {owner.mention if owner else ticket.get('owner')}\n"
                         f"Закрыл: {interaction.user.mention}\n"
                         f"Создан: <t:{ticket.get('created')}:R>"
                         + (f"\nПричина: {reason}" if reason else "")))
        emb.set_footer(text=FOOTER_TEXT)
        files = []
        if rows:
            files.append(discord.File(io.BytesIO(build_transcript(rows).encode("utf-8")),
                                      filename=f"ticket-{ticket.get('id')}.txt"))
        try:
            await log_ch.send(embed=emb, files=files)
        except discord.HTTPException as exc:
            log.warning("Тикеты: не удалось отправить лог закрытия: %s", exc)
    del data["tickets"][str(channel.id)]
    save_tickets()
    try:
        await channel.delete(reason=f"Тикет №{ticket.get('id')} закрыт ({interaction.user})")
    except discord.HTTPException as exc:
        await interaction.followup.send(f"⚠️ Тикет снят с учёта, но канал не удалён: {exc}")


@g_tickets.command(name="настройки", description="Настроить тикеты: категория, роль поддержки, лог-канал, текст")
@app_commands.check(is_bot_admin)
@app_commands.describe(
    category="Категория, где создаются каналы тикетов",
    support="Роль поддержки: доступ к тикетам и пинг при создании",
    log_channel="Канал логов закрытых тикетов (с транскриптом)",
    text="Приветственный текст внутри тикета",
)
async def ticket_settings(
    interaction: discord.Interaction,
    category: Optional[discord.CategoryChannel] = None,
    support: Optional[discord.Role] = None,
    log_channel: Optional[discord.TextChannel] = None,
    text: Optional[app_commands.Range[str, 10, 800]] = None,
):
    cfg = get_config(interaction.guild.id)
    changed = []
    if category is not None:
        cfg["ticket_category"] = category.id
        changed.append(f"категория: **{category.name}**")
    if support is not None:
        cfg["ticket_role"] = support.id
        changed.append(f"роль поддержки: {support.mention}")
    if log_channel is not None:
        cfg["ticket_log"] = log_channel.id
        changed.append(f"лог-канал: {log_channel.mention}")
    if text is not None:
        cfg["ticket_text"] = text
        changed.append("приветственный текст обновлён")
    if changed:
        save_configs()
    cur = (
        f"Категория: {'<#' + str(cfg['ticket_category']) + '>' if cfg.get('ticket_category') else '— не задана —'}\n"
        f"Роль поддержки: {'<@&' + str(cfg['ticket_role']) + '>' if cfg.get('ticket_role') else '— не задана —'}\n"
        f"Лог-канал: {'<#' + str(cfg['ticket_log']) + '>' if cfg.get('ticket_log') else '— не задан —'}"
    )
    prefix = ("✅ Обновлено: " + "; ".join(changed) + "\n\n") if changed else ""
    await interaction.response.send_message(
        prefix + "🎫 **Тикеты — текущие настройки:**\n" + cur
        + "\n\nПанель с кнопкой для игроков: `/тикеты панель channel:#канал`.")


@g_tickets.command(name="панель", description="Опубликовать панель с кнопкой «Создать тикет»")
@app_commands.check(is_bot_admin)
@app_commands.describe(channel="Канал, где появится панель", text="Текст на панели (необязательно)")
async def ticket_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    text: Optional[app_commands.Range[str, 10, 800]] = None,
):
    cfg = get_config(interaction.guild.id)
    if not cfg.get("ticket_category"):
        await interaction.response.send_message(
            "⛔ Сначала задайте категорию тикетов: `/тикеты настройки category:…`.")
        return
    perms = channel.permissions_for(interaction.guild.me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        await interaction.response.send_message(
            "⛔ Боту нужны права в канале: просмотр, отправка сообщений, встраивание ссылок.")
        return
    body = (text or "Поддержка сервера: нажмите кнопку ниже — бот создаст приватный канал тикета.")
    embed = discord.Embed(title="🎫 Поддержка", description=body, color=EMBED_COLOR)
    embed.set_footer(text=FOOTER_TEXT)
    await interaction.response.defer()
    try:
        msg = await channel.send(embed=embed, view=TicketOpenView(interaction.guild.id))
    except discord.HTTPException as exc:
        await interaction.followup.send(f"⛔ Не удалось опубликовать панель: {exc}")
        return
    tk_guild(interaction.guild.id)["panels"].append({"channel": channel.id, "message": msg.id})
    save_tickets()
    await interaction.followup.send(f"✅ Панель тикетов опубликована: {msg.jump_url}")


@g_tickets.command(name="закрыть", description="Закрыть тикет; транскрипт уйдёт в лог-канал")
@app_commands.describe(reason="Причина закрытия (необязательно)")
async def ticket_close_cmd(
    interaction: discord.Interaction,
    reason: Optional[app_commands.Range[str, 2, 300]] = None,
):
    await ticket_close_flow(interaction, reason)


@g_tickets.command(name="добавить", description="Добавить участника в канал тикета")
@app_commands.describe(user="Кого добавить в тикет")
async def ticket_add_user(interaction: discord.Interaction, user: discord.Member):
    guild, channel = interaction.guild, interaction.channel
    data = tk_guild(guild.id)
    if channel is None or str(channel.id) not in data["tickets"]:
        await interaction.response.send_message("⛔ Команда работает только внутри канала тикета.")
        return
    ticket = data["tickets"][str(channel.id)]
    member = guild.get_member(interaction.user.id)
    allowed = (ticket.get("owner") == interaction.user.id or is_bot_admin(interaction)
               or (member is not None and member.guild_permissions.manage_channels))
    if not allowed:
        await interaction.response.send_message("⛔ Добавлять участников может владелец тикета или администратор.")
        return
    try:
        await channel.set_permissions(
            user, view_channel=True, send_messages=True, embed_links=True, attach_files=True,
            read_message_history=True, reason=f"Добавлен в тикет №{ticket.get('id')} ({interaction.user})")
    except discord.Forbidden:
        await interaction.response.send_message("⛔ Боту нужно право «Управление каналами», чтобы дать доступ.")
        return
    await interaction.response.send_message(f"✅ {user.mention} добавлен(а) в тикет №{ticket.get('id')}.")


# --------------------------------------------------------------------------- #
# Модуль: аудит сервера (журнал событий + отчёт безопасности, как Juniper)
# --------------------------------------------------------------------------- #
AUDIT_EVENT_GROUPS = ("members", "messages", "server")
AUDIT_GROUP_LABELS = {
    "members": "Участники (вход/выход, баны, роли, ники)",
    "messages": "Сообщения (удаление/правка)",
    "server": "Сервер (каналы, роли, приглашения)",
}
AUDIT_COLORS = {
    "join": discord.Color.from_rgb(70, 150, 90),
    "leave": discord.Color.from_rgb(150, 110, 60),
    "ban": EMBED_COLOR,
    "msg": discord.Color.from_rgb(170, 80, 60),
    "edit": discord.Color.from_rgb(190, 150, 60),
    "server": discord.Color.from_rgb(120, 160, 190),
    "member": discord.Color.from_rgb(120, 120, 170),
}
MSG_CACHE: dict[int, deque] = {}  # id канала → последние сообщения; переживает перезапуск
MSG_CACHE_MAXLEN = 100            # глубина кэша на канал
MSG_CACHE_KEEP = 500              # обрезка текста: компактнее файл data/msg_cache.json
MSG_CACHE_PATH = os.path.join(DATA_DIR, "msg_cache.json")
AUDIT_DELETER_DELAY = 1.2         # запись в журнале аудита Discord появляется с задержкой
_MSG_CACHE_DIRTY = False


def msg_cache_load():
    """Загрузка кэша сообщений с диска — удаления видны и после перезапуска бота."""
    MSG_CACHE.clear()
    raw = _load(MSG_CACHE_PATH, {})
    if not isinstance(raw, dict):
        return
    for key, rows in raw.items():
        try:
            dq = deque(maxlen=MSG_CACHE_MAXLEN)
            for row in (rows if isinstance(rows, list) else []):
                if isinstance(row, dict) and row.get("id"):
                    dq.append(row)
            if dq:
                MSG_CACHE[int(key)] = dq
        except Exception:
            continue


def msg_cache_save():
    """Сброс кэша сообщений на диск (атомарно; только если были изменения)."""
    global _MSG_CACHE_DIRTY
    if not _MSG_CACHE_DIRTY:
        return
    try:
        items = sorted(MSG_CACHE.items(),
                       key=lambda kv: max((r["id"] for r in kv[1]), default=0),
                       reverse=True)[:400]
        _save(MSG_CACHE_PATH, {str(k): list(v) for k, v in items})
        _MSG_CACHE_DIRTY = False
    except Exception as exc:
        log.warning("Аудит: не удалось сохранить кэш сообщений: %s", exc)


msg_cache_load()


async def alog_send(guild: discord.Guild, group: str, embed: discord.Embed):
    """Отправить запись журнала событий (если канал задан и группа включена)."""
    try:
        cfg = get_config(guild.id)
        cid = cfg.get("audit_channel")
        if not cid:
            return
        events = cfg.get("audit_events") or {}
        if not events.get(group, True):
            return
        channel = guild.get_channel(int(cid))
        if channel is None:
            return
        embed.set_footer(text=FOOTER_TEXT)
        await channel.send(embed=embed)
    except discord.HTTPException as exc:
        log.warning("Аудит: не удалось отправить запись журнала: %s", exc)
    except Exception as exc:  # страховка: аудит не должен ронять обработчики событий
        log.warning("Аудит: сбой журнала: %s", exc)


def alog_embed(title: str, description: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(title=title, description=description[:4000], color=color)
    embed.timestamp = discord.utils.utcnow()
    return embed


@bot.listen("on_message")
async def alog_cache_message(message: discord.Message):
    """Кэш последних сообщений каналов — чтобы показывать содержимое при удалении.

    Кэш сбрасывается на диск (data/msg_cache.json), поэтому удаления сообщений,
    отправленных до перезапуска бота, тоже попадают в журнал.
    """
    global _MSG_CACHE_DIRTY
    if message.guild is None:
        return
    dq = MSG_CACHE.setdefault(message.channel.id, deque(maxlen=MSG_CACHE_MAXLEN))
    dq.append({
        "id": message.id,
        "author_id": message.author.id,
        "author": message.author.display_name,
        "content": (message.content or "")[:MSG_CACHE_KEEP],
        "attach": len(message.attachments),
        "embeds": len(message.embeds),
    })
    _MSG_CACHE_DIRTY = True


def alog_cached(channel_id: int, message_id: int):
    for row in MSG_CACHE.get(channel_id, ()):  # кэш: последние 100 сообщений канала
        if row["id"] == message_id:
            return row
    return None


async def _alog_deleter(guild, channel_id: int, action, author_id: Optional[int] = None):
    """Кто удалил сообщение — из журнала аудита Discord.

    Требуется право «Просмотр журнала аудита». Запись журнала появляется с небольшой
    задержкой, поэтому перед запросом делаем паузу. Всё обёрнуто в try/except:
    имя удалившего — полезный бонус, а не обязательная часть записи.
    """
    try:
        if AUDIT_DELETER_DELAY > 0:
            await asyncio.sleep(AUDIT_DELETER_DELAY)
        fresh_before = time.time() - 60  # старше минуты — уже другое удаление
        async for entry in guild.audit_log(action=action, limit=5):
            created = getattr(entry, "created_at", None)
            if created is not None and created.timestamp() < fresh_before:
                break
            ch = getattr(getattr(entry, "extra", None), "channel", None)
            if ch is not None and getattr(ch, "id", None) != channel_id:
                continue
            target = getattr(entry, "target", None)
            if (author_id is not None and target is not None
                    and getattr(target, "id", None) != author_id):
                continue
            if getattr(entry, "user", None) is not None:
                return entry.user
    except discord.HTTPException as exc:
        log.debug("Аудит: журнал Discord недоступен (право «Просмотр журнала аудита»?): %s", exc)
    except Exception as exc:
        log.debug("Аудит: не удалось определить удалившего: %s", exc)
    return None


def _alog_deleter_txt(deleter, verb: str = "Удалил(а)") -> str:
    if deleter is None:
        return ""
    name = getattr(deleter, "display_name", None) or getattr(deleter, "name", "?")
    return f"\n{verb}: {deleter.mention} (**{name}**)"


@bot.listen("on_member_join")
async def alog_member_join(member: discord.Member):
    age_days = (discord.utils.utcnow() - member.created_at).total_seconds() / 86400
    warn = "\n⚠️ **Аккаунт создан меньше суток назад — возможен рейд/скам.**" if age_days < 1 else ""
    embed = alog_embed(
        "➡️ Вход на сервер",
        f"{member.mention} (**{member}**, id `{member.id}`)\n"
        f"Аккаунт создан: <t:{int(member.created_at.timestamp())}:R>{warn}\n"
        f"Участников теперь: **{member.guild.member_count}**",
        AUDIT_COLORS["join"])
    await alog_send(member.guild, "members", embed)


@bot.listen("on_member_remove")
async def alog_member_remove(member: discord.Member):
    stayed = ""
    if member.joined_at is not None:
        days = (discord.utils.utcnow() - member.joined_at).total_seconds() / 86400
        stayed = f"\nПробыл(а) на сервере: **{days:.1f} дн.**"
    roles = ", ".join(r.mention for r in list(reversed(member.roles))[0:6]) or "—"
    embed = alog_embed("⬅️ Выход с сервера",
                       f"**{member}** (id `{member.id}`){stayed}\nРоли: {roles}",
                       AUDIT_COLORS["leave"])
    await alog_send(member.guild, "members", embed)


@bot.listen("on_member_ban")
async def alog_member_ban(guild: discord.Guild, user: discord.User):
    await alog_send(guild, "members",
                    alog_embed("🚫 Бан", f"**{user}** (id `{user.id}`)", AUDIT_COLORS["ban"]))


@bot.listen("on_member_unban")
async def alog_member_unban(guild: discord.Guild, user: discord.User):
    await alog_send(guild, "members",
                    alog_embed("🔓 Разбан", f"**{user}** (id `{user.id}`)", AUDIT_COLORS["join"]))


@bot.listen("on_raw_message_delete")
async def alog_raw_message_delete(payload):
    """Удаление сообщения. Именно raw-событие: оно приходит всегда — даже когда
    сообщения нет во внутреннем кэше Discord (on_message_delete в таком случае
    не срабатывает вовсе, а когда срабатывает — передаёт Message, а не payload).
    """
    if payload.guild_id is None:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    row = alog_cached(payload.channel_id, payload.message_id)
    cached = getattr(payload, "cached_message", None)
    if row is None and cached is not None:
        row = {"id": cached.id, "author_id": cached.author.id,
               "author": cached.author.display_name, "content": cached.content or "",
               "attach": len(cached.attachments), "embeds": len(cached.embeds)}
    channel = guild.get_channel(payload.channel_id) or bot.get_channel(payload.channel_id)
    ch_txt = channel.mention if channel is not None else f"`{payload.channel_id}`"
    if row is not None:
        body = row["content"]
        if not body and row["attach"]:
            body = f"[вложения: {row['attach']}]"
        if not body and row["embeds"]:
            body = "[эмбед]"
        if not body:
            body = ("[без текста]" if bot.intents.message_content
                    else "[текст недоступен: включите INTENTS_CONTENT=1]")
        author_txt = f"<@{row['author_id']}> (**{row['author']}**)"
        author_id = row["author_id"]
    else:
        body = "[содержимое неизвестно: сообщение не попало в кэш бота]"
        author_txt = "[автор неизвестен — сообщения нет в кэше]"
        author_id = None
    deleter = await _alog_deleter(guild, payload.channel_id,
                                  discord.AuditLogAction.message_delete, author_id=author_id)
    embed = alog_embed(
        "🗑 Удалено сообщение",
        f"Канал: {ch_txt}\nАвтор: {author_txt}\nСодержимое: {body[:900]}"
        + _alog_deleter_txt(deleter),
        AUDIT_COLORS["msg"])
    await alog_send(guild, "messages", embed)


@bot.listen("on_raw_bulk_message_delete")
async def alog_raw_bulk_message_delete(payload):
    """Массовое удаление (purge/очистка канала) — одной записью журнала."""
    if payload.guild_id is None:
        return
    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return
    ids = sorted(payload.message_ids)
    by_id = {m.id: m for m in (getattr(payload, "cached_messages", None) or [])}
    channel = guild.get_channel(payload.channel_id) or bot.get_channel(payload.channel_id)
    ch_txt = channel.mention if channel is not None else f"`{payload.channel_id}`"
    lines, found = [], 0
    for mid in ids:
        row = alog_cached(payload.channel_id, mid)
        m = by_id.get(mid)
        if row is None and m is not None:
            row = {"author": m.author.display_name, "content": m.content or "",
                   "attach": len(m.attachments), "embeds": len(m.embeds)}
        if row is None:
            continue
        found += 1
        txt = (row["content"] or "").replace("\n", " ")[:120]
        if not txt:
            txt = "[вложение]" if row["attach"] else "[эмбед]" if row["embeds"] else "[без текста]"
        lines.append(f"• **{row['author']}**: {txt}")
    if len(ids) - found > 0:
        lines.append(f"• ещё {len(ids) - found} сообщ. вне кэша бота (содержимое неизвестно)")
    shown = "\n".join(lines[:8])
    if len(lines) > 8:
        shown += f"\n…и ещё {len(lines) - 8} записей"
    deleter = await _alog_deleter(guild, payload.channel_id,
                                  discord.AuditLogAction.message_bulk_delete)
    embed = alog_embed(
        "🧹 Массовое удаление сообщений",
        f"Канал: {ch_txt}\nУдалено: **{len(ids)}** сообщ."
        + _alog_deleter_txt(deleter, "Очистил(а)")
        + (("\n" + shown) if shown else ""),
        AUDIT_COLORS["msg"])
    await alog_send(guild, "messages", embed)


@bot.listen("on_message_edit")
async def alog_message_edit(before: discord.Message, after: discord.Message):
    if after.guild is None or after.author.bot or after.webhook_id is not None:
        return
    old, new = before.content or "", after.content or ""
    if old == new:
        return  # правка эмбедов/вложений без текста — не логируем
    embed = alog_embed(
        "✏️ Изменено сообщение",
        f"Канал: {after.channel.mention}\nАвтор: {after.author.mention}\n"
        f"Было: {old[:450] or '—'}\nСтало: {new[:450] or '—'}\n[Перейти к сообщению]({after.jump_url})",
        AUDIT_COLORS["edit"])
    await alog_send(after.guild, "messages", embed)


@bot.listen("on_member_update")
async def alog_member_update(before: discord.Member, after: discord.Member):
    parts = []
    if before.nick != after.nick:
        parts.append(f"Ник: **{before.nick or before.name}** → **{after.nick or after.name}**")
    added = [r for r in after.roles if r not in before.roles]
    removed = [r for r in before.roles if r not in after.roles]
    if added:
        parts.append("Выданы роли: " + ", ".join(r.mention for r in added))
    if removed:
        parts.append("Сняты роли: " + ", ".join(r.mention for r in removed))
    if not parts:
        return
    embed = alog_embed("👤 Изменения участника", f"{after.mention}\n" + "\n".join(parts),
                       AUDIT_COLORS["member"])
    await alog_send(after.guild, "members", embed)


@bot.listen("on_guild_channel_create")
async def alog_channel_create(channel):
    if getattr(channel, "guild", None) is None:
        return
    txt = getattr(channel, "mention", None) or f"**#{channel.name}**"
    embed = alog_embed("📁 Создан канал", f"{txt} (тип: {type(channel).__name__})", AUDIT_COLORS["server"])
    await alog_send(channel.guild, "server", embed)


@bot.listen("on_guild_channel_delete")
async def alog_channel_delete(channel):
    if getattr(channel, "guild", None) is None:
        return
    embed = alog_embed("🗑 Удалён канал", f"**#{channel.name}** (тип: {type(channel).__name__})",
                       AUDIT_COLORS["msg"])
    await alog_send(channel.guild, "server", embed)


@bot.listen("on_guild_role_create")
async def alog_role_create(role: discord.Role):
    embed = alog_embed("🎭 Создана роль", f"{role.mention} (`{role.name}`)", AUDIT_COLORS["server"])
    await alog_send(role.guild, "server", embed)


@bot.listen("on_guild_role_delete")
async def alog_role_delete(role: discord.Role):
    embed = alog_embed("🎭 Удалена роль", f"`{role.name}`", AUDIT_COLORS["msg"])
    await alog_send(role.guild, "server", embed)


@bot.listen("on_guild_role_update")
async def alog_role_update(before: discord.Role, after: discord.Role):
    parts = []
    if before.name != after.name:
        parts.append(f"Название: **{before.name}** → **{after.name}**")
    if before.permissions != after.permissions:
        gained = [p for p, v in after.permissions if v and not getattr(before.permissions, p)]
        lost = [p for p, v in before.permissions if v and not getattr(after.permissions, p)]
        if gained:
            parts.append("Добавлены права: " + ", ".join(gained))
        if lost:
            parts.append("Убраны права: " + ", ".join(lost))
        if after.permissions.administrator and not before.permissions.administrator:
            parts.append("⚠️ Роль получила право **«Администратор»**!")
    if not parts:
        return
    embed = alog_embed("🎭 Изменена роль", f"{after.mention}\n" + "\n".join(parts), AUDIT_COLORS["server"])
    await alog_send(after.guild, "server", embed)


@bot.listen("on_invite_create")
async def alog_invite_create(invite: discord.Invite):
    if getattr(invite, "guild", None) is None:
        return
    ch = getattr(invite, "channel", None)
    ch_txt = getattr(ch, "mention", None) or (f"#{ch.name}" if ch is not None else "—")
    embed = alog_embed(
        "🔗 Создано приглашение",
        f"Код: `{invite.code}` • канал: {ch_txt}\n"
        f"Создал: {invite.inviter.mention if invite.inviter else '—'} • "
        f"лимит использований: {invite.max_uses or '∞'}",
        AUDIT_COLORS["server"])
    await alog_send(invite.guild, "server", embed)


def _enum_val(x, default: int = 0) -> int:
    """Enum/число -> int: перечисления discord.py 2.7 не приводятся int() напрямую."""
    v = getattr(x, "value", x)
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def areport_checks(guild, cfg) -> list:
    """Проверки безопасности: список (статус 0/1/2, название, пояснение, вес)."""
    checks = []
    mfa = _enum_val(getattr(guild, "mfa_level", 0))
    checks.append((2 if mfa >= 1 else 0, "Двухфакторная авторизация для модерации",
                   "Включена: действия модерации требуют 2FA у администрации." if mfa >= 1
                   else "Выключена. Включите: Настройки сервера → Безопасность → 2FA.", 2))
    vl = _enum_val(getattr(guild, "verification_level", 0))
    checks.append((2 if vl >= 2 else (1 if vl == 1 else 0), "Уровень проверки участников",
                   f"Уровень {vl} из 4. Рекомендуется «Средний» (2) и выше.", 1))
    cf = _enum_val(getattr(guild, "explicit_content_filter", 0))
    checks.append((2 if cf >= 2 else (1 if cf == 1 else 0), "Фильтр содержимого",
                   "Сканируются сообщения всех участников." if cf >= 2
                   else "Рекомендуется сканировать сообщения всех участников.", 1))
    dn = _enum_val(getattr(guild, "default_notifications", 1), 1)
    checks.append((2 if dn == 1 else 1, "Уведомления по умолчанию",
                   "Только @упоминания — меньше шума." if dn == 1
                   else "Все сообщения — на активных серверах утомляет участников.", 1))
    everyone = getattr(guild, "default_role", None)
    ev_admin = bool(everyone is not None and everyone.permissions.administrator)
    checks.append((0 if ev_admin else 2, "Права @everyone",
                   "⚠️ У @everyone есть право «Администратор» — снимите немедленно!" if ev_admin
                   else "У @everyone нет права «Администратор».", 3))
    admin_roles = [r for r in getattr(guild, "roles", []) if r.permissions.administrator]
    st = 2 if len(admin_roles) <= 3 else (1 if len(admin_roles) <= 7 else 0)
    names = ", ".join(r.name for r in admin_roles[:8]) or "—"
    checks.append((st, "Роли с правом «Администратор»", f"{len(admin_roles)} шт.: {names}", 2))
    bots = sum(1 for m in getattr(guild, "members", []) if getattr(m, "bot", False))
    checks.append((2 if bots <= 25 else 1, "Боты на сервере",
                   f"{bots} шт. — периодически проверяйте, все ли нужны.", 1))
    checks.append((2 if cfg.get("audit_channel") else 1, "Журнал событий бота",
                   "Настроен: события пишутся в канал." if cfg.get("audit_channel")
                   else "Не настроен: `/аудит настройки channel:#канал` включит журнал.", 1))
    return checks


def areport_score(checks) -> int:
    """Оценка безопасности 0–100: статус 2 = полный вес, 1 = половина, 0 = ноль."""
    total = sum(w for _s, _t, _d, w in checks)
    got = sum((w if s == 2 else w / 2.0 if s == 1 else 0) for s, _t, _d, w in checks)
    return round(100 * got / total) if total else 0


def areport_embed(guild, cfg) -> discord.Embed:
    checks = areport_checks(guild, cfg)
    score = areport_score(checks)
    verdict = ("отлично" if score >= 85 else "хорошо" if score >= 65
               else "требует внимания" if score >= 40 else "критично")
    color = (discord.Color.from_rgb(70, 150, 90) if score >= 65
             else GOLD_COLOR if score >= 40 else EMBED_COLOR)
    embed = discord.Embed(
        title=f"🛡 Аудит безопасности: {guild.name}",
        description=f"**Оценка: {score}/100 — {verdict}**",
        color=color,
    )
    marks = {2: "✅", 1: "⚠️", 0: "❌"}
    for status, title, detail, _w in checks:
        embed.add_field(name=f"{marks[status]} {title}", value=detail[:1024], inline=False)
    members = len(getattr(guild, "members", [])) or getattr(guild, "member_count", 0)
    embed.add_field(
        name="📊 Сервер",
        value=(f"Участников: **{members}** • ролей: **{len(getattr(guild, 'roles', []))}** • "
               f"каналов: **{len(getattr(guild, 'channels', []))}**"),
        inline=False)
    embed.set_footer(text=FOOTER_TEXT)
    return embed


def _onoff_choices():
    return [app_commands.Choice(name="Включить", value="on"),
            app_commands.Choice(name="Выключить", value="off")]


@g_audit.command(name="настройки", description="Журнал событий сервера: канал и группы событий")
@app_commands.check(is_bot_admin)
@app_commands.describe(
    channel="Канал для журнала событий",
    members="Логировать участников (вход/выход, баны, роли, ники)",
    messages="Логировать сообщения (удаление/правка)",
    server="Логировать сервер (каналы, роли, приглашения)",
)
@app_commands.choices(members=_onoff_choices(), messages=_onoff_choices(), server=_onoff_choices())
async def audit_settings_cmd(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    members: Optional[app_commands.Choice[str]] = None,
    messages: Optional[app_commands.Choice[str]] = None,
    server: Optional[app_commands.Choice[str]] = None,
):
    cfg = get_config(interaction.guild.id)
    events = cfg.setdefault("audit_events", {"members": True, "messages": True, "server": True})
    changed = []
    if channel is not None:
        cfg["audit_channel"] = channel.id
        changed.append(f"канал журнала: {channel.mention}")
    for key, choice in (("members", members), ("messages", messages), ("server", server)):
        if choice is not None:
            events[key] = choice.value == "on"
            changed.append(f"{key}: {'вкл' if events[key] else 'выкл'}")
    if changed:
        save_configs()
    cur = ("Канал: " + (f"<#{cfg['audit_channel']}>" if cfg.get("audit_channel") else "— не задан —") + "\n"
           + "\n".join(f"{AUDIT_GROUP_LABELS[k]}: {'✅' if events.get(k, True) else '⛔'}"
                        for k in AUDIT_EVENT_GROUPS))
    prefix = ("✅ Обновлено: " + "; ".join(changed) + "\n\n") if changed else ""
    await interaction.response.send_message(
        prefix + "🛡 **Журнал событий сервера:**\n" + cur
        + "\n\nУдаления пишутся всегда: кэш (100 последних на канал) сохраняется на диск и "
          "переживает перезапуск, массовые очистки (purge) — одной записью. «Кто удалил» — из "
          "журнала аудита Discord (нужно право «Просмотр журнала аудита»). Текст сообщений — "
          "при INTENTS_CONTENT=1. Отчёт безопасности: `/аудит отчёт`.")


@g_audit.command(name="отчёт", description="Отчёт безопасности сервера с оценкой и рекомендациями")
@app_commands.check(is_bot_admin)
async def audit_report_cmd(interaction: discord.Interaction):
    cfg = get_config(interaction.guild.id)
    await interaction.response.send_message(embed=areport_embed(interaction.guild, cfg))


# --------------------------------------------------------------------------- #
# Обработчик ошибок команд
# --------------------------------------------------------------------------- #
@bot.tree.error
async def on_app_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "⛔ Недостаточно прав: требуется право «Управление сервером»."
    elif isinstance(error, app_commands.CommandOnCooldown):
        msg = "⏳ Слишком часто: попробуйте чуть позже."
    elif isinstance(error, app_commands.CheckFailure):
        msg = ("⛔ Настраивать бота может только администратор бота на этом сервере. "
               "Если вы считаете, что это вы — попросите действующего администратора бота "
               "добавить вас через `/админы добавить`.")
    else:
        log.exception("Ошибка команды %s: %s", interaction.command.name if interaction.command else "?", error)
        msg = f"⚠️ Внутренняя ошибка: {error}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg)
        else:
            await interaction.response.send_message(msg)
    except discord.HTTPException:
        pass


# --------------------------------------------------------------------------- #
# Перенос ролевых сообщений между серверами (бот-читатель + вебхуки-писатели)
# --------------------------------------------------------------------------- #
MIG_PATH = os.path.join(DATA_DIR, "migrations.json")
MIGRATIONS = _load(MIG_PATH, {})
MIG_QUEUES: dict = {}
MIG_TASKS: dict = {}
_MIG_SESSION = None
MIG_INVITE_PERMS = 8  # Administrator: гарантированный доступ ко всем каналам исходного сервера

def save_migrations():
    _save(MIG_PATH, MIGRATIONS)

def mig_jobs(guild_id: int) -> list:
    return MIGRATIONS.setdefault(str(guild_id), [])

def build_avatar_url(author_id: int, avatar_hash) -> Optional[str]:
    if not avatar_hash:
        return None
    ext = "gif" if str(avatar_hash).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{author_id}/{avatar_hash}.{ext}"

def mig_invite_link() -> str:
    return (f"https://discord.com/oauth2/authorize?client_id={_client_id()}"
            f"&scope=bot%20applications.commands&permissions={MIG_INVITE_PERMS}")

async def _mig_session() -> aiohttp.ClientSession:
    global _MIG_SESSION
    if _MIG_SESSION is None or _MIG_SESSION.closed:
        _MIG_SESSION = aiohttp.ClientSession()
    return _MIG_SESSION

async def close_mig_session():
    """Закрыть общую HTTP-сессию переноса при остановке бота.

    Без этого aiohttp при завершении процесса логирует
    «ERROR asyncio | Unclosed client session» (сессия уничтожается сборщиком
    мусора незакрытой). Вызывается из обёртки bot.close() в main().
    """
    global _MIG_SESSION
    ses, _MIG_SESSION = _MIG_SESSION, None
    if ses is not None and not ses.closed:
        try:
            await ses.close()
            await asyncio.sleep(0.25)  # рекомендация aiohttp: дать SSL-соединениям закрыться штатно
        except Exception as exc:
            log.debug("Закрытие сессии переноса: %s", exc)

async def mig_send_one(webhook: discord.Webhook, m: discord.Message, job: dict,
                       thread_id=None, thread_name=None):
    """Одно сообщение через вебхук с именем/ликом автора (включая прокси-Tupper)."""
    kwargs = {
        "content": (m.content or "")[:2000],
        "username": (m.author.display_name or m.author.name)[:80],
        "allowed_mentions": discord.AllowedMentions.none(),
        "wait": True,
    }
    avatar = build_avatar_url(m.author.id, m.author.avatar.key if m.author.avatar else None)
    if avatar:
        kwargs["avatar_url"] = avatar
    if job.get("include_embeds", True) and m.embeds:
        kwargs["embeds"] = [discord.Embed.from_dict(e.to_dict()) for e in m.embeds[:10]]
    files = []
    if job.get("include_attachments", True):
        for a in m.attachments[:10]:
            if a.size > 25 * 1024 * 1024:
                log.warning("Перенос: вложение %s пропущено (больше 25 МБ)", a.filename)
                continue
            try:
                data = await a.read()
                files.append(discord.File(io.BytesIO(data), filename=a.filename or "attachment.bin"))
            except discord.HTTPException as exc:
                log.warning("Перенос: не удалось скачать вложение %s: %s", a.filename, exc)
    if files:
        kwargs["files"] = files
    if thread_id:
        kwargs["thread"] = discord.Object(id=int(thread_id))
    if thread_name:
        kwargs["thread_name"] = str(thread_name)[:100]
    try:
        return await webhook.send(**kwargs)
    except discord.HTTPException as exc:
        if exc.status == 429:
            await asyncio.sleep(getattr(exc, "retry_after", 2) or 2)
            return await webhook.send(**kwargs)
        raise

async def mig_collect_streams(channel):
    """Потоки сообщений источника: сам канал или ВСЕ публикации форума.
    Публикации собираются из трёх источников: кэш гейтвея, архивные треды (REST)
    и активные треды гильдии (REST) — чтобы не терять посты при пустом кэше."""
    if not isinstance(channel, discord.ForumChannel):
        return [channel]
    streams = {}
    for t in channel.threads:
        streams[t.id] = t
    try:
        async for t in channel.archived_threads(limit=None):
            streams.setdefault(t.id, t)
    except discord.HTTPException as exc:
        log.warning("Перенос: архивные публикации форума #%s недоступны: %s", channel.name, exc)
    try:
        from discord.http import Route
        data = await bot.http.request(
            Route("GET", "/guilds/{guild_id}/threads/active", guild_id=channel.guild.id)
        )
        for raw in data.get("threads", []):
            tid = int(raw.get("id") or 0)
            if not tid or tid in streams or int(raw.get("parent_id") or 0) != channel.id:
                continue
            th = channel.guild.get_thread(tid)
            if th is None:
                try:
                    fetched = await bot.fetch_channel(tid)
                    th = fetched if isinstance(fetched, discord.Thread) else None
                except discord.HTTPException:
                    th = None
            if th is not None:
                streams[tid] = th
    except Exception as exc:
        log.warning("Перенос: запрос активных тредов недоступен: %s", exc)
    log.info("Перенос: для #%s собрано публикаций: %s", channel.name, len(streams))
    return list(streams.values())

def mig_selected_streams(streams: list, job: dict) -> list:
    """Если в задаче выбраны конкретные публикации (thread_filter) — оставить только их."""
    ids = {int(x) for x in job.get("thread_filter") or []}
    if not ids:
        return streams
    return [t for t in streams if getattr(t, "id", 0) in ids]

async def _mig_send_msg(webhook: discord.Webhook, job: dict, m: discord.Message):
    """Отправка с учётом веток: публикации форумов воссоздаются на цели (thread_name/thread_id)."""
    ch = m.channel
    if not isinstance(ch, discord.Thread):
        return await mig_send_one(webhook, m, job)
    mp = job.setdefault("thread_map", {})
    tid = mp.get(str(ch.id))
    if tid:
        return await mig_send_one(webhook, m, job, thread_id=tid)
    target = bot.get_guild(int(job["guild"])).get_channel(int(job["target_channel"]))
    if target is None:
        log.warning("Перенос: целевой канал недоступен для ветки %s", ch.id)
        return None
    if isinstance(target, discord.ForumChannel):
        sent = await mig_send_one(webhook, m, job, thread_name=ch.name)
        # id созданной публикации: у WebhookMessage нет channel_id — он в sent.channel.id
        # (discord.py подставляет PartialMessageable с id треда при thread_name).
        tid = getattr(sent, "channel_id", None) or getattr(getattr(sent, "channel", None), "id", None)
        if tid == target.id:  # страховка: вдруг вернулись в сам форум, а не в пост
            tid = None
        if tid:
            mp[str(ch.id)] = tid
            save_migrations()
        return sent
    th = await target.create_thread(name=ch.name[:100], auto_archive_duration=1440)
    mp[str(ch.id)] = th.id
    save_migrations()
    return await mig_send_one(webhook, m, job, thread_id=th.id)

async def mig_history_messages(stream, limit: int, after=None):
    """Сообщения источника в хронологическом порядке: последние `limit` (0 = вся история)."""
    if limit and limit > 0:
        batch = [m async for m in stream.history(limit=limit)]  # свежие первыми
        batch.reverse()
        return batch
    return [m async for m in stream.history(limit=None, oldest_first=True, after=after)]

async def mig_worker(job_id: int):
    job = None
    for jobs in MIGRATIONS.values():
        job = next((j for j in jobs if j["id"] == job_id), None)
        if job:
            break
    if job is None:
        return
    gid = int(job["guild"])
    try:
        session = await _mig_session()
        webhook = discord.Webhook.from_url(job["webhook_url"], session=session)
        src_guild = bot.get_guild(int(job["source_guild"]))
        if src_guild is None:
            job["status"] = "error"
            job["error"] = "Бот не состоит на исходном сервере"
            save_migrations()
            return
        channel = src_guild.get_channel(int(job["source_channel"]))
        if channel is None:
            job["status"] = "error"
            job["error"] = "Исходный канал не найден"
            save_migrations()
            return
        # Фаза 1: перенос истории (текстовые каналы, форумы и отдельные ветки)
        if job.get("mode") in ("once", "both") and not job.get("history_done"):
            after = discord.Object(int(job["last_message_id"])) if job.get("last_message_id") else None
            streams = mig_selected_streams(await mig_collect_streams(channel), job)
            for stream in streams:
                if job.get("status") != "running":
                    return
                batch = await mig_history_messages(stream, _int(job.get("history_limit"), 100), after)
                for m in batch:
                    if job.get("status") != "running":
                        return
                    if m.type is not discord.MessageType.default:
                        continue
                    try:
                        await _mig_send_msg(webhook, job, m)
                        job["transferred"] = job.get("transferred", 0) + 1
                        job["last_message_id"] = m.id
                    except discord.HTTPException as exc:
                        job["errors"] = job.get("errors", 0) + 1
                        log.warning("Перенос: сбой сообщения %s: %s", m.id, exc)
                    if job.get("transferred", 0) % 10 == 0:
                        save_migrations()
                    await asyncio.sleep(0.4)
            job["history_done"] = True
            save_migrations()
            if job.get("mode") == "once":
                job["status"] = "done"
                save_migrations()
                log.info("Перенос задачи %s завершён: %s сообщений", job_id, job.get("transferred"))
                return
        # Фаза 2: зеркалирование новых сообщений
        if job.get("mode") in ("mirror", "both"):
            q = MIG_QUEUES.setdefault(job_id, asyncio.Queue())
            log.info("Перенос задачи %s: зеркалирование включено", job_id)
            while job.get("status") == "running":
                m = await q.get()
                try:
                    await _mig_send_msg(webhook, m, job)
                    job["transferred"] = job.get("transferred", 0) + 1
                    job["last_message_id"] = m.id
                except discord.HTTPException as exc:
                    job["errors"] = job.get("errors", 0) + 1
                    log.warning("Зеркало: сбой сообщения %s: %s", m.id, exc)
                if job["transferred"] % 10 == 0:
                    save_migrations()
                await asyncio.sleep(0.35)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.exception("Перенос задачи %s упал: %s", job_id, exc)
        job["status"] = "error"
        job["error"] = str(exc)[:200]
        save_migrations()

def mig_dispatch(message: discord.Message):
    """Из on_message: ставит новые сообщения исходных серверов в очереди зеркалирования."""
    try:
        for jobs in MIGRATIONS.values():
            for job in jobs:
                if (job.get("status") == "running" and job.get("mode") in ("mirror", "both")
                        and str(message.guild.id) == str(job.get("source_guild"))
                        and message.type is discord.MessageType.default):
                    ch = message.channel
                    src_id = int(job.get("source_channel") or 0)
                    if isinstance(ch, discord.Thread):
                        match = ch.id == src_id or ch.parent_id == src_id
                        if match and job.get("thread_filter"):
                            match = ch.id in {int(x) for x in job["thread_filter"]}
                    else:
                        match = ch.id == src_id
                    if match:
                        q = MIG_QUEUES.get(job["id"])
                        if q is not None:
                            q.put_nowait(message)
    except Exception:
        pass

async def mig_start(guild: discord.Guild, job: dict):
    if not bot.intents.message_content:
        return False, ("Для переноса нужен Message Content Intent: включите его в портале "
                       "(Bot → Privileged Gateway Intents) и задайте Env INTENTS_CONTENT=1, затем Restart.")
    if not job.get("webhook_url"):
        target = guild.get_channel(int(job["target_channel"]))
        if target is None:
            return False, "Целевой канал не найден."
        try:
            wh = await target.create_webhook(name=f"Mechanicus-MIG-{job['id']}")
            job["webhook_url"] = wh.url
        except discord.HTTPException as exc:
            return False, f"Не удалось создать вебхук (нужно Manage Webhooks): {exc}"
    old = MIG_TASKS.get(job["id"])
    if old:
        old.cancel()
    job["status"] = "running"
    job["error"] = ""
    save_migrations()
    MIG_QUEUES.setdefault(job["id"], asyncio.Queue())
    MIG_TASKS[job["id"]] = bot.loop.create_task(mig_worker(job["id"]))
    return True, "Задача переноса запущена."

async def mig_stop(job: dict):
    task = MIG_TASKS.pop(job["id"], None)
    if task:
        task.cancel()
    job["status"] = "stopped"
    save_migrations()
    return True, "Задача остановлена."

g_mig = app_commands.Group(name="перенос", description="Перенос ролевых сообщений между серверами")

@g_mig.command(name="список", description="Задачи переноса на этом сервере")
@app_commands.check(is_bot_admin)
async def mig_list(interaction: discord.Interaction):
    jobs = mig_jobs(interaction.guild.id)
    if not jobs:
        await interaction.response.send_message(
            f"Задач переноса нет. Создайте во вкладке «Перенос» на сайте. "
            f"Пригласить бота на исходный сервер: {mig_invite_link()}")
        return
    lines = []
    for j in jobs:
        sg = bot.get_guild(int(j["source_guild"]))
        lines.append(f"**№{j['id']}** {sg.name if sg else j['source_guild']} → <#{j['target_channel']}> • "
                     f"{j.get('mode')} • {j.get('status')} • перенесено {j.get('transferred', 0)}")
    await interaction.response.send_message("📦 Задачи переноса:\n" + "\n".join(lines))

@g_mig.command(name="статус", description="Подробный статус задачи переноса")
@app_commands.check(is_bot_admin)
async def mig_status(interaction: discord.Interaction, job: int):
    j = next((x for x in mig_jobs(interaction.guild.id) if x["id"] == job), None)
    if j is None:
        await interaction.response.send_message(f"Задача №{job} не найдена.")
        return
    await interaction.response.send_message(
        f"Задача №{job}: статус {j.get('status')}, режим {j.get('mode')}, "
        f"перенесено {j.get('transferred', 0)}, ошибок {j.get('errors', 0)}"
        + (f", причина: {j.get('error')}" if j.get("error") else ""))

@g_mig.command(name="начать", description="Запустить задачу переноса")
@app_commands.check(is_bot_admin)
async def mig_start_cmd(interaction: discord.Interaction, job: int):
    j = next((x for x in mig_jobs(interaction.guild.id) if x["id"] == job), None)
    if j is None:
        await interaction.response.send_message(f"Задача №{job} не найдена.")
        return
    ok, msg = await mig_start(interaction.guild, j)
    await interaction.response.send_message(("✅ " if ok else "⛔ ") + msg)

@g_mig.command(name="остановить", description="Остановить задачу переноса")
@app_commands.check(is_bot_admin)
async def mig_stop_cmd(interaction: discord.Interaction, job: int):
    j = next((x for x in mig_jobs(interaction.guild.id) if x["id"] == job), None)
    if j is None:
        await interaction.response.send_message(f"Задача №{job} не найдена.")
        return
    ok, msg = await mig_stop(j)
    await interaction.response.send_message(("✅ " if ok else "⛔ ") + msg)

@g_mig.command(name="удалить", description="Удалить задачу переноса")
@app_commands.check(is_bot_admin)
async def mig_remove_cmd(interaction: discord.Interaction, job: int):
    jobs = mig_jobs(interaction.guild.id)
    j = next((x for x in jobs if x["id"] == job), None)
    if j is None:
        await interaction.response.send_message(f"Задача №{job} не найдена.")
        return
    await mig_stop(j)
    jobs.remove(j)
    save_migrations()
    await interaction.response.send_message(f"🗑 Задача №{job} удалена.")


async def _threads_boxes(channel: discord.ForumChannel) -> str:
    streams = await mig_collect_streams(channel)
    boxes = "".join(
        f"<label style='display:block;margin:6px 0'><input type='checkbox' name='threads' value='{t.id}'> "
        f"«{t.name}» — сообщений: {getattr(t, 'message_count', '?')}</label>"
        for t in streams[:60]
    )
    extra = f" (показаны первые 60 из {len(streams)})" if len(streams) > 60 else ""
    return (
        "<label>Публикации форума — отметьте нужные для переноса" + extra +
        ":</label><div style='max-height:230px;overflow:auto;border:1px solid #3a2c55;border-radius:8px;padding:10px'>"
        + (boxes or "<p>Публикаций не найдено.</p>") + "</div>"
        "<p class='sub'>Если не отмечена ни одна публикация — переносится весь форум целиком.</p>"
    )

async def dash_migration(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    jobs = mig_jobs(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/migration?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:перенос", post.get("action", ""))
        action = post.get("action", "")
        if action == "probe":
            sg = bot.get_guild(_int(post.get("source_guild")))
            ch = sg.get_channel(_int(post.get("source_channel"))) if sg else None
            if ch is None:
                raise web.HTTPFound(f"/guild/{gid}/migration?msg=Канал+не+найден&kind=err")
            streams = await mig_collect_streams(ch)
            if isinstance(ch, discord.ForumChannel):
                names = ", ".join(f"«{t.name}»" for t in streams[:8]) or "—"
                extra = f" …и ещё {len(streams) - 8}" if len(streams) > 8 else ""
                msg = f"Форум «{ch.name}»: бот видит публикаций: {len(streams)} → {names}{extra}"
            else:
                cnt = 0
                async for _m in ch.history(limit=100):
                    cnt += 1
                msg = f"Канал «{ch.name}»: бот видит сообщений (до 100): {cnt}"
            raise web.HTTPFound(f"/guild/{gid}/migration?src={sg.id}&sch={_int(post.get('ui_sch'))}&msg={urllib.parse.quote(msg)}")
        if action == "create":
            src_guild = bot.get_guild(_int(post.get("source_guild")))
            if src_guild is None:
                raise web.HTTPFound(f"/guild/{gid}/migration?msg=Бот+не+состоит+на+исходном+сервере&kind=err")
            if not _int(post.get("source_channel")):
                raise web.HTTPFound(f"/guild/{gid}/migration?src={src_guild.id}&sch={_int(post.get('ui_sch'))}&msg=Выберите+исходный+канал&kind=err")
            new_id = max([j["id"] for j in jobs], default=0) + 1
            jobs.append({
                "id": new_id, "guild": str(gid),
                "source_guild": str(src_guild.id),
                "source_channel": _int(post.get("source_channel")),
                "target_channel": _int(post.get("target_channel")),
                "webhook_url": (post.get("webhook_url") or "").strip() or None,
                "mode": post.get("mode", "both"),
                "history_limit": max(0, _int(post.get("history_limit"), 100)),
                "thread_filter": [_int(x) for x in post.getall("threads", []) if _int(x)],
                "include_attachments": "attachments" in post,
                "include_embeds": "embeds" in post,
                "status": "idle", "transferred": 0, "errors": 0,
                "last_message_id": None, "history_done": False, "error": "",
            })
            save_migrations()
            raise web.HTTPFound(f"/guild/{gid}/migration?src={_int(post.get('ui_src'))}&sch={_int(post.get('ui_sch'))}"
                        f"&msg=Задача+создана+—+нажмите+Запустить")
        if action in ("start", "stop", "remove"):
            j = next((x for x in jobs if x["id"] == _int(post.get("job"))), None)
            if j is None:
                raise web.HTTPFound(f"/guild/{gid}/migration?msg=Задача+не+найдена&kind=err")
            if action == "start":
                ok, msg = await mig_start(guild, j)
            elif action == "stop":
                ok, msg = await mig_stop(j)
            else:
                await mig_stop(j)
                jobs.remove(j)
                save_migrations()
                ok, msg = True, "Задача удалена"
            raise web.HTTPFound(f"/guild/{gid}/migration?src={_int(post.get('ui_src'))}&sch={_int(post.get('ui_sch'))}"
                        f"&msg={urllib.parse.quote(msg)}" + ("" if ok else "&kind=err"))
    src_sel = _int(request.query.get("src"))
    src_guild = bot.get_guild(src_sel) if src_sel else None
    sch_sel = _int(request.query.get("sch"))
    src_channel = src_guild.get_channel(sch_sel) if src_guild and sch_sel else None
    threads_html = ""
    if isinstance(src_channel, discord.ForumChannel):
        threads_html = await _threads_boxes(src_channel)
    ui_src = _int(request.query.get("src"))
    ui_sch = _int(request.query.get("sch"))
    ui_hid = (f"<input type='hidden' name='ui_src' value='{ui_src}'>"
              f"<input type='hidden' name='ui_sch' value='{ui_sch}'>")
    rows = ""
    for j in jobs:
        sg = bot.get_guild(int(j["source_guild"]))
        sc = sg.get_channel(int(j["source_channel"])) if sg else None
        acts = (
            f"<form method='post' style='display:inline'>{csrf_input(session)}{ui_hid}"
            f"<input type='hidden' name='action' value='start'><input type='hidden' name='job' value='{j['id']}'>"
            "<button>▶ Запустить</button></form> "
            f"<form method='post' style='display:inline'>{csrf_input(session)}{ui_hid}"
            f"<input type='hidden' name='action' value='stop'><input type='hidden' name='job' value='{j['id']}'>"
            "<button class='ghost'>⏸ Стоп</button></form> "
            f"<form method='post' style='display:inline'>{csrf_input(session)}{ui_hid}"
            f"<input type='hidden' name='action' value='remove'><input type='hidden' name='job' value='{j['id']}'>"
            "<button class='danger'>🗑</button></form>"
        )
        filt = j.get("thread_filter") or []
        rows += (
            f"<tr><td>№{j['id']}</td><td>{sg.name if sg else j['source_guild']} / #{sc.name if sc else j['source_channel']}"
            f"<br><span class='sub'>публикации: {len(filt) if filt else 'все'}</span></td>"
            f"<td>#{guild.get_channel(int(j['target_channel'])).name if guild.get_channel(int(j['target_channel'])) else j['target_channel']}</td>"
            f"<td>{j.get('mode')}</td><td>{j.get('status')}</td><td>{j.get('transferred', 0)} / err {j.get('errors', 0)}</td>"
            f"<td>{acts}</td></tr>"
        )
    jobs_block = (
        f"<table><tr><th>№</th><th>Источник</th><th>Цель</th><th>Режим</th><th>Статус</th><th>Прогресс</th><th></th></tr>{rows}</table>"
        if jobs else "<p>Задач переноса пока нет.</p>"
    )
    g_opts = "".join(
        f"<option value='{g.id}'{' selected' if src_guild and g.id == src_guild.id else ''}>{g.name}</option>"
        for g in bot.guilds if g.id != gid
    )
    src_list = (list(src_guild.text_channels) + list(src_guild.forums)) if src_guild else []
    sc_opts = "".join(
        f"<option value='{c.id}'{' selected' if c.id == sch_sel else ''}>"
        f"{'📁 ' if isinstance(c, discord.ForumChannel) else '#'}{c.name}</option>"
        for c in src_list
    )
    tc_opts = "".join(
        f"<option value='{c.id}'>{'📁 ' if isinstance(c, discord.ForumChannel) else '#'}{c.name}</option>"
        for c in (list(guild.text_channels) + list(guild.forums))
    )
    body = (
        flash_block(request)
        + f"<div class='card'><h2>Журнал переноса</h2>"
        "<div class='sub'>гибридная схема: бот читает историю исходного сервера, вебхук пишет её в целевой канал "
        "с именем и аватаром автора (включая персонажей Tupperbox/PluralKit)</div>"
        f"<p><span class='pill'>1</span> Пригласите бота на исходный сервер ссылкой: "
        f"<code>{mig_invite_link()}</code> (права: просмотр каналов + чтение истории).</p>"
        "<p><span class='pill'>2</span> Для переноса текста нужен Message Content Intent: портал → Bot → "
        "Privileged Gateway Intents → Message Content Intent, и Env <code>INTENTS_CONTENT=1</code>, затем Restart.</p>"
        "<p><span class='pill'>3</span> Создайте задачу: вебхук создастся сам (нужно Manage Webhooks в целевом канале) "
        "или вставьте готовый URL вебхука канала.</p></div>"
        + f"<div class='card'><h2>Список передач</h2>{jobs_block}</div>"
        + f"<div class='card'><h2>Новая передача</h2>"
        f"<form method='get' class='row'>"
        f"<div><label>Исходный сервер (где бот)</label><select name='src' onchange='this.form.submit()'>"
        f"<option value='0'>— выберите —</option>{g_opts}</select></div>"
        f"<div><label>Исходный канал / форум</label><select name='sch' onchange='lvThreads(this.form.src.value, this.value)'>"
        f"<option value='0'>— выберите —</option>{sc_opts}</select></div>"
        "<div style='flex:0'><label>&nbsp;</label><button class='ghost'>Показать публикации</button></div></form></div>"
        + f"<form method='post'>{csrf_input(session)}"
        "<input type='hidden' name='source_guild' value='" + str(src_guild.id if src_guild else 0) + "'>"
        "<input type='hidden' name='source_channel' value='" + str(sch_sel or 0) + "'>"
        + ui_hid
        + (f"<div class='card' id='lv-threads-card'><h2>Цитаты источника</h2><div id='lv-threads'>{threads_html}</div></div>"
           if threads_html else
           "<div class='card' id='lv-threads-card' style='display:none'><h2>Цитаты источника</h2><div id='lv-threads'></div></div>")
        + f"<div class='card'><h2>Устав переноса</h2>"
        "<div class='row'>"
        f"<div><label>Целевой канал здесь</label><select name='target_channel'>{tc_opts}</select></div>"
        "<div><label>Режим</label><select name='mode'><option value='both'>История + зеркалирование</option>"
        "<option value='once'>Только история</option><option value='mirror'>Только зеркалирование</option></select></div>"
        "<div><label>Сколько последних сообщений переносить из каждого канала/публикации</label>"
        "<input type='number' name='history_limit' min='0' max='100000' value='100'>"
        "<span class='sub'>0 — вся история без ограничения</span></div>"
        "</div>"
        "<label>URL вебхука целевого канала (необязательно: создадим сами)</label>"
        "<input type='text' name='webhook_url' placeholder='https://discord.com/api/webhooks/…'>"
        f"<label><input type='checkbox' name='attachments' checked> Переносить вложения (до 10 на сообщение, до 25 МБ)</label>"
        f"<label><input type='checkbox' name='embeds' checked> Переносить embeds</label>"
        "<p><button name='action' value='create'>Создать задачу</button> "
        "<button class='ghost' name='action' value='probe'>🔍 Проверить видимость источника</button></p>"
        "<p class='sub'>Для форума можно перенести только отмеченные публикации; если не отмечено ни одной — "
        "переносится весь форум. Список публикаций подгружается без перезагрузки страницы.</p></div>"
        "</form>"
        + "<script>function lvThreads(sid,cid){var card=document.getElementById('lv-threads-card');"
        "var box=document.getElementById('lv-threads');"
        "var h1=document.getElementsByName('source_channel')[0];"
        "var h2=document.getElementsByName('ui_sch')[0];"
        "if(!cid||cid==='0'){card.style.display='none';box.innerHTML='';"
        "if(h1){h1.value='0';}if(h2){h2.value='0';}return;}"
        "if(h1){h1.value=cid;}if(h2){h2.value=cid;}"
        "card.style.display='';box.innerHTML='<p class=\"sub\">Загружаю публикации…</p>';"
        "fetch('/guild/" + str(gid) + "/migration/threads?src='+sid+'&sch='+cid)"
        ".then(function(r){return r.text();}).then(function(t){box.innerHTML=t;})"
        ".catch(function(){box.innerHTML='<p class=\"sub\">Не удалось загрузить публикации.</p>';});}</script>"
    )
    return web.Response(text=dash_page("Перенос", body, session, guild_tabs(gid, "migration"), guild=guild), content_type="text/html")

# --------------------------------------------------------------------------- #
# Веб-дашборд настройки бота (встроенный, aiohttp)
# --------------------------------------------------------------------------- #
def _dash_port() -> int:
    """Порт панели: DASHBOARD_PORT, а на хостинге без него — открытый Primary-порт SERVER_PORT."""
    return int(os.environ.get("DASHBOARD_PORT") or os.environ.get("SERVER_PORT") or "8080")

def _dash_url(port: int) -> str:
    url = os.environ.get("DASHBOARD_URL", f"http://127.0.0.1:{port}").rstrip("/")
    if url.endswith("/callback"):  # защита от частой ошибки: /callback бот добавляет сам
        url = url[: -len("/callback")]
    return url

DASH_PORT = _dash_port()
DASH_URL = _dash_url(DASH_PORT)
DASH_SECRET = os.environ.get("DASHBOARD_SECRET", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
OAUTH_REDIRECT = DASH_URL + "/callback"
COOKIE_NAME = "lv_dash"
SESSION_TTL = 60 * 60 * 12
MANAGE_GUILD = 0x20
APPLY_LABELS = {"both": "XP и дата-коги", "credits": "дата-коги", "xp": "XP"}
_DASH_STARTED = False
_GUILD_CACHE: dict = {}
WEB_DIR = os.path.join(BASE_DIR, "web")  # папка статики дашборда (фон и т.п.)

def _int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _float(value, default=1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def _dash_secret() -> str:
    return DASH_SECRET or hashlib.sha256(("lv|" + DISCORD_CLIENT_SECRET).encode()).hexdigest()

def _client_id() -> str:
    token = get_token() or ""
    seg = token.split(".")[0]
    if seg:
        try:
            return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)).decode()
        except Exception:
            pass
    return os.environ.get("DISCORD_CLIENT_ID", "")

def _sign(payload: str) -> str:
    return hmac.new(_dash_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()

def session_encode(data: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    return payload + "." + _sign(payload)

def session_decode(value: Optional[str]) -> Optional[dict]:
    if not value or "." not in value:
        return None
    payload, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except Exception:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return data

def csrf_token(session: dict) -> str:
    return hmac.new(_dash_secret().encode(), ("csrf|" + str(session.get("uid"))).encode(), hashlib.sha256).hexdigest()[:32]

async def fetch_my_guilds(access_token: str) -> list:
    """Серверы пользователя с маской прав (кэш 60 с)."""
    now = time.time()
    hit = _GUILD_CACHE.get(access_token)
    if hit and now - hit[0] < 60:
        return hit[1]
    guilds = []
    try:
        async with aiohttp.ClientSession() as ses:
            async with ses.get(
                "https://discord.com/api/v10/users/@me/guilds",
                headers={"Authorization": f"Bearer {access_token}"},
            ) as resp:
                if resp.status == 200:
                    guilds = await resp.json()
    except Exception as exc:
        log.warning("Дашборд: не удалось получить серверы пользователя: %s", exc)
        if hit:
            return hit[1]
    _GUILD_CACHE[access_token] = (now, guilds)
    return guilds

def is_web_admin(uid: int, guild: discord.Guild, entry: Optional[dict]) -> bool:
    """Зеркало is_bot_admin для веба: владелец приложения → admin_ids → владелец сервера → manage_guild."""
    if bot.owner_id and uid == bot.owner_id:
        return True
    cfg = get_config(guild.id)
    if uid in cfg.get("admin_ids", []):
        return True
    if guild.owner_id == uid:
        return True
    if entry:
        try:
            return bool(int(entry.get("permissions", 0)) & MANAGE_GUILD)
        except Exception:
            return False
    return False

DASH_CSS = """
:root{--bg:#0e1013;--panel:rgba(23,15,16,.93);--panel2:#1c2126;--line:#4a5158;--accent:#a3162e;--accent2:#5d0c17;--gold:#c8a24a;--ivory:#e8ebee;--muted:#98a1a8;--text:var(--ivory)}
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;color:var(--text);font:16px/1.6 'Rajdhani','Segoe UI',sans-serif;background-color:var(--bg);background-image:radial-gradient(1100px 480px at 85% -8%,rgba(163,22,46,.18),transparent 70%),radial-gradient(900px 420px at 8% -4%,rgba(200,162,74,.07),transparent 70%),linear-gradient(180deg,#171b20,#0e1013 45%,#0a0c0e);background-attachment:fixed}
a{color:var(--gold);text-decoration:none}a:hover{text-decoration:underline}
h1,h2,h3,header.nav .brand{font-family:'Orbitron','Rajdhani',sans-serif;color:var(--gold);letter-spacing:.08em;font-weight:700}
header.nav{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:10px 26px;background:linear-gradient(180deg,rgba(34,38,44,.96),rgba(17,20,24,.96));border-bottom:1px solid rgba(200,162,74,.35);position:sticky;top:0;backdrop-filter:blur(10px);z-index:20;box-shadow:0 6px 24px rgba(0,0,0,.45),inset 0 1px 0 rgba(255,255,255,.06)}
header.nav .brand{display:flex;align-items:center;gap:10px;font-size:15px;text-transform:uppercase}
header.nav .brand img{width:36px;height:36px;border-radius:8px;border:1px solid rgba(200,162,74,.65);box-shadow:0 0 0 3px rgba(163,22,46,.25),0 4px 12px rgba(0,0,0,.5);object-fit:cover}
header.nav .brand:hover{text-decoration:none;filter:brightness(1.12)}
nav.mainnav{display:flex;gap:2px;flex-wrap:wrap;align-items:center}
nav.mainnav a{padding:7px 13px;border-radius:6px;color:var(--muted);font-family:'Rajdhani',sans-serif;font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.12em;border:1px solid transparent;transition:color .25s,background .25s,border-color .25s}
nav.mainnav a:hover{color:var(--ivory);background:rgba(163,22,46,.18);text-decoration:none}
nav.mainnav a.active{color:#e6c87e;background:linear-gradient(180deg,rgba(163,22,46,.4),rgba(93,12,23,.22));border-color:rgba(200,162,74,.4);box-shadow:inset 0 -2px 0 var(--accent),inset 0 1px 0 rgba(255,255,255,.08)}
header.nav .spacer{flex:1}header.nav .user{color:var(--muted);font-size:14px}
.guildpill{max-width:230px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;border-color:rgba(200,162,74,.4);color:var(--ivory)}
main.page{min-height:62vh}
.wrap{max-width:1280px;margin:0 auto;padding:26px 26px 64px}
.flashwrap{max-width:1280px;margin:0 auto;padding:18px 26px 0}
#lv-root{transition:opacity .19s ease,transform .19s ease}
#lv-root .capsule,#lv-root .fcard,#lv-root .card,#lv-root .hero-txt{animation:rise .5s ease both}
#lv-root .fcard:nth-child(2){animation-delay:.05s}#lv-root .fcard:nth-child(3){animation-delay:.1s}#lv-root .fcard:nth-child(4){animation-delay:.15s}#lv-root .fcard:nth-child(5){animation-delay:.2s}#lv-root .fcard:nth-child(6){animation-delay:.25s}#lv-root .fcard:nth-child(7){animation-delay:.3s}#lv-root .fcard:nth-child(8){animation-delay:.35s}#lv-root .fcard:nth-child(9){animation-delay:.4s}#lv-root .fcard:nth-child(10){animation-delay:.45s}#lv-root .fcard:nth-child(11){animation-delay:.5s}#lv-root .fcard:nth-child(12){animation-delay:.55s}
@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
.capsule{background:linear-gradient(180deg,rgba(30,34,40,.94),rgba(18,21,25,.97));border:1px solid var(--line);border-radius:14px;box-shadow:0 18px 50px rgba(0,0,0,.55),inset 0 1px 0 rgba(255,255,255,.05);overflow:hidden}
.capsule-head{display:flex;align-items:center;gap:16px;padding:18px 24px;background:linear-gradient(90deg,rgba(163,22,46,.32),rgba(30,34,40,.3) 55%,rgba(30,34,40,0));border-bottom:1px solid var(--line)}
.capsule-head .ci{font-size:30px;line-height:1;filter:drop-shadow(0 0 12px rgba(163,22,46,.55))}
.capsule-head .ct h2{margin:0;font-size:19px;text-transform:uppercase}
.capsule-head .ct .sub{margin:2px 0 0}
img.cap-art{margin-left:auto;width:216px;height:92px;object-fit:cover;border-radius:10px;border:1px solid rgba(200,162,74,.5);box-shadow:0 8px 20px rgba(0,0,0,.55),0 0 0 3px rgba(90,96,104,.25);transition:transform .35s ease,box-shadow .35s ease}
img.cap-art:hover{transform:scale(1.05);box-shadow:0 10px 26px rgba(0,0,0,.6),0 0 0 3px rgba(200,162,74,.3)}
.capsule-body{padding:22px 24px 26px}
@media(max-width:700px){img.cap-art{display:none}.capsule-head{padding:16px 18px}.capsule-body{padding:16px 14px 20px}}
.hero{position:relative;display:grid;grid-template-columns:1.05fr .95fr;gap:40px;align-items:center;max-width:1280px;margin:0 auto;padding:60px 26px 84px}
.hero::before{content:'';position:absolute;right:-70px;top:-60px;width:360px;height:360px;border:2px dashed rgba(200,162,74,.2);border-radius:50%;pointer-events:none}
.hero::after{content:'';position:absolute;left:-40px;bottom:-30px;width:220px;height:220px;border:2px solid rgba(163,22,46,.26);border-radius:50%;pointer-events:none}
.hero-txt h1{font-size:40px;line-height:1.15;margin:0 0 14px;color:var(--ivory);text-transform:uppercase}
.hero-txt h1 .hgold{color:var(--gold)}
.hero-txt .lead{color:var(--muted);font-size:17px;max-width:560px;margin:0 0 26px}
.hero-cta{display:flex;gap:12px;flex-wrap:wrap}
.herostats{display:flex;gap:10px;flex-wrap:wrap;margin-top:24px}
.herostats .pill{padding:6px 14px;font-size:13px;background:rgba(28,33,38,.8);border-color:rgba(200,162,74,.3);color:var(--ivory)}
.herostats .pill b{color:var(--gold)}
.hero-art{position:relative;display:flex;justify-content:center}
.frame{width:min(430px,100%);border-radius:14px;overflow:hidden;border:1px solid rgba(200,162,74,.55);box-shadow:0 24px 60px rgba(0,0,0,.6),0 0 0 4px rgba(90,96,104,.3),0 0 46px rgba(163,22,46,.3);background:#15181c}
.frame img{display:block;width:100%;height:330px;object-fit:cover}
.frame .cap{display:flex;justify-content:space-between;gap:10px;padding:10px 16px;background:linear-gradient(180deg,#1e2228,#14171b);border-top:1px solid var(--line);color:var(--muted);font-size:12.5px;text-transform:uppercase;letter-spacing:.06em}
.lower{background:linear-gradient(180deg,#14171b,#101317 60%,#0b0d10);border-top:1px solid rgba(200,162,74,.14);padding-bottom:34px}
.wave{display:block;width:100%;height:84px;margin-top:-42px}
@media(max-width:960px){.hero{grid-template-columns:1fr;padding:44px 20px 64px}.hero-txt h1{font-size:30px}}
.fsec{max-width:1280px;margin:0 auto;padding:6px 26px 26px}
.fsec h2{text-align:center;font-size:27px;margin:0 0 6px;text-transform:uppercase}
.fsec .sub{text-align:center;margin:0 0 26px;color:var(--muted)}
.fgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(235px,1fr));gap:14px}
.fcard{display:flex;flex-direction:column;gap:8px;background:linear-gradient(180deg,rgba(30,34,40,.9),rgba(19,22,26,.94));border:1px solid var(--line);border-radius:12px;padding:20px;transition:transform .25s,border-color .25s,box-shadow .25s}
.fcard:hover{transform:translateY(-5px);border-color:rgba(200,162,74,.55);box-shadow:0 14px 34px rgba(0,0,0,.5)}
.fcard .ico{width:46px;height:46px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:22px;background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 6px 16px rgba(93,12,23,.45),inset 0 1px 0 rgba(255,255,255,.12)}
.fcard .ico.g{background:linear-gradient(135deg,#c8a24a,#6e551f);box-shadow:0 6px 16px rgba(110,85,31,.45)}
.fcard .ico.d{background:linear-gradient(135deg,#2a3038,#1a1e23);border:1px solid var(--line);box-shadow:none}
.fcard h3{margin:4px 0 0;font-size:15.5px;color:var(--ivory);letter-spacing:.04em}
.fcard p{margin:0;color:var(--muted);font-size:14px;flex:1}
.fcard a.more{color:var(--gold);font-size:14px;font-weight:600}
.homegrid{display:grid;grid-template-columns:1fr 1fr;gap:16px;max-width:1280px;margin:0 auto;padding:0 26px 10px;align-items:start}
@media(max-width:900px){.homegrid{grid-template-columns:1fr}}
.layout{display:grid;grid-template-columns:230px minmax(0,1fr) 310px;gap:16px;align-items:start}
.layout .side .card{margin:0 0 16px}.layout .col-main{min-width:0}.span-all{grid-column:1/-1}
@media(max-width:1150px){.layout{grid-template-columns:1fr}.layout .side{display:grid;grid-template-columns:1fr 1fr;gap:16px}.layout .side .card{margin:0}}
.card{background:rgba(18,21,25,.6);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin:0 0 16px;box-shadow:inset 0 1px 0 rgba(255,255,255,.04)}
.card h2{margin:0 0 4px;font-size:19px}.card .sub{color:var(--muted);font-size:13px;margin-bottom:14px}
.sub{color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.stat{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px 14px;transition:transform .25s,border-color .25s}
.stat:hover{transform:translateY(-3px);border-color:var(--gold)}
.stat b{display:block;font-size:19px;color:var(--ivory);font-family:'Orbitron',sans-serif}.stat span{color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}
label{display:block;margin:10px 0 4px;color:var(--muted);font-size:14px}
input[type=text],input[type=number],textarea,select{width:100%;background:#12151a;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:9px 12px;font:inherit;transition:border-color .25s,box-shadow .25s}
input:focus,textarea:focus,select:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(163,22,46,.25)}
select[multiple]{min-height:110px}textarea{min-height:90px}
input[type=checkbox]{width:18px;height:18px;accent-color:var(--accent)}
.row{display:flex;gap:10px;flex-wrap:wrap}.row>*{flex:1;min-width:150px}
button,.btn{background:linear-gradient(180deg,#b01c33,#7d1220);color:#fff;border:1px solid #3f0a12;border-radius:8px;padding:10px 20px;font:inherit;font-weight:600;letter-spacing:.04em;cursor:pointer;display:inline-block;transition:transform .2s,box-shadow .2s,filter .2s;box-shadow:0 4px 14px rgba(0,0,0,.4),inset 0 1px 0 rgba(255,255,255,.14)}
button:hover,.btn:hover{transform:translateY(-1px);filter:brightness(1.14);box-shadow:0 6px 18px rgba(163,22,46,.4),inset 0 1px 0 rgba(255,255,255,.14);text-decoration:none}
button.ghost,.btn.ghost{background:linear-gradient(180deg,#22262c,#191d22);border:1px solid var(--line);color:var(--muted);box-shadow:inset 0 1px 0 rgba(255,255,255,.05)}
button.ghost:hover,.btn.ghost:hover{color:var(--gold);border-color:var(--gold)}
button.danger{background:linear-gradient(180deg,#6e2318,#40130d);border-color:#2c0d08}
table{width:100%;border-collapse:collapse;margin:8px 0}td,th{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;font-size:14px}
th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.8px;font-family:'Orbitron','Rajdhani',sans-serif}
.flash{background:rgba(61,107,58,.18);border:1px solid #3d6b3a;color:#cde8cb;border-radius:10px;padding:10px 14px;margin:0 0 16px}
.flash.err{background:rgba(122,58,49,.18);border-color:#7a3a31;color:#e8c9c4}
#lv-toast{position:fixed;right:24px;bottom:24px;max-width:400px;background:linear-gradient(180deg,#2a1215,#1a0d0f);border:1px solid var(--gold);color:var(--ivory);border-radius:10px;padding:14px 18px;box-shadow:0 12px 30px rgba(0,0,0,.55);opacity:0;transform:translateY(18px);transition:opacity .35s ease,transform .35s ease;pointer-events:none;z-index:60}
#lv-toast.show{opacity:1;transform:none}
#lv-toast.err{border-color:var(--accent);color:#f4c9c9}
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;background:var(--panel2);border:1px solid var(--line);color:var(--muted)}
.pill.on{color:#cde8cb;border-color:#3d6b3a}.pill.off{color:#e8c9c4;border-color:#7a3a31}
footer{color:#6f777e;text-align:center;padding:26px;font-size:12px;letter-spacing:.05em}
code{background:#12151a;border:1px solid var(--line);border-radius:6px;padding:1px 6px}
.quotebox{font-family:'Rajdhani',sans-serif;font-weight:500;font-size:20px;line-height:1.5;color:var(--ivory);margin:8px 0;border-left:2px solid rgba(200,162,74,.4);padding-left:14px}
.quotebox .src{display:block;color:var(--muted);font-size:13px;margin-top:12px}
.audit td{font-size:12.5px;color:var(--muted)}.audit td b{color:var(--text)}
.rrlist{margin:0;padding-left:18px}.rrlist li{margin:2px 0}
"""

def _side_stats(guild: discord.Guild) -> str:
    bots = sum(1 for m in guild.members if m.bot)
    rows = [
        (str(guild.member_count or len(guild.members)), "участников"),
        (str(bots), "ботов"),
        (str(len(guild.roles)), "ролей"),
        (str(len(guild.text_channels)), "текстовых каналов"),
    ]
    stats = "".join(f"<div class='stat'><b>{v}</b><span>{k}</span></div>" for v, k in rows)
    return f"<div class='card'><h2>Кузница</h2><div class='sub'>кузница {guild.name}</div><div class='grid'>{stats}</div></div>"

def _side_quote(guild: discord.Guild) -> str:
    pool = build_pool(guild.id, "any", get_config(guild.id)["quotes_mode"])
    if not pool:
        return "<div class='card'><h2>Голос Машины</h2><p class='sub'>Цитаты пусты.</p></div>"
    q = random.choice(pool)[1]
    era = ERA_LABELS.get(q.get("era", "both"), ERA_LABELS["both"])
    return (
        "<div class='card'><h2>Голос Машины</h2><div class='sub'>слово из литаний кузни</div>"
        f"<blockquote class='quotebox'>«{q['text']}»"
        f"<span class='src'>— {q.get('author') or 'Неизвестный автор'} • {q.get('source') or ''} • {era}</span></blockquote>"
        "<form method='get'><p><button class='ghost'>⚙ Другое слово</button></p></form></div>"
    )

def _audit_panel(guild: discord.Guild) -> str:
    rows = [a for a in AUDIT if a.get("guild") == str(guild.id)][-12:][::-1]
    if not rows:
        return ("<div class='card'><h2>Хроника кузни</h2><div class='sub'>аудит: последние команды и правки</div>"
                "<p>Хроника пуста: она наполнится после первых команд и правок в панели.</p></div>")
    lines = ""
    for a in rows:
        m = guild.get_member(int(a["actor"])) if str(a["actor"]).isdigit() else None
        who = m.display_name if m else a["actor"]
        when = time.strftime("%d.%m %H:%M", time.localtime(a["ts"]))
        lines += f"<tr><td>{when}</td><td><b>{who}</b></td><td>{a['action']}</td><td>{a.get('detail', '')}</td></tr>"
    return ("<div class='card'><h2>Хроника кузни</h2><div class='sub'>аудит: последние 12 событий кузни</div>"
            f"<table class='audit'><tr><th>Время</th><th>Кто</th><th>Действие</th><th>Детали</th></tr>{lines}</table></div>")

def guild_layout(guild: discord.Guild, body: str) -> str:
    return (
        f"<div class='layout'><div class='side'>{_side_stats(guild)}</div>"
        f"<div class='col-main'>{body}</div>"
        f"<div class='side'>{_side_quote(guild)}</div>"
        f"<div class='span-all'>{_audit_panel(guild)}</div></div>"
    )

LV_JS = """<script>
(function(){
  var root=document.getElementById('lv-root');
  if(!root)return;
  function setTab(){var a=root.querySelector('nav.mainnav a.active');document.body.dataset.tab=a?(a.getAttribute('data-tab')||''):'';}
  setTab();
  var toast=document.getElementById('lv-toast'),tt=null;
  function show(msg,kind){toast.textContent=msg;toast.className='show '+(kind||'');clearTimeout(tt);tt=setTimeout(function(){toast.className='';},4600);}
  function frag(u){return u+(u.indexOf('?')>=0?'&':'?')+'frag=1';}
  function swap(html){root.style.opacity=0;root.style.transform='translateY(10px)';setTimeout(function(){root.innerHTML=html;setTab();root.style.opacity=1;root.style.transform='';window.scrollTo({top:0,behavior:'smooth'});},190);}
  function nav(u,push){fetch(frag(u),{credentials:'same-origin'}).then(function(r){return r.text();}).then(function(h){swap(h);if(push)history.pushState({u:u},' ',u);}).catch(function(){location.href=u;});}
  window.addEventListener('popstate',function(){nav(location.pathname+location.search,false);});
  document.addEventListener('click',function(e){
    var a=e.target.closest?e.target.closest('a'):null;if(!a)return;
    var h=a.getAttribute('href')||'';
    if(h.indexOf('/guild/')===0||h==='/guilds'){e.preventDefault();nav(h,true);}
  });
  document.addEventListener('submit',function(e){
    var f=e.target;if(!f.closest||!f.closest('#lv-root'))return;
    if((f.method||'get').toLowerCase()==='get'){
      e.preventDefault();
      var q=new URLSearchParams(new FormData(f)).toString();
      nav((f.getAttribute('action')||location.pathname)+(q?'?'+q:''),true);
      return;
    }
  });
  document.addEventListener('click',function(e){
    var t=e.target;
    if(t&&t.classList&&t.classList.contains('core-add')){
      var inp=document.querySelector("input[name='core_blocks']");
      if(!inp)return;
      var id=t.getAttribute('data-id')||'';
      var cur=(inp.value||'').split(/[^0-9]+/).filter(function(x){return x;});
      if(cur.indexOf(id)<0){cur.push(id);}
      inp.value=cur.join(',');
    }
  });
  function blkform(){return document.getElementById('blk-form');}
  function renumber(){
    var f=blkform();if(!f)return;
    f.querySelectorAll('.row').forEach(function(r,i){
      var c=r.querySelector("input[name='canon']"),s=r.querySelector("input[name='self']");
      if(c)c.value='c'+i; if(s)s.value='s'+i;
    });
  }
  document.addEventListener('click',function(e){
    if(e.target&&e.target.id==='addrow'){
      var f=blkform();if(!f)return;
      var tpl=document.getElementById('blk-row-tpl');if(!tpl)return;
      var node=tpl.content.cloneNode(true);
      f.insertBefore(node,f.querySelector('#addrow').closest('p'));
      renumber();
    }
    if(e.target&&e.target.classList&&e.target.classList.contains('rmrow')){
      var row=e.target.closest('.row');if(row){row.remove();renumber();}
    }
  });
  document.addEventListener('submit',function(e){
    var f=e.target;
    if(f&&f.id==='blk-form'){
      e.preventDefault();
      renumber();
      var fd=new FormData(f);
      var sb=e.submitter;
      if(sb&&sb.name){fd.append(sb.name,sb.value);}
      var y=window.scrollY||window.pageYOffset||0;
      fetch(f.getAttribute('action')||location.pathname,{method:'POST',body:fd,credentials:'same-origin'})
        .then(function(r){
          var u=new URL(r.url||location.href,location.origin);
          var msg=u.searchParams.get('msg'),kind=u.searchParams.get('kind');
          var eu=u.pathname+(u.search?(u.search+'&'):'?')+'only=editor';
          return fetch(eu,{credentials:'same-origin'}).then(function(rr){return rr.text();})
            .then(function(h){
              var old=document.getElementById('blk-editor');
              if(old){old.outerHTML=h;}
              window.scrollTo(0,y);
              show(msg||'Готово',kind);
            });
        }).catch(function(){f.submit();});
    }
  });
  var m=location.search.match(/[?&]msg=([^&]+)/);
  if(m){show(decodeURIComponent(m[1].replace(/\\+/g,' ')),(location.search.match(/[?&]kind=([^&]+)/)||[])[1]);
    var u=new URL(location.href);u.searchParams.delete('msg');u.searchParams.delete('kind');history.replaceState({},'',u.pathname+(u.search||''));}
})();
</script>"""

_REQ = contextvars.ContextVar("_REQ", default=None)

@web.middleware
async def _dash_log_redirect(request, handler):
    """r31: результат каждого POST панели виден в консоли хостинга
    (текст вспышки из Location) — диагностика без скриншотов."""
    resp = await handler(request)
    if request.method == "POST" and request.path.startswith("/guild/") and isinstance(resp, web.HTTPRedirection):
        log.info("Панель: POST %s -> %s", request.path, str(resp.location or resp.headers.get("Location", ""))[:220])
    return resp

@web.middleware
async def _dash_safe(request, handler):
    """r29: любой необработанный сбой обработчика панели виден пользователю
    вспышкой с причиной (и в логе), а не безликой страницей 500."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as exc:
        if not request.path.startswith("/guild/"):
            raise
        gid = request.match_info.get("gid", "")
        tab = request.path.rstrip("/").rsplit("/", 1)[-1] or ""
        log.exception("Панель: сбой %s %s", request.method, request.path)
        reason = f"{type(exc).__name__}: {exc}"[:110]
        raise web.HTTPFound(
            f"/guild/{gid}/{tab}?msg=Сбой+{request.method}:+"
            + reason.replace(" ", "+").replace("&", "+").replace("\n", "+") + "&kind=err")

@web.middleware
async def _req_middleware(request, handler):
    _REQ.set(request)
    return await handler(request)

TAB_META = {
    "": ("⚙", "Кузница", "сводка настроек и состояние машины сервера", "/static/art_forge.jpg"),
    "quotes": ("📜", "Книга литаний", "полный список цитат, поиск и назначение каналов", "/static/art_litany.jpg"),
    "progress": ("🔩", "Ранги познания", "ранги, опыт и дата-коги участников", "/static/art_cog.jpg"),
    "board": ("🏆", "Рейтинг активности", "циклы день и неделя, награды в дата-когах, архив", "/static/art_cog.jpg"),
    "dossier": ("📜", "Досье", "анкеты персонажей: блоки-дерево, ядро, допуск канона", "/static/art_litany.jpg"),
    "news": ("📰", "Новости", "черновики, отложенные публикации, правка ленты", "/static/art_data.jpg"),
    "shop": ("⚒", "Лавка кузни", "товары, цены и награды за дата-коги", "/static/art_ark.jpg"),
    "loot": ("⚱", "Тех-арки", "ларцы реквизиции и их содержимое", "/static/art_ark.jpg"),
    "giveaways": ("🎁", "Жребий конклава", "розыгрыши дата-когов и реликвий", "/static/art_conclave.jpg"),
    "welcome": ("🛎", "Ритуал посвящения", "встреча новичков в канале посвящений", "/static/art_litany.jpg"),
    "admins": ("⚜", "Магосы кузни", "кто вправе настраивать бота и панель", "/static/art_conclave.jpg"),
    "migration": ("📖", "Передача данных", "перенос сообщений между серверами", "/static/art_data.jpg"),
    "tickets": ("🎫", "Тикеты", "панель поддержки, приватные каналы, транскрипты", "/static/art_data.jpg"),
    "audit": ("🛡", "Аудит кузни", "журнал событий и отчёт безопасности 0–100", "/static/art_cog.jpg"),
}

def _active_tab(tabs: str) -> str:
    """Ключ активного раздела из строки вкладок (для шапки-капсулы)."""
    m = re.search(r"data-tab='([^']*)'\s+class='active'", tabs or "")
    return m.group(1) if m else ""

def _capsule(key: str, inner: str) -> str:
    """Раздел панели как отдельная капсула: шапка с артом + содержимое без промежутка."""
    icon, title, sub, art = TAB_META.get(key, ("⚙", "Кузница", "обзор настроек", "/static/art_forge.jpg"))
    return (
        f"<section class='capsule'><div class='capsule-head'><span class='ci'>{icon}</span>"
        f"<div class='ct'><h2>{title}</h2><div class='sub'>{sub}</div></div>"
        f"<img class='cap-art' src='{art}' alt='{title}'>"
        f"</div><div class='capsule-body'>{inner}</div></section>"
    )

def _nav_header(session: Optional[dict], tabs: str, guild: Optional[discord.Guild]) -> str:
    """Верхняя навигационная полоса в духе carl.gg: эмблема, разделы, вход."""
    if session:
        user_html = (
            f'<span class="user">{session.get("uname", "")}</span> '
            f'<a class="btn ghost" style="padding:6px 14px" href="/logout">Выйти</a>'
        )
    else:
        user_html = '<a class="btn" style="padding:6px 14px" href="/login">Войти через Discord</a>'
    if guild is not None:
        brand_href = f"/guild/{guild.id}/"
        pill = f"<span class='pill guildpill'>⚙ {guild.name}</span>"
    else:
        brand_href = "/guilds"
        pill = ""
    nav = tabs or ("<a href='/guilds' data-tab='guilds' class='active'>Кузни</a>" if session else "")
    build_pill = (f"<span class='pill' style='margin-left:8px;font-size:12px'>сборка {BOT_BUILD}</span>")
    return (
        f"<header class='nav'><a class='brand' href='{brand_href}'>"
        f"<img src='/static/avatar.png' alt=''> <span>{BOT_DISPLAY_NAME}</span></a>{pill}{build_pill}"
        f"<nav class='mainnav'>{nav}</nav><span class='spacer'></span>{user_html}</header>"
    )

def dash_page(title: str, body: str, session: Optional[dict] = None, tabs: str = "", guild: Optional[discord.Guild] = None) -> str:
    req = _REQ.get(None)
    active = _active_tab(tabs)
    if guild is not None:
        inner = _capsule(active, guild_layout(guild, body)) if active else body
    elif body.lstrip().startswith("<section class='hero'"):
        inner = body
    else:
        inner = f"<div class='wrap'>{body}</div>"
    core = f"{_nav_header(session, tabs, guild)}<main class='page'>{inner}</main>"
    if req is not None and req.query.get("frag") == "1":
        return core
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title>"
        "<link rel='icon' href='/favicon.ico'>"
        "<link rel='preconnect' href='https://fonts.googleapis.com'>"
        "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
        "<link href='https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Rajdhani:wght@400;500;600;700&display=swap' rel='stylesheet'>"
        f"<style>{DASH_CSS}</style></head><body>"
        f"<div id='lv-root'>{core}</div>"
        "<div id='lv-toast' role='status'></div>"
        f"<footer>{FOOTER_TEXT} • изменения применяются мгновенно • сборка {BOT_BUILD}</footer>"
        + LV_JS + "</body></html>"
    )

def guild_tabs(gid: int, active: str) -> str:
    items = [
        ("", "Кузница"), ("quotes", "Литании"), ("progress", "Ранги"), ("board", "Рейтинг"), ("dossier", "Досье"), ("news", "Новости"),
        ("shop", "Лавка"),
        ("loot", "Тех-арки"), ("giveaways", "Жребий"), ("welcome", "Посвящение"),
        ("admins", "Магосы"), ("migration", "Перенос"),
        ("tickets", "Тикеты"), ("audit", "Аудит"),
    ]
    return "".join(
        f"<a href='/guild/{gid}/{key}' data-tab='{key}' class='{'active' if key == active else ''}'>{label}</a>"
        for key, label in items
    )

def flash_block(request) -> str:
    msg = request.query.get("msg", "")
    kind = request.query.get("kind", "ok")
    if not msg:
        return ""
    return f"<div class='flash {'err' if kind == 'err' else ''}'>{msg}</div>"

def csrf_input(session: dict) -> str:
    return f"<input type='hidden' name='csrf' value='{csrf_token(session)}'>"

def opt(selected, value) -> str:
    return " selected" if str(selected) == str(value) else ""

async def dash_session(request) -> Optional[dict]:
    return session_decode(request.cookies.get(COOKIE_NAME))

async def dash_guard(request, gid: int):
    session = await dash_session(request)
    if not session:
        raise web.HTTPFound("/login?next=" + urllib.parse.quote(str(request.path)))
    guild = bot.get_guild(gid)
    if guild is None:
        raise web.HTTPNotFound(text="Бот не находится на этом сервере.")
    guilds = await fetch_my_guilds(session.get("token", ""))
    entry = next((g for g in guilds if str(g.get("id")) == str(gid)), None)
    if not is_web_admin(session.get("uid", 0), guild, entry):
        raise web.HTTPForbidden(text="Недостаточно прав: нужен статус администратора бота на этом сервере.")
    return session, guild, entry

async def dash_index(request):
    if await dash_session(request):
        raise web.HTTPFound("/guilds")
    feats = (
        "<div class='fcard'><span class='ico'>📜</span><h3>Литании в каналах</h3>"
        "<p>Бот слушает живые разговоры в избранных каналах и отмечает достойные реплики цитатами.</p></div>"
        "<div class='fcard'><span class='ico g'>⚙</span><h3>Ранги познания</h3>"
        "<p>Опыт за слова, ранги роста, дата-коги и награды за усердие — всё прозрачно в хронике.</p></div>"
        "<div class='fcard'><span class='ico d'>⚒</span><h3>Лавка и тех-арки</h3>"
        "<p>Тратьте дата-коги на дары лавки, вскрывайте ларцы реквизиции и разыгрывайте жребий конклава.</p></div>"
    )
    body = (
        "<section class='hero'><div class='hero-txt'>"
        "<h1>Кузница <span class='hgold'>Адептус Механикус</span></h1>"
        "<p class='lead'>Панель кузни: литании во время живых бесед, ранги познания и дата-коги, "
        "лавка даров, тех-арки, жребий конклава и ритуал посвящения — "
        "всё в браузере, без единой команды. Вход — только для магосов кузни.</p>"
        "<div class='hero-cta'><a class='btn' href='/login'>Войти через Discord</a></div></div>"
        "<div class='hero-art'><div class='frame'><img src='/static/art_forge.jpg' alt='Кузница Марса'>"
        "<span class='cap'><span>Adeptus Mechanicus</span><span>Кузница Бога-Машины</span></span></div></div>"
        "</section>"
        "<div class='lower'><svg class='wave' viewBox='0 0 1440 90' preserveAspectRatio='none' aria-hidden='true'>"
        "<path fill='#14171b' d='M0,52 C240,92 480,12 720,42 C960,72 1200,22 1440,56 L1440,90 L0,90 Z'></path></svg>"
        "<div class='fsec'><h2>Почему кузница?</h2>"
        "<div class='sub'>три столпа, на которых держится машина сервера</div>"
        f"<div class='fgrid'>{feats}</div></div></div>"
    )
    return web.Response(text=dash_page("Adeptus Mechanicus — вход в кузницу", body), content_type="text/html")

async def dash_login(request):
    cid = _client_id()
    if not cid or not DISCORD_CLIENT_SECRET:
        return web.Response(
            text=dash_page("Ошибка", "<div class='card'><h2>Дашборд не настроен</h2>"
                           "<p>Задайте на хостинге переменные окружения <code>DISCORD_CLIENT_SECRET</code> "
                           "и <code>DASHBOARD_URL</code>, а в Discord Developer Portal (OAuth2 → Redirects) "
                           f"добавьте <code>{OAUTH_REDIRECT}</code>.</p></div>"),
            content_type="text/html", status=500,
        )
    params = urllib.parse.urlencode({
        "client_id": cid,
        "redirect_uri": OAUTH_REDIRECT,
        "response_type": "code",
        "scope": "identify guilds",
        "state": _secrets.token_hex(8),
    })
    raise web.HTTPFound("https://discord.com/oauth2/authorize?" + params)

async def dash_callback(request):
    code = request.query.get("code", "")
    if not code:
        raise web.HTTPFound("/")
    async with aiohttp.ClientSession() as ses:
        async with ses.post(
            "https://discord.com/api/v10/oauth2/token",
            data={
                "client_id": _client_id(),
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": OAUTH_REDIRECT,
            },
        ) as resp:
            if resp.status != 200:
                raise web.HTTPFound("/")
            tok = await resp.json()
        async with ses.get(
            "https://discord.com/api/v10/users/@me",
            headers={"Authorization": f"Bearer {tok['access_token']}"},
        ) as resp:
            me = await resp.json()
    session = {
        "uid": int(me["id"]),
        "uname": me.get("username", ""),
        "token": tok["access_token"],
        "exp": int(time.time()) + SESSION_TTL,
    }
    resp = web.HTTPFound("/guilds")
    resp.set_cookie(COOKIE_NAME, session_encode(session), max_age=SESSION_TTL, httponly=True, samesite="Lax")
    raise resp

async def dash_logout(request):
    resp = web.HTTPFound("/")
    resp.del_cookie(COOKIE_NAME)
    raise resp

async def dash_guilds(request):
    session = await dash_session(request)
    if not session:
        raise web.HTTPFound("/login")
    guilds = await fetch_my_guilds(session.get("token", ""))
    rows = []
    for g in guilds:
        gid = _int(g.get("id"))
        bg = bot.get_guild(gid)
        if bg is None or not is_web_admin(session["uid"], bg, g):
            continue
        cfg = get_config(gid)
        state = "<span class='pill on'>включён</span>" if cfg["enabled"] else "<span class='pill off'>выключен</span>"
        rows.append(
            f"<tr><td><a href='/guild/{gid}/'><b>{g['name']}</b></a></td>"
            f"<td>{state}</td><td>{len(cfg['channels'])} кан. цитат</td>"
            f"<td>{len(cfg['xp_channels'])} кан. прогрессии</td></tr>"
        )
    table = (
        "<table><tr><th>Сервер</th><th>Литании</th><th>Каналы цитат</th><th>Каналы прогрессии</th></tr>"
        + "".join(rows) + "</table>"
        if rows else
        "<p>Бот не найден на серверах, где у вас есть права настройки. Пригласите бота или попросите "
        "администратора бота добавить вас: <code>/админы добавить</code>.</p>"
    )
    body = f"<div class='card'><h2>Кузни под знаком Омниссии</h2><div class='sub'>Показаны лишь те кузни, где ты в совете магосов.</div>{table}</div>"
    return web.Response(text=dash_page("Серверы", body, session), content_type="text/html")

def _home_landing(guild: discord.Guild, flash_html: str = "") -> str:
    """Главная в духе carl.gg: hero с эмблемой кузни, волна, сетка разделов-карточек."""
    gid = guild.id
    cfg = get_config(gid)
    active_gw = [g for g in guild_giveaways(gid) if g.get("status") == "active"]
    pending = sum(1 for q in CUSTOM.get(str(gid), []) if q.get("status") == "pending")
    tint = ("", "g", "d")
    feats = ""
    for i, (key, (icon, title, sub, _art)) in enumerate(TAB_META.items()):
        feats += (
            f"<div class='fcard'><span class='ico {tint[i % 3]}'>{icon}</span><h3>{title}</h3>"
            f"<p>{sub}</p><a class='more' href='/guild/{gid}/{key}'>Открыть →</a></div>"
        )
    hero = (
        "<section class='hero'><div class='hero-txt'>"
        f"<h1>Кузница <span class='hgold'>Адептус Механикус</span></h1>"
        f"<p class='lead'>Панель кузни: литании во время живых бесед, ранги познания и дата-коги, "
        f"лавка даров, тех-арки и жребий конклава — всё в браузере, без единой команды. "
        f"Сервер: <b>{guild.name}</b>.</p>"
        "<div class='hero-cta'>"
        f"<a class='btn' href='/guild/{gid}/quotes'>📜 Книга литаний</a>"
        f"<a class='btn ghost' href='/guild/{gid}/progress'>⚙ Ранги познания</a>"
        f"<a class='btn ghost' href='/guild/{gid}/admins'>⚜ Магосы кузни</a>"
        "</div>"
        "<div class='herostats'>"
        f"<span class='pill'><b>{guild.member_count or len(guild.members)}</b> участников</span>"
        f"<span class='pill'><b>{len(cfg['channels'])}</b> каналов цитат</span>"
        f"<span class='pill'><b>{len(active_gw)}</b> активных жребиев</span>"
        f"<span class='pill'><b>{pending}</b> цитат на модерации</span>"
        "</div></div>"
        "<div class='hero-art'><div class='frame'><img src='/static/avatar.png' alt='Техножрец кузни'>"
        f"<span class='cap'><span>Adeptus Mechanicus</span><span>{guild.name}</span></span></div></div>"
        "</section>"
    )
    lower = (
        "<div class='lower'>"
        "<svg class='wave' viewBox='0 0 1440 90' preserveAspectRatio='none' aria-hidden='true'>"
        "<path fill='#14171b' d='M0,52 C240,92 480,12 720,42 C960,72 1200,22 1440,56 L1440,90 L0,90 Z'></path></svg>"
        + (f"<div class='flashwrap'>{flash_html}</div>" if flash_html else "")
        + "<div class='fsec'><h2>Разделы кузни</h2>"
        "<div class='sub'>выберите отсек панели — всё открывается без перезагрузки страницы</div>"
        f"<div class='fgrid'>{feats}</div></div>"
        f"<div class='homegrid'>{_side_stats(guild)}{_side_quote(guild)}</div>"
        f"<div class='wrap' style='padding-top:0'>{_audit_panel(guild)}</div>"
        "</div>"
    )
    return hero + lower

async def dash_home(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    body = _home_landing(guild, flash_block(request))
    return web.Response(text=dash_page(guild.name, body, session, guild_tabs(gid, ""), guild=guild), content_type="text/html")

async def dash_quotes(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    cfg = get_config(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/quotes?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:литании", post.get("action", "save"))
        action = post.get("action", "save")
        if action == "save":
            cfg["enabled"] = "enabled" in post
            cfg["channels"] = [_int(x) for x in post.getall("channels", []) if _int(x)]
            cfg["interval_minutes"] = _int(post.get("interval"), 45)
            cfg["window_minutes"] = _int(post.get("window"), 10)
            cfg["min_messages"] = _int(post.get("min_messages"), 6)
            cfg["min_unique_users"] = _int(post.get("min_users"), 2)
            cfg["era"] = post.get("era", "any")
            cfg["quotes_mode"] = post.get("quotes_mode", "moderate")
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/quotes?msg=Настройки+цитат+сохранены")
        if action in ("approve", "reject"):
            qid = _int(post.get("quote_id"))
            entries = CUSTOM.get(str(gid), [])
            target = next((q for q in entries if q["id"] == qid), None)
            if target:
                if action == "approve":
                    target["status"] = "approved"
                else:
                    entries.remove(target)
                save_custom()
            raise web.HTTPFound(f"/guild/{gid}/quotes?msg=Литания+обработана")
        if action == "add_quote":
            text = (post.get("text") or "").strip()
            if len(text) < 3:
                raise web.HTTPFound(f"/guild/{gid}/quotes?msg=Литания+слишком+короткая&kind=err")
            era = post.get("era", "both")
            author = (post.get("author") or "").strip() or "Неизвестный автор"
            source = (post.get("source") or "").strip() or "Добавлено администрацией"
            if post.get("dest", "custom") == "canon":
                CANON.append({"text": text[:1000], "author": author[:100],
                              "source": source[:140], "era": era})
                save_canon()
                msg = "Литания вписана в каноническую базу"
            else:
                entries = CUSTOM.setdefault(str(gid), [])
                new_id = max([q["id"] for q in entries], default=0) + 1
                entries.append({"id": new_id, "text": text[:1000], "author": author[:100],
                                "source": source[:140], "era": era, "status": "approved",
                                "added_by": session["uid"], "ts": int(time.time())})
                save_custom()
                msg = f"Литания+№{new_id}+вписана+в+книгу+кузни"
            raise web.HTTPFound(f"/guild/{gid}/quotes?msg={urllib.parse.quote(msg)}")
        if action == "del_custom":
            entries = CUSTOM.get(str(gid), [])
            target = next((q for q in entries if q["id"] == _int(post.get("quote_id"))), None)
            if target:
                entries.remove(target)
                save_custom()
            raise web.HTTPFound(f"/guild/{gid}/quotes?msg=Литания+удалена")
        if action == "del_canon":
            idx = _int(post.get("canon_idx"), -1)
            if 0 <= idx < len(CANON):
                CANON.pop(idx)
                save_canon()
            raise web.HTTPFound(f"/guild/{gid}/quotes?msg=Литания+убрана+из+канона")
    lst = request.query.get("list", "canon")
    q = request.query.get("q", "")
    page = max(1, _int(request.query.get("page", "1"), 1)) - 1
    if lst == "custom":
        raw = [dict(e, _key=f"u{e['id']}") for e in CUSTOM.get(str(gid), [])]
    else:
        raw = [dict(e, _key=f"c{i}", _idx=i) for i, e in enumerate(CANON)]
    filtered = filter_quotes(raw, q)
    chunk, pages, page = paginate(filtered, page, 10)
    rows = ""
    for e in chunk:
        if lst == "custom":
            status = "на модерации" if e.get("status") == "pending" else "одобрена"
            acts = ""
            if e.get("status") == "pending":
                acts += (
                    f"<form method='post' style='display:inline'>{csrf_input(session)}"
                    f"<input type='hidden' name='action' value='approve'><input type='hidden' name='quote_id' value='{e['id']}'>"
                    "<button>✅</button></form> "
                    f"<form method='post' style='display:inline'>{csrf_input(session)}"
                    f"<input type='hidden' name='action' value='reject'><input type='hidden' name='quote_id' value='{e['id']}'>"
                    "<button class='danger'>❌</button></form> "
                )
            acts += (
                f"<form method='post' style='display:inline'>{csrf_input(session)}"
                f"<input type='hidden' name='action' value='del_custom'><input type='hidden' name='quote_id' value='{e['id']}'>"
                "<button class='danger'>🗑</button></form>"
            )
        else:
            status = "канон"
            acts = (
                f"<form method='post' style='display:inline'>{csrf_input(session)}"
                f"<input type='hidden' name='action' value='del_canon'><input type='hidden' name='canon_idx' value='{e['_idx']}'>"
                "<button class='danger'>🗑</button></form>"
            )
        rows += (
            f"<tr><td>{e['_key']}</td><td>«{e['text'][:110]}»</td><td>{e.get('author', '')}</td>"
            f"<td>{ERA_LABELS.get(e.get('era'), e.get('era', ''))}</td><td>{status}</td><td>{acts}</td></tr>"
        )
    base = f"/guild/{gid}/quotes?list={lst}&q={urllib.parse.quote(q)}&page="
    nav = (f"<p><a class='btn ghost' href='{base}{page}'>◀</a> стр. {page + 1} / {pages} "
           f"<a class='btn ghost' href='{base}{page + 2}'>▶</a></p>") if pages > 1 else ""
    toggle = (f"<p><a class='btn{' ghost' if lst != 'canon' else ''}' href='/guild/{gid}/quotes?list=canon'>Канон ({len(CANON)})</a> "
              f"<a class='btn{' ghost' if lst != 'custom' else ''}' href='/guild/{gid}/quotes?list=custom'>Книга кузни ({len(CUSTOM.get(str(gid), []))})</a></p>")
    search = (f"<form method='get' class='row'><input type='hidden' name='list' value='{lst}'>"
              f"<div><input type='text' name='q' value='{q}' placeholder='Поиск: текст, автор, источник'></div>"
              "<div style='flex:0'><button class='ghost'>Найти</button></div></form>")
    table = (f"<table><tr><th>Ключ</th><th>Текст</th><th>Автор</th><th>Эпоха</th><th>Статус</th><th></th></tr>{rows}</table>"
             if chunk else "<p>Ничего не найдено.</p>")
    ch_opts = "".join(
        f"<option value='{c.id}'{' selected' if c.id in cfg['channels'] else ''}>#{c.name}</option>"
        for c in guild.text_channels
    )
    body = (
        flash_block(request)
        + f"<div class='card'><h2>Книга литаний</h2><div class='sub'>полный список цитат, поиск и назначение каналов</div>"
        + toggle + search + table + nav + "</div>"
        + f"<div class='card'><h2>Вписать литанию</h2><div class='sub'>магосы вписывают напрямую, без очередей</div>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='add_quote'>"
        "<label>Текст (минимум 3 символа)</label><textarea name='text' maxlength='1000' required></textarea>"
        "<div class='row'><div><label>Автор</label><input type='text' name='author' maxlength='100'></div>"
        "<div><label>Источник</label><input type='text' name='source' maxlength='140'></div></div>"
        "<div class='row'><div><label>Эпоха</label><select name='era'><option value='both'>Обе эпохи</option>"
        "<option value='heresy'>Ересь Хоруса</option><option value='imperium'>Империум (40k)</option></select></div>"
        "<div><label>Куда добавить</label><select name='dest'><option value='custom'>Книга кузни</option>"
        "<option value='canon'>Общая книга литаний (все кузни)</option></select></div></div>"
        "<p><button>Добавить цитату</button></p></form></div>"
        + f"<div class='card'><h2>Чередование литаний в беседах</h2><div class='sub'>команды: /цитаты … • /цитаты каналы …</div>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='save'>"
        f"<label><input type='checkbox' name='enabled'{' checked' if cfg['enabled'] else ''}> Включить литании на сервере</label>"
        f"<label>Каналы публикации (Ctrl+клик — несколько)</label><select name='channels' multiple>{ch_opts}</select>"
        "<div class='row'>"
        f"<div><label>Пауза между литаниями, мин</label><input type='number' name='interval' min='5' max='10080' value='{cfg['interval_minutes']}'></div>"
        f"<div><label>Окно активности, мин</label><input type='number' name='window' min='1' max='120' value='{cfg['window_minutes']}'></div>"
        f"<div><label>Мин. сообщений в окне</label><input type='number' name='min_messages' min='2' max='200' value='{cfg['min_messages']}'></div>"
        f"<div><label>Мин. разных авторов</label><input type='number' name='min_users' min='2' max='100' value='{cfg['min_unique_users']}'></div>"
        "</div><div class='row'>"
        "<div><label>Эпохи</label><select name='era'>"
        f"<option value='any'{opt(cfg['era'], 'any')}>Все эпохи</option>"
        f"<option value='heresy'{opt(cfg['era'], 'heresy')}>Ересь Хоруса</option>"
        f"<option value='imperium'{opt(cfg['era'], 'imperium')}>Империум (40k)</option></select></div>"
        "<div><label>Цитаты игроков</label><select name='quotes_mode'>"
        f"<option value='moderate'{opt(cfg['quotes_mode'], 'moderate')}>Модерация</option>"
        f"<option value='auto'{opt(cfg['quotes_mode'], 'auto')}>Авто-одобрение</option>"
        f"<option value='off'{opt(cfg['quotes_mode'], 'off')}>Отключены</option></select></div>"
        "</div><p><button>Сохранить</button></p></form></div>"
    )
    return web.Response(text=dash_page("Литании", body, session, guild_tabs(gid, "quotes"), guild=guild), content_type="text/html")

async def dash_progress(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    cfg = get_config(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/progress?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:прогрессия", post.get("action", "save"))
        action = post.get("action", "save")
        if action == "save":
            old_mult_mode = cfg.get("mult_mode", "add")
            cfg["xp_channels"] = [_int(x) for x in post.getall("xp_channels", []) if _int(x)]
            cfg["xp_volume"] = "volume" in post
            cfg["level_roles_exclusive"] = "exclusive" in post
            cfg["xp_char_cap"] = max(100, min(4000, _int(post.get("cap"), 2000)))
            cfg["xp_per_message"] = _int(post.get("xp"), 10)
            cfg["credits_per_message"] = _int(post.get("credits"), 5)
            cfg["xp_cooldown"] = _int(post.get("cooldown"), 60)
            cfg["xp_base"] = _int(post.get("xp_base"), 100)
            cfg["xp_step"] = max(1, _int(post.get("xp_step"), 10))
            cfg["xp_mode"] = "mul" if post.get("xp_mode") == "mul" else "add"
            cfg["xp_mul"] = max(1.01, min(5.0, _float(post.get("xp_mul"), 1.1)))
            cfg["mult_mode"] = "mul" if post.get("mult_mode") == "mul" else "add"
            new_ms = _float(post.get("mult_step"), 0.5)
            new_mm = max(1.01, min(5.0, _float(post.get("mult_mul"), 1.1)))
            if (abs(new_ms - float(cfg.get("mult_step", 0.5))) > 1e-9
                    or abs(new_mm - float(cfg.get("mult_mul", 1.1))) > 1e-9
                    or cfg["mult_mode"] != old_mult_mode):
                cfg["mult_step"] = new_ms
                cfg["mult_mul"] = new_mm
                cfg["multipliers"] = mult_table(cfg)
            else:
                cfg["mult_step"] = new_ms
                cfg["mult_mul"] = new_mm
            cfg["announce_levelup"] = "announce" in post
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/progress?msg=Прогрессия+сохранена")
        if action == "role_mult_add":
            cfg["role_multipliers"][post.get("role", "0")] = {
                "mult": round(_float(post.get("value"), 1.0), 2),
                "apply": post.get("apply", "both"),
            }
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/progress?msg=Коэффициент+роли+добавлен")
        if action == "role_mult_del":
            cfg["role_multipliers"].pop(post.get("role", ""), None)
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/progress?msg=Коэффициент+удалён")
        if action == "mult_set":
            lvl = _int(post.get("level"), 1)
            if 1 <= lvl <= MAX_LEVEL:
                cfg["multipliers"][lvl - 1] = round(_float(post.get("value"), 1.0), 2)
                save_configs()
            raise web.HTTPFound(f"/guild/{gid}/progress?msg=Множитель+ранга+обновлён")
        if action == "mult_reset":
            cfg["multipliers"] = mult_table(cfg)
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/progress?msg=Таблица+множителей+сброшена")
        if action == "reward_add":
            rewards = cfg.setdefault("level_rewards", [])
            new_id = max([r.get("id", 0) for r in rewards], default=0) + 1
            kind = post.get("kind", "credits")
            rewards.append({
                "id": new_id,
                "level": min(MAX_LEVEL, max(1, _int(post.get("level"), 1))),
                "kind": kind,
                "role_id": _int(post.get("role")) or None,
                "item_name": (post.get("name") or "").strip()[:80] or None,
                "credits": max(0, _int(post.get("amount"))),
            })
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/progress?msg=Награда+добавлена")
        if action == "reward_del":
            rewards = cfg.setdefault("level_rewards", [])
            for r in list(rewards):
                if r.get("id") == _int(post.get("rid")):
                    rewards.remove(r)
                    save_configs()
                    break
            raise web.HTTPFound(f"/guild/{gid}/progress?msg=Награда+удалена")
    xp_opts = "".join(
        f"<option value='{c.id}'{' selected' if c.id in cfg['xp_channels'] else ''}>"
        f"#{c.name}{' (форум: считаются посты и ветки)' if c in guild.forums else ''}</option>"
        for c in (list(guild.text_channels) + list(guild.forums))
    )
    role_opts = "".join(f"<option value='{r.id}'>@{r.name}</option>" for r in guild.roles if not r.managed)
    rm_rows = ""
    for rid, e in cfg.get("role_multipliers", {}).items():
        r = guild.get_role(_int(rid))
        rm_rows += (
            f"<tr><td>@{r.name if r else rid}</td><td>×{e['mult']:g}</td>"
            f"<td>{APPLY_LABELS.get(e.get('apply', 'both'), e.get('apply'))}</td>"
            f"<td><form method='post' style='display:inline'>{csrf_input(session)}"
            f"<input type='hidden' name='action' value='role_mult_del'><input type='hidden' name='role' value='{rid}'>"
            "<button class='danger'>Удалить</button></form></td></tr>"
        )
    rm_block = (
        f"<table><tr><th>Роль</th><th>Множитель</th><th>Применяется</th><th></th></tr>{rm_rows}</table>"
        if cfg.get("role_multipliers") else "<p>Коэффициенты не заданы.</p>"
    )
    rw_rows = ""
    where = {"role": "роль", "item": "предмет", "credits": "дата-коги"}
    for r in cfg.get("level_rewards", []):
        if r["kind"] == "role":
            role = guild.get_role(_int(r.get("role_id")))
            val = f"@{role.name if role else r.get('role_id')}"
        elif r["kind"] == "item":
            val = f"«{r.get('item_name')}»"
        else:
            val = f"{fmt_credits(r.get('credits', 0))} {CREDITS_NAME}"
        rw_rows += (
            f"<tr><td>№{r['id']}</td><td>ур. {r['level']}</td><td>{where.get(r['kind'], r['kind'])}</td><td>{val}</td>"
            f"<td><form method='post' style='display:inline'>{csrf_input(session)}"
            f"<input type='hidden' name='action' value='reward_del'><input type='hidden' name='rid' value='{r['id']}'>"
            "<button class='danger'>Убрать</button></form></td></tr>"
        )
    rw_block = (
        f"<table><tr><th>№</th><th>Ранг</th><th>Тип</th><th>Значение</th><th></th></tr>{rw_rows}</table>"
        if cfg.get("level_rewards") else "<p>Награды не заданы.</p>"
    )
    body = (
        flash_block(request)
        + f"<div class='card'><h2>Уровни и опыт</h2><div class='sub'>команды: /уровни …</div>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='save'>"
        f"<label>Каналы прогрессии (XP и дата-коги)</label><select name='xp_channels' multiple>{xp_opts}</select>"
        "<div class='row'>"
        f"<div><label>XP за сообщение</label><input type='number' name='xp' min='1' max='1000' value='{cfg['xp_per_message']}'></div>"
        f"<div><label>Дата-когов за сообщение (ставка на лимите символов)</label><input type='number' name='credits' min='1' max='1000' value='{cfg['credits_per_message']}'></div>"
        f"<div><label>Кулдаун, сек (только в фикс-режиме)</label><input type='number' name='cooldown' min='0' max='3600' value='{cfg['xp_cooldown']}'></div>"
        f"<div><label>Лимит символов для максимума (xp_char_cap)</label><input type='number' name='cap' min='100' max='4000' value='{int(cfg.get('xp_char_cap', 2000))}'></div>"
        f"<label><input type='checkbox' name='volume'{' checked' if cfg.get('xp_volume', True) else ''}> Объёмный режим: награда пропорциональна длине сообщения, без кулдауна</label>"
        f"<label><input type='checkbox' name='exclusive'{' checked' if cfg.get('level_roles_exclusive', True) else ''}> Ранг-роли эксклюзивны: с новым рангом прежняя роль ранга снимается</label>"
        f"<div><label>База XP (xp_base)</label><input type='number' name='xp_base' min='10' max='100000' value='{cfg['xp_base']}'></div>"
        f"<div><label>Режим кривой опыта</label><select name='xp_mode'>"
        f"<option value='add'{' selected' if cfg.get('xp_mode', 'add') != 'mul' else ''}>шаг: +N опыта за ранг</option>"
        f"<option value='mul'{' selected' if cfg.get('xp_mode') == 'mul' else ''}>множитель: ×N за ранг</option></select></div>"
        f"<div><label>Шаг необходимого опыта (xp_step)</label><input type='number' name='xp_step' min='1' max='100000' value='{int(cfg.get('xp_step', 10))}'></div>"
        f"<div><label>Множитель необходимого опыта (xp_mul)</label><input type='number' step='0.01' name='xp_mul' min='1.01' max='5' value='{float(cfg.get('xp_mul', 1.1)):g}'></div>"
        f"<div><label>Режим кривой модификаторов</label><select name='mult_mode'>"
        f"<option value='add'{' selected' if cfg.get('mult_mode', 'add') != 'mul' else ''}>шаг: +N к модификатору за ранг</option>"
        f"<option value='mul'{' selected' if cfg.get('mult_mode') == 'mul' else ''}>множитель: модификатор ×N за ранг</option></select></div>"
        f"<div><label>Шаг модификаторов (mult_step)</label><input type='number' step='0.05' name='mult_step' min='0.01' max='10' value='{float(cfg.get('mult_step', 0.5)):g}'></div>"
        f"<div><label>Множитель модификаторов (mult_mul)</label><input type='number' step='0.01' name='mult_mul' min='1.01' max='5' value='{float(cfg.get('mult_mul', 1.1)):g}'></div>"
        "</div>"
        f"<label><input type='checkbox' name='announce'{' checked' if cfg['announce_levelup'] else ''}> Анонсировать повышение ранга</label>"
        "<p><button>Сохранить</button></p></form></div>"
        + f"<div class='card'><h2>Дары за ранги</h2><div class='sub'>знамя кузни, дар в кладезь или дата-коги при достижении ранга</div>{rw_block}"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='reward_add'><div class='row'>"
        "<div><label>Ранг</label><input type='number' name='level' min='1' max='100' value='5'></div>"
        "<div><label>Тип награды</label><select name='kind'><option value='role'>Роль</option>"
        "<option value='item'>Предмет в кладезь</option><option value='credits'>Дата-коги</option></select></div>"
        f"<div><label>Роль (если тип «роль»)</label><select name='role'><option value='0'>—</option>{role_opts}</select></div>"
        "<div><label>Название предмета</label><input type='text' name='name' maxlength='80'></div>"
        "<div><label>Сумма дата-когов</label><input type='number' name='amount' min='0' max='1000000' value='0'></div>"
        "</div><p><button>Добавить награду</button></p></form></div>"
        + f"<div class='card'><h2>Знамёна кузни</h2>{rm_block}"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='role_mult_add'><div class='row'>"
        f"<div><label>Роль</label><select name='role'>{role_opts}</select></div>"
        "<div><label>Множитель</label><input type='number' step='0.05' name='value' min='0.1' max='100' value='1.25'></div>"
        "<div><label>Применять к</label><select name='apply'><option value='both'>XP и дата-коги</option>"
        "<option value='credits'>Только дата-коги</option><option value='xp'>Только XP</option></select></div>"
        "</div><p><button>Добавить коэффициент</button></p></form></div>"
        + f"<div class='card'><h2>Дата-коги по рангам</h2>"
        f"<p class='sub'>Сейчас: ур.1 ×{cfg['multipliers'][0]:g}, ур.10 ×{cfg['multipliers'][9]:g}, ур.100 ×{cfg['multipliers'][99]:g}</p>"
        f"<form method='post' class='row'>{csrf_input(session)}<input type='hidden' name='action' value='mult_set'>"
        "<div><label>Ранг</label><input type='number' name='level' min='1' max='100' value='5'></div>"
        "<div><label>Множитель</label><input type='number' step='0.05' name='value' min='0.1' max='10000' value='2'></div>"
        "<div style='flex:0'><label>&nbsp;</label><button>Задать</button></div></form>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='mult_reset'>"
        "<p><button class='ghost'>Сбросить таблицу (×1, ×1.1, ×1.3, …)</button></p></form></div>"
    )
    return web.Response(text=dash_page("Прогрессия", body, session, guild_tabs(gid, "progress"), guild=guild), content_type="text/html")

async def dash_shop(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    cfg = get_config(gid)
    items = shop_items(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/shop?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:лавка", post.get("action", "save"))
        action = post.get("action", "")
        if action == "add":
            new_id = max([it["id"] for it in items], default=0) + 1
            items.append({
                "id": new_id,
                "name": post.get("name", "Товар")[:80],
                "price": max(1, _int(post.get("price"), 1)),
                "description": post.get("description", "") or "",
            })
            save_shop()
            raise web.HTTPFound(f"/guild/{gid}/shop?msg=Товар+добавлен")
        if action == "remove":
            for it in list(items):
                if it["id"] == _int(post.get("item")):
                    items.remove(it)
                    save_shop()
                    break
            raise web.HTTPFound(f"/guild/{gid}/shop?msg=Товар+удалён")
        if action == "notify":
            cfg["shop_notify_channel"] = _int(post.get("channel")) or None
            cfg["shop_notify_role"] = _int(post.get("role")) or None
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/shop?msg=Уведомления+настроены")
    rows = ""
    for it in items:
        rows += (
            f"<tr><td>№{it['id']}</td><td>{it['name']}</td><td>{fmt_credits(it['price'])}</td>"
            f"<td><form method='post' style='display:inline'>{csrf_input(session)}"
            f"<input type='hidden' name='action' value='remove'><input type='hidden' name='item' value='{it['id']}'>"
            "<button class='danger'>Удалить</button></form></td></tr>"
        )
    shop_block = f"<table><tr><th>№</th><th>Товар</th><th>Цена</th><th></th></tr>{rows}</table>" if items else "<p>Лавка пуста.</p>"
    ch_opts = "".join(
        f"<option value='{c.id}'{' selected' if c.id == cfg['shop_notify_channel'] else ''}>#{c.name}</option>"
        for c in guild.text_channels
    )
    role_opts = "".join(
        f"<option value='{r.id}'{' selected' if r.id == cfg['shop_notify_role'] else ''}>@{r.name}</option>"
        for r in guild.roles if not r.managed
    )
    body = (
        flash_block(request)
        + f"<div class='card'><h2>Магазин</h2><div class='sub'>команды: /магазин …</div>{shop_block}"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='add'>"
        "<div class='row'><div><label>Название</label><input type='text' name='name' maxlength='80' required></div>"
        "<div><label>Цена, дата-когов</label><input type='number' name='price' min='1' max='10000000' value='100'></div></div>"
        "<label>Описание</label><textarea name='description'></textarea>"
        "<p><button>Добавить товар</button></p></form></div>"
        + f"<div class='card'><h2>Уведомления лавки</h2><div class='sub'>команда: /магазин уведомления</div>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='notify'><div class='row'>"
        f"<div><label>Канал</label><select name='channel'><option value='0'>— не назначен —</option>{ch_opts}</select></div>"
        f"<div><label>Роль (пинг)</label><select name='role'><option value='0'>— не назначена —</option>{role_opts}</select></div>"
        "</div><p><button>Сохранить</button></p></form></div>"
    )
    return web.Response(text=dash_page("Лавка", body, session, guild_tabs(gid, "shop"), guild=guild), content_type="text/html")

async def dash_loot(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    boxes = loot_boxes(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/loot?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:тех-арки", post.get("action", "save"))
        action = post.get("action", "")
        if action == "box_add":
            new_id = max([b["id"] for b in boxes], default=0) + 1
            boxes.append({"id": new_id, "name": post.get("name", "Тех-арк")[:80],
                          "price": max(1, _int(post.get("price"), 100)), "items": []})
            save_loot()
            raise web.HTTPFound(f"/guild/{gid}/loot?msg=Тех-арк+создан")
        if action == "box_del":
            for b in list(boxes):
                if b["id"] == _int(post.get("box")):
                    boxes.remove(b)
                    save_loot()
                    break
            raise web.HTTPFound(f"/guild/{gid}/loot?msg=Тех-арк+удалён")
        if action == "item_add":
            box = next((b for b in boxes if b["id"] == _int(post.get("box"))), None)
            if box:
                new_id = max([it["id"] for it in box.get("items", [])], default=0) + 1
                box.setdefault("items", []).append({
                    "id": new_id, "name": post.get("name", "Предмет")[:80],
                    "chance": max(0.01, _float(post.get("chance"), 1.0)),
                    "description": post.get("description", "") or "",
                })
                save_loot()
            raise web.HTTPFound(f"/guild/{gid}/loot?msg=Предмет+добавлен")
        if action == "item_del":
            box = next((b for b in boxes if b["id"] == _int(post.get("box"))), None)
            if box:
                for it in list(box.get("items", [])):
                    if it["id"] == _int(post.get("item")):
                        box["items"].remove(it)
                        save_loot()
                        break
            raise web.HTTPFound(f"/guild/{gid}/loot?msg=Предмет+удалён")
    cards = ""
    for b in boxes:
        it_rows = ""
        for it in b.get("items", []):
            it_rows += (
                f"<tr><td>№{it['id']} {it['name']}</td><td>{chance_percent(b, it):.1f}%</td>"
                f"<td><form method='post' style='display:inline'>{csrf_input(session)}"
                f"<input type='hidden' name='action' value='item_del'><input type='hidden' name='box' value='{b['id']}'>"
                f"<input type='hidden' name='item' value='{it['id']}'><button class='danger'>Убрать</button></form></td></tr>"
            )
        items_block = f"<table><tr><th>Предмет</th><th>Шанс</th><th></th></tr>{it_rows}</table>" if b.get("items") else "<p>Пуста.</p>"
        cards += (
            f"<div class='card'><h2>Тех-арк №{b['id']}: {b['name']}</h2>"
            f"<div class='sub'>Цена вскрытия: {fmt_credits(b['price'])} дата-когов</div>{items_block}"
            f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='item_add'>"
            f"<input type='hidden' name='box' value='{b['id']}'><div class='row'>"
            "<div><label>Название предмета</label><input type='text' name='name' maxlength='80' required></div>"
            "<div><label>Шанс (вес)</label><input type='number' step='0.01' name='chance' min='0.01' max='1000' value='10'></div></div>"
            "<label>Описание</label><textarea name='description'></textarea>"
            "<p><button>Добавить предмет</button></p></form>"
            f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='box_del'>"
            f"<input type='hidden' name='box' value='{b['id']}'><p><button class='danger'>Удалить тех-арк</button></p></form></div>"
        )
    body = (
        flash_block(request) + cards
        + f"<div class='card'><h2>Создать лутбокс</h2><div class='sub'>команда: /лутбоксы создать</div>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='box_add'><div class='row'>"
        "<div><label>Название</label><input type='text' name='name' maxlength='80' required></div>"
        "<div><label>Цена вскрытия</label><input type='number' name='price' min='1' max='10000000' value='100'></div></div>"
        "<p><button>Создать тех-арк</button></p></form></div>"
    )
    return web.Response(text=dash_page("Тех-арки", body, session, guild_tabs(gid, "loot"), guild=guild), content_type="text/html")

async def dash_giveaways(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    gws = guild_giveaways(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/giveaways?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:жребии", post.get("action", ""))
        action = post.get("action", "")
        if action == "create":
            channel = guild.get_channel(_int(post.get("channel")))
            if channel is None:
                raise web.HTTPFound(f"/guild/{gid}/giveaways?msg=Выберите+канал&kind=err")
            new_id = max([g["id"] for g in gws], default=0) + 1
            k = post.get("kind", "items")
            prizes = {}
            for place in (1, 2, 3):
                p = (post.get(f"prize{place}", "") or "").strip()
                if not p:
                    continue
                if k == "credits":
                    v = _int(p)
                    if v > 0:
                        prizes[str(place)] = {"credits": v}
                else:
                    prizes[str(place)] = {"name": p[:80], "description": "Приз жребия"}
            gw = {
                "id": new_id, "title": post.get("title", "Жребий")[:100],
                "channel_id": channel.id, "message_id": None,
                "ends_at": time.time() + max(1, _int(post.get("minutes"), 60)) * 60,
                "winners": min(10, max(1, _int(post.get("winners"), 1))),
                "prizes": prizes, "kind": k, "participants": [], "status": "active",
            }
            gws.append(gw)
            save_giveaways()
            view = GiveawayView(gid, new_id)
            msg = await channel.send(embed=giveaway_embed(gw), view=view)
            gw["message_id"] = msg.id
            save_giveaways()
            raise web.HTTPFound(f"/guild/{gid}/giveaways?msg=Жребий+запущен")
        if action in ("end", "cancel"):
            gw = next((g for g in gws if g["id"] == _int(post.get("giveaway"))), None)
            if gw and gw.get("status") == "active":
                if action == "end":
                    await finish_giveaway(guild, gw)
                else:
                    gw["status"] = "cancelled"
                    save_giveaways()
                    channel = guild.get_channel(gw.get("channel_id")) if gw.get("channel_id") else None
                    if channel is not None and gw.get("message_id"):
                        try:
                            m = await channel.fetch_message(gw["message_id"])
                            await m.edit(embed=giveaway_embed(gw), view=None)
                        except discord.HTTPException:
                            pass
            raise web.HTTPFound(f"/guild/{gid}/giveaways?msg=Жребий+завершён")
    rows = ""
    for g in gws[-15:][::-1]:
        acts = ""
        if g.get("status") == "active":
            acts = (
                f"<form method='post' style='display:inline'>{csrf_input(session)}"
                f"<input type='hidden' name='action' value='end'><input type='hidden' name='giveaway' value='{g['id']}'>"
                "<button>Завершить</button></form> "
                f"<form method='post' style='display:inline'>{csrf_input(session)}"
                f"<input type='hidden' name='action' value='cancel'><input type='hidden' name='giveaway' value='{g['id']}'>"
                "<button class='danger'>Отменить</button></form>"
            )
        ends = f"<t:{int(g['ends_at'])}:R>" if g.get("status") == "active" else "—"
        kind_lbl = "валюта" if g.get("kind") == "credits" else "предметы"
        rows += (
            f"<tr><td>№{g['id']} {g['title']}</td><td>{g.get('status')}</td><td>{kind_lbl}</td>"
            f"<td>{len(g.get('participants', []))}</td><td>{ends}</td><td>{acts}</td></tr>"
        )
    gw_block = (
        f"<table><tr><th>Жребий</th><th>Статус</th><th>Призы</th><th>Участников</th><th>До конца</th><th></th></tr>{rows}</table>"
        if gws else "<p>Жребийей ещё не было.</p>"
    )
    ch_opts = "".join(f"<option value='{c.id}'>#{c.name}</option>" for c in guild.text_channels)
    body = (
        flash_block(request)
        + f"<div class='card'><h2>Жребий кузни</h2><div class='sub'>команды: /розыгрыши …; призы «валютой» зачисляются на баланс автоматически</div>{gw_block}"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='create'>"
        "<div class='row'><div><label>Название</label><input type='text' name='title' maxlength='100' required></div>"
        f"<div><label>Канал сообщения</label><select name='channel'>{ch_opts}</select></div></div>"
        "<div class='row'><div><label>Длительность, мин</label><input type='number' name='minutes' min='1' max='10080' value='60'></div>"
        "<div><label>Победителей</label><input type='number' name='winners' min='1' max='10' value='1'></div>"
        "<div><label>Тип призов</label><select name='kind'><option value='items'>Предметы в кладезь</option>"
        "<option value='credits'>Валюта на баланс</option></select></div></div>"
        "<div class='row'><div><label>Приз за 1 место (название ИЛИ сумма дата-когов)</label><input type='text' name='prize1' maxlength='80'></div>"
        "<div><label>Приз за 2 место</label><input type='text' name='prize2' maxlength='80'></div>"
        "<div><label>Приз за 3 место</label><input type='text' name='prize3' maxlength='80'></div></div>"
        "<p><button>Запустить жребий</button></p></form></div>"
    )
    return web.Response(text=dash_page("Жребии", body, session, guild_tabs(gid, "giveaways"), guild=guild), content_type="text/html")

def _board_html_rows(guild: discord.Guild, rows: list, rewards: list, limit: int) -> str:
    """Строки таблицы рейтинга для дашборда (HTML, без discord-разметки)."""
    if not rows:
        return "<p class='sub'>Пока пусто: участники не достигли порога сообщений.</p>"
    out = []
    for i, (uid, xp, msg) in enumerate(rows[:limit], 1):
        m = guild.get_member(uid)
        name = (m.display_name if m else f"id {uid}").replace("<", "&lt;")
        prize = rewards[i - 1] if i - 1 < len(rewards) else 0
        out.append(
            f"<tr><td>{i}</td><td>{name}</td><td>{xp}</td><td>{msg}</td>"
            f"<td>{prize if prize else '—'}</td></tr>"
        )
    return ("<table><tr><th>№</th><th>Участник</th><th>XP</th><th>Сообщ.</th><th>Награда</th></tr>"
            + "".join(out) + "</table>")

async def dash_board(request):
    """Рейтинг активности: живые таблицы циклов, архив и настройки."""
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    cfg = get_config(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/board?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:рейтинг", post.get("action", "save"))
        cfg["board_enabled"] = "enabled" in post
        cfg["board_daily"] = "daily" in post
        cfg["board_weekly"] = "weekly" in post
        cfg["board_channel"] = _int(post.get("channel")) or None
        for key, field, lo, hi in (
            ("board_min_messages", "min_messages", 0, 1000),
            ("board_show", "show", 3, 25),
            ("board_utc_offset", "offset", -12, 14),
        ):
            cfg[key] = max(lo, min(hi, _int(post.get(field))))
        for key, field in (("board_rewards_daily", "rewards_daily"),
                           ("board_rewards_weekly", "rewards_weekly")):
            vals, err = board_parse_rewards(post.get(field, ""))
            if err:
                raise web.HTTPFound(
                    f"/guild/{gid}/board?msg=Награды+{field}:+" + err.replace(" ", "+") + "&kind=err")
            cfg[key] = vals
        save_configs()
        raise web.HTTPFound(f"/guild/{gid}/board?msg=Рейтинг+сохранён")
    day, week = board_cycle_ids(cfg)
    rew_d = cfg.get("board_rewards_daily", [])
    rew_w = cfg.get("board_rewards_weekly", [])
    limit = int(cfg.get("board_show", 10))
    tbl_d = _board_html_rows(guild, await board_top(guild, cfg, "d", day), rew_d, limit)
    tbl_w = _board_html_rows(guild, await board_top(guild, cfg, "w", week), rew_w, limit)
    hist = board_state(gid)["hist"]
    arch = []
    for period, label in (("d", "день"), ("w", "неделя")):
        for h in hist[period][-2:]:
            top = ", ".join(
                f"{i}. {(guild.get_member(u).display_name if guild.get_member(u) else u)} ({x} XP)"
                for i, (u, x, _m) in enumerate(h["rows"][:3], 1)) or "—"
            arch.append(f"<li><b>{h['id']}</b> ({label}, <t:{h['ts']}:D>): {top}"
                        + (f" • наград: {len(h['paid'])}" if h.get("paid") else "") + "</li>")
    ch_opts = "".join(
        f"<option value='{c.id}'{' selected' if c.id == cfg.get('board_channel') else ''}>#{c.name}</option>"
        for c in guild.text_channels
    )
    body = (
        flash_block(request)
        + f"<div class='card'><h2>Текущий день: {day}</h2>{tbl_d}</div>"
        + f"<div class='card'><h2>Текущая неделя: {week}</h2>{tbl_w}</div>"
        + ("<div class='card'><h2>Архив циклов</h2><ul>" + "".join(arch) + "</ul></div>" if arch else "")
        + "<div class='card'><h2>Настройки рейтинга</h2>"
        "<div class='sub'>команды: /рейтинг день • неделя • архив • награды • исключения; "
        "награды начисляются в дата-коги при перекате цикла</div>"
        f"<form method='post'>{csrf_input(session)}"
        f"<label><input type='checkbox' name='enabled'{' checked' if cfg.get('board_enabled') else ''}> Включить рейтинг</label>"
        f"<label><input type='checkbox' name='daily'{' checked' if cfg.get('board_daily') else ''}> Дневной цикл</label>"
        f"<label><input type='checkbox' name='weekly'{' checked' if cfg.get('board_weekly') else ''}> Недельный цикл</label>"
        f"<label>Канал анонса итогов</label><select name='channel'><option value='0'>— не назначен —</option>{ch_opts}</select>"
        f"<label>Награды дня (дата-коги за места, через запятую)</label>"
        f"<input type='text' name='rewards_daily' value='{','.join(str(v) for v in rew_d)}'>"
        f"<label>Награды недели</label>"
        f"<input type='text' name='rewards_weekly' value='{','.join(str(v) for v in rew_w)}'>"
        f"<label>Порог сообщений за цикл</label>"
        f"<input type='number' name='min_messages' min='0' max='1000' value='{int(cfg.get('board_min_messages', 5))}'>"
        f"<label>Строк в таблицах (3–25)</label>"
        f"<input type='number' name='show' min='3' max='25' value='{limit}'>"
        f"<label>Часовой пояс границ (сдвиг от UTC, часов)</label>"
        f"<input type='number' name='offset' min='-12' max='14' value='{board_utc_offset(cfg)}'>"
        "<p><button name='action' value='save'>Сохранить</button></p></form></div>"
    )
    return web.Response(text=dash_page("Рейтинг", body, session, guild_tabs(gid, "board"), guild=guild), content_type="text/html")

async def dash_dossier(request):
    """Досье: настройки модуля, конструктор блоков-дерева, архив анкет."""
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    g = doss_guild(gid)
    cfg = doss_cfg(gid)
    blocks = doss_blocks(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:досье", post.get("action", ""))
        fmap = {"cfg": {"save_cfg"}, "rules": {"save_rules", "rules_default"},
                "new": {"block_new"}, "blk": {"block_save"}, "del": {"block_del"}}
        marker = post.get("form", "")
        action = post.get("action", "")
        if marker in fmap:
            if not action and len(fmap[marker]) == 1:
                action = next(iter(fmap[marker]))  # бесшовный POST без кнопки-отправителя
            elif action not in fmap[marker]:
                raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Кнопка+не+из+этой+формы+«{action}»:"
                                    f"+действие+проигнорировано&kind=err")
        else:
            action = action or "save_cfg"
        if action == "save_cfg" and not any(k in post for k in (
                "enabled", "category", "review_role", "canon_role", "canon_level",
                "root_block", "core_blocks", "header", "footer")):
            raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Пустая+форма+настроек+проигнорирована:"
                                f"+данные+не+затёрты&kind=err")
        if action == "save_cfg":
            cfg["enabled"] = "enabled" in post
            cfg["category"] = _int(post.get("category")) or None
            cfg["review_role"] = _int(post.get("review_role")) or None
            cfg["canon_role"] = _int(post.get("canon_role")) or None
            cfg["canon_level"] = max(0, min(100, _int(post.get("canon_level"))))
            root = post.get("root_block") or ""
            if root and root not in blocks:
                raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Корневой+блок+не+найден&kind=err")
            cfg["root_block"] = root or None
            raw_core = post.get("core_blocks") or ""
            core = []
            for b in re.findall(r"\d+", raw_core):
                if b not in core:
                    core.append(b)
            missing = [b for b in core if b not in blocks]
            if missing:
                valid = ",".join(sorted(blocks, key=lambda x: int(x) if x.isdigit() else 0)) or "пока никаких"
                raise web.HTTPFound(f"/guild/{gid}/dossier?msg=В+ядре+нет+блоков+"
                                    + "+".join(missing) + f"+(существуют:+{valid})&kind=err")
            cfg["core_blocks"] = core
            cfg["header"] = (post.get("header") or DEFAULT_DOSS_HEADER)[:1500]
            cfg["footer"] = (post.get("footer") or DEFAULT_DOSS_FOOTER)[:1500]
            save_dossier()
            log.info("Панель: досье save_cfg сохранено: enabled=%s category=%s review=%s canon_role=%s "
                     "canon_level=%s root=%s core=%s", cfg["enabled"], cfg["category"], cfg["review_role"],
                     cfg["canon_role"], cfg["canon_level"], cfg["root_block"], cfg["core_blocks"])
            _core_msg = ("+Ядро:+" + "+".join(cfg["core_blocks"])) if cfg["core_blocks"] else "+Ядро:+пусто"
            raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Настройки+досье+сохранены.{_core_msg}")
        if action == "save_rules":
            # r44: правила анкет — отдельная форма, свой маркер «rules»
            cfg["rules_send"] = "rules_send" in post
            cfg["rules_text"] = (post.get("rules_text") or "")[:20000]
            save_dossier()
            n = len(doss_rules_chunks(cfg["rules_text"]))
            log.info("Панель: досье save_rules: send=%s знаков=%d сообщений=%d",
                     cfg["rules_send"], len(cfg["rules_text"]), n)
            state = "отправка+включена" if cfg["rules_send"] else "отправка+выключена"
            raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Правила+анкет+сохранены:+{state},+"
                                f"{n}+сообщ.+по+лимитам+Discord")
        if action == "rules_default":
            cfg["rules_text"] = DEFAULT_DOSS_RULES
            cfg["rules_send"] = True
            save_dossier()
            log.info("Панель: досье rules_default — правила сброшены к заводским")
            raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Правила+анкет+сброшены+к+заводским+и+включены")
        if action == "block_new":
            bid = doss_new_block_id(gid)
            blocks[str(bid)] = {"id": bid, "title": (post.get("title") or "Новый блок")[:80],
                                "text": "Вопрос блока…", "answers": []}
            save_dossier()
            raise web.HTTPFound(f"/guild/{gid}/dossier?block={bid}&msg=Блок+создан:+добавьте+хотя+бы+один+"
                                f"ответ+(подпись+и+фрагмент)+и+сохраните+блок")
        if action == "block_save":
            bid = str(_int(post.get("block")))
            blk = blocks.get(bid)
            if not blk:
                raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Блок+не+найден&kind=err")
            labels = post.getall("label", []) or []
            frags = post.getall("fragment", []) or []
            nexts = post.getall("next", []) or []
            ids = post.getall("ans_id", []) or []
            canon = post.getall("canon", []) or []
            selff = post.getall("self", []) or []
            answers = []
            exist_ids = [int(a.get("id")) for a in blk.get("answers", [])
                         if str(a.get("id", "")).isdigit()]
            next_id = (max(exist_ids) + 1) if exist_ids else 1
            for i, lab in enumerate(labels):
                lab = (lab or "").strip()
                if not lab:
                    continue
                if i < len(ids) and ids[i] not in ("", "0"):
                    aid = ids[i]
                else:
                    aid = str(next_id)
                    next_id += 1
                nxt = nexts[i] if i < len(nexts) else ""
                answers.append({
                    "id": aid, "label": lab[:80],
                    "fragment": (frags[i] if i < len(frags) else "")[:1900],
                    "next": nxt if nxt and nxt != bid else "",
                    "canon": f"c{i}" in canon, "self": f"s{i}" in selff,
                })
            cand = {"id": blk.get("id"), "title": (post.get("title") or "")[:80],
                    "text": (post.get("text") or "")[:1900], "answers": answers,
                    "after": (post.get("after") or "").strip()}
            err = doss_validate_block(gid, cand, ignore_id=bid)
            if err:
                raise web.HTTPFound(f"/guild/{gid}/dossier?block={bid}&msg=" + err.replace(" ", "+") + "&kind=err")
            # r33: если id ответов пересоздались (форма без скрытых ans_id),
            # перезапускаем ссылки after по позициям, чтобы связи не гнили.
            old_ids = [str(a.get("id")) for a in blk.get("answers", [])]
            new_ids = [str(a.get("id")) for a in cand["answers"]]
            remap = {o: n for o, n in zip(old_ids, new_ids) if o != n}
            if remap:
                for b in blocks.values():
                    aft = str(b.get("after") or "")
                    bpart, _, apart = aft.partition(":")
                    if bpart == bid and apart in remap:
                        b["after"] = f"{bid}:{remap[apart]}"
            blocks[bid] = cand
            log.info("Панель: досье блок %s сохранён: ответов=%d заголовок=%r", bid, len(cand["answers"]), cand["title"])
            cycles = doss_find_cycles(gid)
            if cycles:
                blocks[bid] = blk
                raise web.HTTPFound(
                    f"/guild/{gid}/dossier?block={bid}&msg=Цикл+переходов:+блоки+"
                    + "+".join(cycles) + "&kind=err")
            save_dossier()
            raise web.HTTPFound(f"/guild/{gid}/dossier?block={bid}&msg=Блок+сохранён")
        if action == "block_del":
            bid = str(_int(post.get("block")))
            if str(cfg.get("root_block")) == bid:
                raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Блок+занят+как+корневой&kind=err")
            if bid in [str(b) for b in cfg.get("core_blocks", [])]:
                raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Блок+в+ядре+—+сначала+уберите+из+ядра&kind=err")
            for b in blocks.values():
                for a in b.get("answers", []):
                    if str(a.get("next")) == bid:
                        raise web.HTTPFound(f"/guild/{gid}/dossier?msg=На+блок+ссылаются+ответы+блока+{b.get('id')}&kind=err")
            for chid, sess in DOSS_SESS.items():
                if sess.get("gid") == gid and sess.get("status") == "flow" and \
                        any(str(p[0]) == bid for p in sess.get("path", [])):
                    raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Блок+в+активной+анкете+#{chid}&kind=err")
            blocks.pop(bid, None)
            save_dossier()
            raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Блок+удалён")
    # GET: обзор или редактор блока
    edit_id = str(_int(request.query.get("block"))) if request.query.get("block") else ""
    cat_opts = "".join(
        f"<option value='{c.id}'{' selected' if c.id == cfg.get('category') else ''}>#{c.name}</option>"
        for c in guild.categories)
    role_opts = "".join(
        f"<option value='{r.id}'{' selected' if r.id == cfg.get(x) else ''}>{r.name}</option>"
        for x in ("review_role",) for r in guild.roles if not r.managed)
    canon_role_opts = "".join(
        f"<option value='{r.id}'{' selected' if r.id == cfg.get('canon_role') else ''}>{r.name}</option>"
        for r in guild.roles if not r.managed)
    block_opts = "".join(
        f"<option value='{b}'{' selected' if str(cfg.get('root_block')) == b else ''}>№{b}: {blocks[b].get('title', '')[:40]}</option>"
        for b in blocks)
    blist = "".join(
        f"<li><a href='/guild/{gid}/dossier?block={b}'>№{b}: {blocks[b].get('title', '')[:60]}</a>"
        f" • ответов: {len(blocks[b].get('answers', []))}"
        + (" • ЯДРО" if b in [str(x) for x in cfg.get('core_blocks', [])] else "")
        + (" • КОРЕНЬ" if str(cfg.get('root_block')) == b else "") + "</li>"
        for b in sorted(blocks, key=lambda x: int(x) if x.isdigit() else 0)) or "<li>блоков пока нет</li>"
    arch = "".join(
        f"<li><t:{a['ts']}:D> — <b>{a['kind']}</b>, игрок {a['uid']}, блоков: {len(a['path'])}</li>"
        for a in g["arch"][-8:][::-1]) or "<li>архив пуст</li>"
    editor = ""
    if edit_id and edit_id in blocks:
        blk = blocks[edit_id]
        rows = []
        ans = list(blk.get("answers", [])) + [None, None, None]
        for i, a in enumerate(ans):
            nxt = (a or {}).get("next", "")
            next_opts = "".join(
                f"<option value='{b}'{' selected' if str(nxt) == b else ''}>{b}</option>"
                for b in sorted(blocks, key=lambda x: int(x) if x.isdigit() else 0) if b != edit_id)
            rows.append(
                f"<div class='row'><input type='hidden' name='ans_id' value='{(a or {}).get('id', '')}'>"
                f"<input type='text' name='label' maxlength='80' placeholder='Подпись кнопки' value=\"{(a or {}).get('label', '')}\">"
                f"<textarea name='fragment' placeholder='Фрагмент шаблона при этом ответе'>{(a or {}).get('fragment', '')}</textarea>"
                f"<select name='next'><option value=''>— финиш / по ядру —</option>{next_opts}</select>"
                f"<label><input type='checkbox' name='canon' value='c{i}'{' checked' if (a or {}).get('canon') else ''}> канон</label>"
                f"<label><input type='checkbox' name='self' value='s{i}'{' checked' if (a or {}).get('self') else ''}> самописный</label>"
                "<button type='button' class='ghost rmrow'>× строку</button></div>")
        after_cur = str(blk.get("after") or "")
        _ao = []
        for b in sorted(blocks, key=lambda x: int(x) if x.isdigit() else 0):
            for a in blocks[b].get("answers", []):
                val = f"{b}:{a.get('id')}"
                sel = " selected" if after_cur == val else ""
                lab = (a.get("label") or "")[:30]
                _ao.append(f"<option value='{val}'{sel}>после ответа «{lab}» (блок №{b})</option>")
        after_opts = "".join(_ao)
        next_opts_base = "".join(
            f"<option value='{b}'>№{b}: {blocks[b].get('title', '')[:30]}</option>"
            for b in sorted(blocks, key=lambda x: int(x) if x.isdigit() else 0) if b != edit_id)
        row_tpl = (
            "<template id='blk-row-tpl'><div class='row'>"
            "<input type='hidden' name='ans_id' value=''>"
            "<input type='text' name='label' maxlength='80' placeholder='Подпись кнопки'>"
            "<textarea name='fragment' placeholder='Фрагмент шаблона при этом ответе'></textarea>"
            f"<select name='next'><option value=''>— финиш / по ядру —</option>{next_opts_base}</select>"
            "<label><input type='checkbox' name='canon' value='cN'> канон</label>"
            "<label><input type='checkbox' name='self' value='sN'> самописный</label>"
            "<button type='button' class='ghost rmrow'>× строку</button></div></template>")
        editor = (
            f"<div id='blk-editor'><div class='card'><h2>Блок №{edit_id}</h2>"
            f"<form method='post' id='blk-form'>{csrf_input(session)}<input type='hidden' name='form' value='blk'>"
            f"<input type='hidden' name='block' value='{edit_id}'>"
            f"<label>Заголовок</label><input type='text' name='title' maxlength='80' value=\"{blk.get('title', '')}\">"
            f"<label>Текст вопроса</label><textarea name='text'>{blk.get('text', '')}</textarea>"
            f"<label>Показывать блок только после ответа (зависимость)</label>"
            f"<select name='after'><option value=''>— всегда по маршруту —</option>{after_opts}</select>"
            "<label>Ответы: подпись кнопки, фрагмент шаблона, следующий блок, флаги корневого выбора. "
            "Строки добавляются кнопкой «+ ответ» без перезагрузки</label>"
            + "".join(rows) + row_tpl +
            "<p><button type='button' class='ghost' id='addrow'>+ ответ</button></p>"
            "<p><button form='blk-form' name='action' value='block_save'>Сохранить блок</button></p></form>"
            f"<form method='post' id='del-form'>{csrf_input(session)}<input type='hidden' name='form' value='del'>"
            f"<input type='hidden' name='block' value='{edit_id}'>"
            "<p><button form='del-form' class='ghost' name='action' value='block_del'>Удалить блок</button></p></form></div></div>")
    howto = (
        "<div class='card'><h2>Как собрать схему анкет (пошагово)</h2><ol style='margin:0;padding-left:20px'>"
        "<li>«Создать блок» — заведите блоки-вопросы: корневой («Каноничный или самописный?»), "
        "блоки ядра (сторона, фракция, псайкер…) и ветви (подфракции сторон).</li>"
        "<li>В редакторе блока заполните ответы: <b>подпись</b> — текст кнопки; <b>фрагмент</b> — "
        "кусок будущей анкеты, который попадёт в шаблон при этом ответе; <b>следующий блок</b> — "
        "куда вести дальше (пусто — у ядерных блоков следующий по ядру, у прочих финиш).</li>"
        "<li>В корневом блоке отметьте флаги: у ответа «каноничный» — <b>канон</b>, "
        "у ответа «самописный» — <b>самописный</b>.</li>"
        "<li>В настройках модуля укажите: категорию приватных каналов, роль проверяющих, "
        "допуск канона (роль и/или ранг), <b>корневой блок</b> и <b>ядро</b> — id блоков ядра "
        "через запятую в порядке опроса.</li>"
        "<li>«Сохранить настройки», затем в Discord: <code>/анкета включить</code> и "
        "<code>/анкета панель channel:#канал</code>. Схема защиты не даст сохранить цикл "
        "переходов и удалить блок, на который кто-то ссылается.</li>"
        "<li>Игрок проходит блоки кнопками; в финале бот склеивает шаблон из фрагментов "
        "вокруг шапки/подвала и тут же отправляет в канал правила анкет (карточка «Правила анкет»), "
        "канал остаётся открытым, роль проверяющих получает пинг.</li>"
        "</ol></div>")
    if request.query.get("only") == "editor":
        return web.Response(text=editor or "<div id='blk-editor'></div>", content_type="text/html")
    body = (
        flash_block(request)
        + howto
        + "<div class='card'><h2>Модуль анкет</h2><div class='sub'>команды: /анкета панель • включить • "
          "настройки • список • продолжить • закрыть • шаблон • правила</div>"
        f"<form method='post' id='cfg-form'>{csrf_input(session)}<input type='hidden' name='form' value='cfg'>"
        f"<label><input type='checkbox' name='enabled'{' checked' if cfg.get('enabled') else ''}> Включить анкеты</label>"
        f"<label>Категория приватных каналов</label><select name='category'><option value='0'>— не задана —</option>{cat_opts}</select>"
        f"<label>Роль проверяющих (пинг о выдаче шаблона и о каноне)</label><select name='review_role'><option value='0'>— не задана —</option>{role_opts}</select>"
        f"<label>Роль допуска каноничных героев (или ранг ниже)</label><select name='canon_role'><option value='0'>— не задана —</option>{canon_role_opts}</select>"
        f"<label>Минимальный ранг для канона (0 — только роль)</label><input type='number' name='canon_level' min='0' max='100' value='{int(cfg.get('canon_level') or 0)}'>"
        f"<label>Корневой блок (каноничный / самописный)</label><select name='root_block'><option value=''>— не задан —</option>{block_opts}</select>"
        f"<label>Ядро: id блоков в порядке опроса (через запятую или клик по чипу)</label>"
        f"<input type='text' name='core_blocks' value='{','.join(str(b) for b in cfg.get('core_blocks', []))}'>"
        "<div style='margin:6px 0'>" + "".join(
            f"<button type='button' class='ghost core-add' data-id='{b}' "
            f"style='margin:2px'>+ №{b}: {(blocks[b].get('title') or '')[:24]}</button>"
            for b in sorted(blocks, key=lambda x: int(x) if x.isdigit() else 0)) + "</div>"
        f"<label>Шапка шаблона (плейсхолдер {{user}})</label><textarea name='header'>{cfg.get('header', '')}</textarea>"
        f"<label>Подвал шаблона</label><textarea name='footer'>{cfg.get('footer', '')}</textarea>"
        "<p><button form='cfg-form' name='action' value='save_cfg'>Сохранить настройки</button></p></form></div>"
        + "<div class='card'><h2>Правила анкет</h2>"
          "<div class='sub'>бот отправляет этот текст в канал анкеты сразу после её формирования: "
          "на самописном пути — после выдачи шаблона, на каноничном — после завершения блоков; "
          "длинный текст сам режется на сообщения по лимиту Discord (2000 знаков)</div>"
        f"<form method='post' id='rules-form'>{csrf_input(session)}<input type='hidden' name='form' value='rules'>"
        f"<label><input type='checkbox' name='rules_send'{' checked' if cfg.get('rules_send', True) else ''}> "
        "Отправлять правила после формирования анкеты</label>"
        "<label>Текст правил (пустой текст — ничего не отправлять)</label>"
        f"<textarea name='rules_text' style='min-height:360px'>{cfg.get('rules_text', DEFAULT_DOSS_RULES)}</textarea>"
        "<p><button form='rules-form' name='action' value='save_rules'>Сохранить правила</button> "
        "<button form='rules-form' class='ghost' name='action' value='rules_default'>Сбросить к заводским</button>"
        "</p></form></div>"
        + f"<div class='card'><h2>Блоки схемы</h2><ul>{blist}</ul>"
        f"<form method='post' id='new-form'>{csrf_input(session)}<input type='hidden' name='form' value='new'>"
        "<label>Заголовок нового блока</label>"
        "<input type='text' name='title' maxlength='80' placeholder='Сторона конфликта'>"
        "<p><button form='new-form' name='action' value='block_new'>Создать блок</button></p></form></div>"
        + editor
        + f"<div class='card'><h2>Архив анкет</h2><ul>{arch}</ul></div>"
    )
    return web.Response(text=dash_page("Досье", body, session, guild_tabs(gid, "dossier"), guild=guild), content_type="text/html")

async def dash_news(request):
    """Новости: черновики, публикация сразу/по таймеру, правка опубликованного."""
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    g = news_guild(gid)
    cfg = g["cfg"]
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/news?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:новости", post.get("action", ""))
        fmap = {"ncfg": {"save_cfg"}, "ndraft": {"save_draft", "publish", "schedule"},
                "ndel": {"del_draft"}, "ncancel": {"cancel"}, "nedit": {"edit_pub"}}
        marker = post.get("form", "")
        action = post.get("action", "")
        if marker in fmap:
            if not action and len(fmap[marker]) == 1:
                action = next(iter(fmap[marker]))
            elif action not in fmap[marker]:
                raise web.HTTPFound(f"/guild/{gid}/news?msg=Кнопка+не+из+этой+формы+«{action}»:"
                                    f"+действие+проигнорировано&kind=err")
        else:
            action = action or "save_cfg"
        if action == "save_cfg":
            cfg["channels"] = [int(x) for x in post.getall("channels", []) if str(x).isdigit()]
            cfg["ping_role"] = _int(post.get("ping_role")) or None
            save_news()
            log.info("Панель: новости save_cfg сохранено: channels=%s ping=%s", cfg["channels"], cfg["ping_role"])
            raise web.HTTPFound(f"/guild/{gid}/news?msg=Каналы+новостей+сохранены")
        if action == "save_draft":
            did = str(_int(post.get("draft")))
            d = g["drafts"].get(did)
            if did and did != "0" and d is None:
                raise web.HTTPFound(f"/guild/{gid}/news?msg=Черновик+не+найден&kind=err")
            title = (post.get("title") or "").strip()
            text = (post.get("text") or "").strip()
            image = (post.get("image") or "").strip()
            err = news_validate(title, text, image)
            if err:
                raise web.HTTPFound(f"/guild/{gid}/news?msg=" + err.replace(" ", "+") + "&kind=err")
            if not d or did in ("", "0"):
                nid = g["seq"]
                g["seq"] += 1
                d = {"id": nid, "created": int(time.time())}
                g["drafts"][str(nid)] = d
                did = str(nid)
            d.update({"title": title, "text": text, "image": image})
            save_news()
            raise web.HTTPFound(f"/guild/{gid}/news?draft={did}&msg=Черновик+сохранён")
        if action in ("publish", "schedule"):
            did = str(_int(post.get("draft")))
            d = g["drafts"].get(did)
            if not d:
                raise web.HTTPFound(f"/guild/{gid}/news?msg=Черновик+не+найден&kind=err")
            off = board_utc_offset(get_config(gid))
            when = post.get("when") or ""
            if action == "schedule" and not when:
                when = post.get("when_dt") or ""
            if not cfg.get("channels"):
                raise web.HTTPFound(f"/guild/{gid}/news?draft={did}&msg=Сначала+настройте+каналы+новостей+"
                                    f"(чекбоксы+выше):+черновик+сохранён&kind=err")
            ts, err = news_parse_when(when.replace("T", " ")[:16] if "T" in when else when, off)
            if err:
                raise web.HTTPFound(f"/guild/{gid}/news?draft={did}&msg=" + err.replace(" ", "+") + "&kind=err")
            entry = dict(d, msgs={}, status="scheduled" if ts else "sent", publish_at=ts or 0)
            g["published"].append(entry)
            del g["published"][:-60]
            g["drafts"].pop(did, None)
            save_news()
            if ts:
                raise web.HTTPFound(f"/guild/{gid}/news?msg=Новость+выйдет+<t:{ts}:F>")
            warn = await news_send(guild, entry)
            raise web.HTTPFound(f"/guild/{gid}/news?msg=Опубликовано+в+{len(entry.get('msgs', {}))}+кан."
                                + ("+" + warn.replace(" ", "+") if warn else ""))
        if action == "del_draft":
            g["drafts"].pop(str(_int(post.get("draft"))), None)
            save_news()
            raise web.HTTPFound(f"/guild/{gid}/news?msg=Черновик+удалён")
        if action == "cancel":
            entry = next((e for e in g["published"]
                          if e["id"] == _int(post.get("news")) and e.get("status") == "scheduled"), None)
            if entry:
                g["published"].remove(entry)
                g["drafts"][str(entry["id"])] = {k: entry[k] for k in ("id", "title", "text", "image") if k in entry}
                g["drafts"][str(entry["id"])]["created"] = int(time.time())
                save_news()
            raise web.HTTPFound(f"/guild/{gid}/news?msg=Отложенная+новость+возвращена+в+черновики")
        if action == "edit_pub":
            entry = next((e for e in g["published"] if e["id"] == _int(post.get("news"))), None)
            if not entry:
                raise web.HTTPFound(f"/guild/{gid}/news?msg=Новость+не+найдена&kind=err")
            title = (post.get("title") or entry["title"]).strip()
            text = (post.get("text") or entry["text"]).strip()
            image = (post.get("image") or "").strip() or entry.get("image", "")
            err = news_validate(title, text, image)
            if err:
                raise web.HTTPFound(f"/guild/{gid}/news?msg=" + err.replace(" ", "+") + "&kind=err")
            done = await news_edit_published(guild, entry, text, title, image)
            raise web.HTTPFound(f"/guild/{gid}/news?msg=Обновлено+сообщений:+{done}")
    edit_id = str(_int(request.query.get("draft"))) if request.query.get("draft") else ""
    d = g["drafts"].get(edit_id) or {"id": 0, "title": "", "text": "", "image": ""}
    ch_boxes = "".join(
        f"<label><input type='checkbox' name='channels' value='{c.id}'"
        f"{' checked' if c.id in cfg.get('channels', []) else ''}> #{c.name}</label>"
        for c in guild.text_channels)
    role_opts = "".join(
        f"<option value='{r.id}'{' selected' if r.id == cfg.get('ping_role') else ''}>{r.name}</option>"
        for r in guild.roles if not r.managed)
    drafts = "".join(
        f"<li><a href='/guild/{gid}/news?draft={x['id']}'>№{x['id']}: {x['title'][:60]}</a></li>"
        for x in sorted(g["drafts"].values(), key=lambda z: z["id"])) or "<li>черновиков нет</li>"
    pub = "".join(
        f"<li>№{e['id']} <b>{e['title'][:50]}</b> — "
        + (f"запланирована <t:{int(e['publish_at'])}:F>" if e.get("status") == "scheduled"
           else f"опубликована <t:{e.get('ts', 0)}:D> в {len(e.get('msgs', {}))} кан.")
        + f"<form method='post' style='display:inline' id='ncan{e['id']}'>{csrf_input(session)}"
        f"<input type='hidden' name='form' value='ncancel'>"
        f"<input type='hidden' name='news' value='{e['id']}'>"
        + (f"<button form='ncan{e['id']}' class='ghost' name='action' value='cancel'>Отменить</button>"
           if e.get("status") == "scheduled" else "")
        + f"<button class='ghost' name='action' value='edit_pub' form='pubform{e['id']}'>Правка формой ниже</button>"
        + "</form></li>"
        for e in g["published"][-8:][::-1]) or "<li>новостей ещё не было</li>"
    body = (
        flash_block(request)
        + "<div class='card'><h2>Каналы и пинг</h2><div class='sub'>команды: /новости черновик • "
          "опубликовать • каналы • роль • правка • история</div>"
        f"<form method='post' id='ncfg-form'>{csrf_input(session)}<input type='hidden' name='form' value='ncfg'>"
        "<label>Каналы публикации</label>{ch_boxes}"
        f"<label>Роль-пинг</label><select name='ping_role'><option value='0'>— без пинга —</option>{role_opts}</select>"
        "<p><button form='ncfg-form' name='action' value='save_cfg'>Сохранить</button></p></form></div>"
        + f"<div class='card'><h2>Черновик {('№' + str(d['id'])) if d['id'] else '(новый)'}</h2>"
        f"<form method='post' id='ndraft-form'>{csrf_input(session)}<input type='hidden' name='form' value='ndraft'>"
        f"<input type='hidden' name='draft' value='{d['id']}'>"
        f"<label>Заголовок</label><input type='text' name='title' maxlength='256' value=\"{d['title']}\">"
        f"<label>Текст</label><textarea name='text'>{d['text']}</textarea>"
        f"<label>Изображение (ссылка https)</label><input type='text' name='image' value=\"{d['image']}\">"
        "<p><button form='ndraft-form' name='action' value='save_draft'>Сохранить черновик</button> "
        "<button form='ndraft-form' name='action' value='publish'>Опубликовать сейчас</button></p>"
        "<label>Или отложить: «45» (минуты) / «2026-10-24 20:00»</label>"
        "<input type='text' name='when' placeholder='45'>"
        "<p><button form='ndraft-form' class='ghost' name='action' value='schedule'>Запланировать</button></p></form>"
        f"<form method='post' id='ndel-form'>{csrf_input(session)}<input type='hidden' name='form' value='ndel'>"
        f"<input type='hidden' name='draft' value='{d['id']}'>"
        "<p><button form='ndel-form' class='ghost' name='action' value='del_draft'>Удалить черновик</button></p></form></div>"
        + f"<div class='card'><h2>Черновики</h2><ul>{drafts}</ul></div>"
        + f"<div class='card'><h2>Лента</h2><ul>{pub}</ul>"
    )
    for e in g["published"][-8:][::-1]:
        body += (f"<form method='post' id='pubform{e['id']}'>{csrf_input(session)}"
                 f"<input type='hidden' name='form' value='nedit'>"
                 f"<input type='hidden' name='news' value='{e['id']}'>"
                 f"<label>Правка №{e['id']}: заголовок</label><input type='text' name='title' maxlength='256' value=\"{e['title']}\">"
                 f"<label>Текст</label><textarea name='text'>{e['text']}</textarea>"
                 f"<label>Изображение</label><input type='text' name='image' value=\"{e.get('image', '')}\">"
                 "<p><button name='action' value='edit_pub'>Обновить сообщения</button></p></form>")
    body += "</div>"
    return web.Response(text=dash_page("Новости", body, session, guild_tabs(gid, "news"), guild=guild), content_type="text/html")

async def dash_welcome(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    cfg = get_config(gid)
    preview = ""
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/welcome?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:посвящения", post.get("action", "save"))
        action = post.get("action", "save")
        if action == "save":
            cfg["welcome_enabled"] = "enabled" in post
            cfg["welcome_channel"] = _int(post.get("channel")) or None
            cfg["welcome_text"] = (post.get("text", "") or DEFAULT_WELCOME_TEXT)[:1500]
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/welcome?msg=Посвящения+сохранены")
        if action == "preview":
            sample = (post.get("text", "") or cfg["welcome_text"]).format(
                mention="@Новичок", user="Новичок", server=guild.name, count=guild.member_count or 0
            )
            preview = "<div class='card'><h2>Предпросмотр</h2><p>" + sample.replace("<", "&lt;") + "</p></div>"
    ch_opts = "".join(
        f"<option value='{c.id}'{' selected' if c.id == cfg['welcome_channel'] else ''}>#{c.name}</option>"
        for c in guild.text_channels
    )
    body = (
        flash_block(request) + preview
        + f"<div class='card'><h2>Посвящение новичков</h2><div class='sub'>команды: /приветствие … (нужен Server Members Intent)</div>"
        f"<form method='post'>{csrf_input(session)}"
        f"<label><input type='checkbox' name='enabled'{' checked' if cfg['welcome_enabled'] else ''}> Включить посвящения</label>"
        f"<label>Канал посвящений</label><select name='channel'><option value='0'>— не назначен —</option>{ch_opts}</select>"
        "<label>Текст (плейсхолдеры: {mention} {user} {server} {count})</label>"
        f"<textarea name='text' maxlength='1500'>{cfg['welcome_text']}</textarea>"
        "<p><button name='action' value='save'>Сохранить</button> "
        "<button class='ghost' name='action' value='preview'>Предпросмотр</button></p></form></div>"
    )
    return web.Response(text=dash_page("Посвящения", body, session, guild_tabs(gid, "welcome"), guild=guild), content_type="text/html")

async def dash_admins(request):
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    cfg = get_config(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/admins?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:админы", post.get("action", "save"))
        action = post.get("action", "")
        if action == "add":
            uid = _int(post.get("user"))
            if uid and uid not in cfg["admin_ids"]:
                cfg["admin_ids"].append(uid)
                save_configs()
            raise web.HTTPFound(f"/guild/{gid}/admins?msg=Советник+призван")
        if action == "remove":
            uid = _int(post.get("user"))
            if uid in cfg["admin_ids"]:
                cfg["admin_ids"].remove(uid)
                save_configs()
            raise web.HTTPFound(f"/guild/{gid}/admins?msg=Советник+отпущен")
    rows = ""
    for uid in cfg.get("admin_ids", []):
        m = guild.get_member(uid)
        rows += (
            f"<tr><td>{m.display_name if m else uid}</td><td>{uid}</td>"
            f"<td><form method='post' style='display:inline'>{csrf_input(session)}"
            f"<input type='hidden' name='action' value='remove'><input type='hidden' name='user' value='{uid}'>"
            "<button class='danger'>Снять</button></form></td></tr>"
        )
    empty_note = (
        "<p><span class='pill off'>Список пуст: настраивать могут владелец сервера и все с правом «Управление сервером».</span></p>"
        if not cfg.get("admin_ids") else ""
    )
    table = f"<table><tr><th>Имя</th><th>ID</th><th></th></tr>{rows}</table>" if cfg.get("admin_ids") else ""
    members = sorted([m for m in guild.members if not m.bot], key=lambda m: m.display_name.lower())[:200]
    mem_opts = "".join(f"<option value='{m.id}'>{m.display_name}</option>" for m in members)
    body = (
        flash_block(request)
        + f"<div class='card'><h2>Админы бота</h2><div class='sub'>команды: /админы добавить / убрать</div>"
        + empty_note + table
        + f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='add'>"
        f"<label>Участник сервера</label><select name='user'>{mem_opts}</select>"
        "<p><button>Назначить администратором бота</button></p></form>"
        "<p class='sub'>Как только список не пуст, настраивать бота и эту панель могут только люди из списка "
        "и владелец приложения-бота.</p></div>"
    )
    return web.Response(text=dash_page("Админы", body, session, guild_tabs(gid, "admins"), guild=guild), content_type="text/html")

@bot.event
async def on_interaction(interaction: discord.Interaction):
    """Аудит: слэш-команды и нажатия кнопок (без содержимого сообщений)."""
    if interaction.guild_id is None:
        return
    try:
        if interaction.type is discord.InteractionType.application_command:
            opts = interaction.data.get("options") or []
            parts = []
            if opts and opts[0].get("type") in (1, 2):
                parts.append(opts[0].get("name", ""))
                inner = opts[0].get("options") or []
                if opts[0].get("type") == 2 and inner:
                    parts.append(inner[0].get("name", ""))
            name = "/" + interaction.data.get("name", "?") + (f" {' '.join(parts)}" if parts else "")
            audit(interaction.guild_id, interaction.user.id, name)
        elif interaction.type is discord.InteractionType.component:
            audit(interaction.guild_id, interaction.user.id,
                  "кнопка", (interaction.data.get("custom_id") or "")[:40])
    except Exception:
        pass

async def dash_healthz(request):
    """Публичная.health-строчка: сборка и признаки жизни (без секретов и токенов)."""
    return web.json_response({
        "build": BOT_BUILD,
        "guilds": len(bot.guilds),
        "data_writable": os.access(DATA_DIR, os.W_OK),
        "dashboard": bool(DISCORD_CLIENT_SECRET),
        "modules": {
            "quotes": sum(1 for c in CONFIGS.values() if c.get("enabled")),
            "board": sum(1 for c in CONFIGS.values() if c.get("board_enabled")),
            "dossier_guilds": len([g for g in DOSSIER.values() if g.get("cfg", {}).get("enabled")]),
            "news_drafts": sum(len(g.get("drafts", {})) for g in NEWS.values()),
        },
    })

async def dash_favicon(request):
    """Иконка вкладки панели (устраняет 404 на /favicon.ico)."""
    path = os.path.join(WEB_DIR, "favicon.ico")
    if os.path.exists(path):
        return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})
    raise web.HTTPNotFound()

async def dash_migration_threads(request):
    """HTML-фрагмент чекбоксов публикаций форума (для подгрузки без перезагрузки)."""
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    sg = bot.get_guild(_int(request.query.get("src")))
    ch = sg.get_channel(_int(request.query.get("sch"))) if sg else None
    if not isinstance(ch, discord.ForumChannel):
        return web.Response(text="<p class='sub'>Выберите форум — и здесь появятся его публикации.</p>",
                            content_type="text/html")
    return web.Response(text=await _threads_boxes(ch), content_type="text/html")

# --------------------------------------------------------------------------- #
# Разделы панели r19: роли за реакции, тикеты, аудит
# --------------------------------------------------------------------------- #
def _role_name(guild, rid: int) -> str:
    """Человеческое имя роли для таблиц панели."""
    r = guild.get_role(rid) if hasattr(guild, "get_role") else None
    return f"@{r.name}" if r is not None and hasattr(r, "name") else f"роль {rid}"


async def _rr_sync_message(guild, panel: dict, add: str = "", remove: str = "") -> None:
    """Обновить embed панели ролей и поставить/снять эмодзи, если сообщение живо."""
    ch = guild.get_channel(panel.get("channel"))
    if ch is None:
        return
    try:
        msg = await ch.fetch_message(panel.get("message"))
        await msg.edit(embed=rr_panel_embed(panel))
        if add:
            pe = _rr_resolve_emoji(guild, add)
            if pe is not None:
                await msg.add_reaction(pe)
        if remove:
            pe = _rr_resolve_emoji(guild, remove)
            if pe is not None:
                await msg.clear_reaction(pe)
    except Exception as exc:
        log.debug("Панель ролей: сообщение не обновлено: %s", exc)




async def dash_tickets(request):
    """Тикеты: настройки (категория, роль поддержки, лог) и публикация панели."""
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    cfg = get_config(gid)
    data = tk_guild(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/tickets?msg=Ошибка+CSRF&kind=err")
        action = post.get("action", "save")
        audit(gid, session["uid"], "панель:тикеты", action)
        if action == "save":
            cfg["ticket_category"] = _int(post.get("category")) or None
            cfg["ticket_role"] = _int(post.get("role")) or None
            cfg["ticket_log"] = _int(post.get("log")) or None
            cfg["ticket_text"] = (post.get("text") or DEFAULT_TICKET_TEXT)[:800]
            save_configs()
            raise web.HTTPFound(f"/guild/{gid}/tickets?msg=Настройки+тикетов+сохранены")
        if action == "publish":
            if not cfg.get("ticket_category"):
                raise web.HTTPFound(f"/guild/{gid}/tickets?msg=Сначала+задайте+категорию+тикетов&kind=err")
            ch = guild.get_channel(_int(post.get("channel")))
            if ch is None:
                raise web.HTTPFound(f"/guild/{gid}/tickets?msg=Выберите+канал&kind=err")
            text = (post.get("panel_text") or "")[:800] or \
                "Поддержка кузни: нажмите кнопку ниже — бот создаст приватный канал тикета."
            embed = discord.Embed(title="🎫 Поддержка", description=text, color=EMBED_COLOR)
            embed.set_footer(text=FOOTER_TEXT)
            try:
                msg = await ch.send(embed=embed, view=TicketOpenView(gid))
            except Exception:
                raise web.HTTPFound(f"/guild/{gid}/tickets?msg=Не+удалось+опубликовать+панель&kind=err")
            data["panels"].append({"channel": ch.id, "message": getattr(msg, "id", 0)})
            save_tickets()
            raise web.HTTPFound(f"/guild/{gid}/tickets?msg=Панель+тикетов+опубликована")
    cat_opts = "".join(
        f"<option value='{c.id}'{opt(cfg.get('ticket_category'), c.id)}>{c.name}</option>"
        for c in getattr(guild, "categories", []))
    role_opts = "".join(
        f"<option value='{r.id}'{opt(cfg.get('ticket_role'), r.id)}>{r.name}</option>"
        for r in sorted(guild.roles, key=lambda r: r.name) if r.id != gid)
    log_opts = "".join(
        f"<option value='{c.id}'{opt(cfg.get('ticket_log'), c.id)}>#{c.name}</option>"
        for c in guild.text_channels)
    pub_opts = "".join(f"<option value='{c.id}'>#{c.name}</option>" for c in guild.text_channels)
    panel_rows = ""
    for p in data["panels"]:
        ch = guild.get_channel(p.get("channel"))
        ch_name = f"#{ch.name}" if ch is not None and hasattr(ch, "name") else "канал удалён"
        link = f"https://discord.com/channels/{gid}/{p.get('channel')}/{p.get('message')}"
        panel_rows += (f"<tr><td>{ch_name}</td>"
                       f"<td><a href='{link}' target='_blank' rel='noopener'>открыть сообщение</a></td></tr>")
    ticket_rows = ""
    for cid, t in data["tickets"].items():
        ch = guild.get_channel(_int(cid))
        m = guild.get_member(t.get("owner")) if hasattr(guild, "get_member") else None
        who = m.display_name if m is not None and hasattr(m, "display_name") else t.get("owner")
        when = time.strftime("%d.%m %H:%M", time.localtime(t.get("created", 0)))
        ch_name = f"#{ch.name}" if ch is not None and hasattr(ch, "name") else "канал удалён"
        ticket_rows += f"<tr><td>№{t.get('id')}</td><td>{ch_name}</td><td>{who}</td><td>{when}</td></tr>"
    body = (
        flash_block(request)
        + "<div class='card'><h2>Настройки тикетов</h2><div class='sub'>команды: /тикеты настройки • панель • закрыть</div>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='save'>"
        f"<div class='row'><div><label>Категория тикетов</label><select name='category'><option value='0'>— не задана —</option>{cat_opts}</select></div>"
        f"<div><label>Роль поддержки</label><select name='role'><option value='0'>— не задана —</option>{role_opts}</select></div></div>"
        f"<div class='row'><div><label>Лог-канал (транскрипты)</label><select name='log'><option value='0'>— не задан —</option>{log_opts}</select></div></div>"
        "<label>Текст в новом тикете</label>"
        f"<textarea name='text' maxlength='800'>{cfg.get('ticket_text') or DEFAULT_TICKET_TEXT}</textarea>"
        "<p><button>Сохранить настройки</button></p></form></div>"
        + "<div class='card'><h2>Панель с кнопкой</h2><div class='sub'>бот отправит embed с кнопкой «Создать тикет» в выбранный канал</div>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='publish'>"
        f"<label>Канал публикации</label><select name='channel'>{pub_opts}</select>"
        "<label>Текст на панели (необязательно)</label>"
        "<textarea name='panel_text' maxlength='800' placeholder='Поддержка кузни: нажмите кнопку ниже…'></textarea>"
        "<p><button>Опубликовать панель</button></p></form>"
        + (f"<table><tr><th>Канал</th><th>Сообщение</th></tr>{panel_rows}</table>" if panel_rows else "")
        + "</div>"
        + f"<div class='card'><h2>Открытые тикеты</h2><div class='sub'>всего: {len(data['tickets'])}</div>"
        + (f"<table><tr><th>№</th><th>Канал</th><th>Автор</th><th>Создан</th></tr>{ticket_rows}</table>"
           if ticket_rows else "<p><span class='pill off'>Открытых тикетов нет.</span></p>")
        + "</div>"
    )
    return web.Response(text=dash_page("Тикеты", body, session, guild_tabs(gid, "tickets"), guild=guild), content_type="text/html")


async def dash_audit(request):
    """Аудит кузни: канал журнала, группы событий и отчёт безопасности."""
    gid = _int(request.match_info["gid"])
    session, guild, _ = await dash_guard(request, gid)
    cfg = get_config(gid)
    if request.method == "POST":
        post = await request.post()
        if post.get("csrf") != csrf_token(session):
            raise web.HTTPFound(f"/guild/{gid}/audit?msg=Ошибка+CSRF&kind=err")
        audit(gid, session["uid"], "панель:аудит", post.get("action", "save"))
        cfg["audit_channel"] = _int(post.get("channel")) or None
        events = cfg.setdefault("audit_events", {"members": True, "messages": True, "server": True})
        events["members"] = "members" in post
        events["messages"] = "messages" in post
        events["server"] = "server" in post
        save_configs()
        raise web.HTTPFound(f"/guild/{gid}/audit?msg=Настройки+журнала+сохранены")
    ch_opts = "".join(f"<option value='{c.id}'{opt(cfg.get('audit_channel'), c.id)}>#{c.name}</option>"
                      for c in guild.text_channels)
    ev = cfg.get("audit_events") or {}
    marks = {2: "✅", 1: "⚠️", 0: "❌"}
    checks = areport_checks(guild, cfg)
    score = areport_score(checks)
    verdict = ("отлично" if score >= 85 else "хорошо" if score >= 65
               else "требует внимания" if score >= 40 else "критично")
    rows = "".join(f"<tr><td>{marks.get(s, '?')}</td><td><b>{t}</b></td><td>{d}</td></tr>"
                   for s, t, d, _w in checks)
    body = (
        flash_block(request)
        + "<div class='card'><h2>Журнал событий</h2><div class='sub'>команды: /аудит настройки • отчёт</div>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='action' value='save'>"
        f"<label>Канал журнала</label><select name='channel'><option value='0'>— не задан —</option>{ch_opts}</select>"
        "<label>Группы событий</label>"
        f"<label><input type='checkbox' name='members'{' checked' if ev.get('members') else ''}> Участники (вход, выход, баны, роли)</label>"
        f"<label><input type='checkbox' name='messages'{' checked' if ev.get('messages') else ''}> Сообщения (удаление, правки)</label>"
        f"<label><input type='checkbox' name='server'{' checked' if ev.get('server') else ''}> Сервер (каналы, роли, инвайты)</label>"
        "<p class='sub'>Удалённые сообщения пишутся и после перезапуска бота (кэш на диске), "
        "массовые очистки — одной записью; «кто удалил» — из журнала аудита Discord "
        "(право «Просмотр журнала аудита»), текст сообщений — при Env INTENTS_CONTENT=1.</p>"
        "<p><button>Сохранить</button></p></form></div>"
        + "<div class='card'><h2>Отчёт безопасности</h2>"
        + f"<div class='sub'>оценка: <b>{score}/100 — {verdict}</b> • та же проверка, что и /аудит отчёт</div>"
        + f"<table><tr><th></th><th>Проверка</th><th>Подробности</th></tr>{rows}</table></div>"
    )
    return web.Response(text=dash_page("Аудит", body, session, guild_tabs(gid, "audit"), guild=guild), content_type="text/html")


def build_dashboard_app() -> web.Application:
    app = web.Application(middlewares=[_req_middleware, _dash_log_redirect, _dash_safe])
    app.router.add_get("/", dash_index)
    app.router.add_get("/login", dash_login)
    app.router.add_get("/callback", dash_callback)
    app.router.add_get("/logout", dash_logout)
    app.router.add_get("/guilds", dash_guilds)
    app.router.add_get("/guild/{gid}", dash_home)
    app.router.add_get("/guild/{gid}/", dash_home)  # ссылки вида /guild/<id>/ (с хвостовым слэшем)
    app.router.add_route("*", "/guild/{gid}/quotes", dash_quotes)
    app.router.add_route("*", "/guild/{gid}/progress", dash_progress)
    app.router.add_route("*", "/guild/{gid}/board", dash_board)
    app.router.add_route("*", "/guild/{gid}/dossier", dash_dossier)
    app.router.add_route("*", "/guild/{gid}/news", dash_news)
    app.router.add_route("*", "/guild/{gid}/shop", dash_shop)
    app.router.add_route("*", "/guild/{gid}/loot", dash_loot)
    app.router.add_route("*", "/guild/{gid}/giveaways", dash_giveaways)
    app.router.add_route("*", "/guild/{gid}/welcome", dash_welcome)
    app.router.add_route("*", "/guild/{gid}/admins", dash_admins)
    app.router.add_route("*", "/guild/{gid}/migration", dash_migration)
    app.router.add_route("*", "/guild/{gid}/tickets", dash_tickets)
    app.router.add_route("*", "/guild/{gid}/audit", dash_audit)
    app.router.add_get("/guild/{gid}/migration/threads", dash_migration_threads)
    app.router.add_get("/favicon.ico", dash_favicon)
    app.router.add_get("/healthz", dash_healthz)
    if os.path.isdir(WEB_DIR):
        app.router.add_static("/static", WEB_DIR, show_index=False)
    return app

_DASH_RUNNER = None  # текущий AppRunner дашборда (для корректной остановки)

async def start_dashboard():
    global _DASH_RUNNER
    if not DISCORD_CLIENT_SECRET:
        log.warning(
            "Дашборд отключён: не задана переменная окружения DISCORD_CLIENT_SECRET "
            "(OAuth2 Client Secret из Developer Portal). Бот работает без панели."
        )
        return
    runner = web.AppRunner(build_dashboard_app())
    await runner.setup()
    _DASH_RUNNER = runner
    site = web.TCPSite(runner, "0.0.0.0", DASH_PORT)
    await site.start()
    log.info("Дашборд запущен: %s (redirect URI: %s)", DASH_URL, OAUTH_REDIRECT)

async def stop_dashboard():
    """Остановить веб-сервер дашборда при завершении работы бота."""
    global _DASH_RUNNER
    if _DASH_RUNNER is not None:
        try:
            await _DASH_RUNNER.cleanup()
        except Exception as exc:
            log.debug("Остановка дашборда: %s", exc)
        _DASH_RUNNER = None

# --------------------------------------------------------------------------- #
# Точка входа
# --------------------------------------------------------------------------- #
def selftest() -> int:
    problems = []
    if not CANON:
        problems.append("пустая база канонических цитат")
    for i, q in enumerate(CANON):
        for field in ("text", "author", "source", "era"):
            if not q.get(field):
                problems.append(f"цитата c{i}: пусто поле {field}")
        if q.get("era") not in ERA_LABELS:
            problems.append(f"цитата c{i}: неверная эпоха {q.get('era')}")
    mult = default_multipliers()
    if len(mult) != MAX_LEVEL:
        problems.append("таблица множителей не равна 100 рангам")
    if mult[:3] != [1.0, 1.5, 2.0]:
        problems.append(f"стандартные множители начинаются неверно: {mult[:3]} (шаг 0.5)")
    if not TAROT_DECK:
        problems.append("пустая колода Аугура")
    for gid, boxes in LOOT.items():
        for b in boxes:
            for it in b.get("items", []):
                if float(it.get("chance", 0)) < 0:
                    problems.append(f"тех-арк {b.get('id')} сервера {gid}: отрицательный шанс")
    if not os.path.exists(os.path.join(WEB_DIR, "favicon.ico")):
        problems.append("нет web/favicon.ico — иконка вкладки панели (залейте файл из web/)")
    for _webf in ("avatar.png", "art_forge.jpg", "art_litany.jpg", "art_cog.jpg",
                  "art_ark.jpg", "art_conclave.jpg", "art_data.jpg"):
        if not os.path.exists(os.path.join(WEB_DIR, _webf)):
            problems.append(f"нет web/{_webf} — оформленное изображение раздела (залейте папку web/ целиком)")
    eras = {}
    for q in CANON:
        eras[q.get("era")] = eras.get(q.get("era"), 0) + 1
    print(f"Литаний в книге: {len(CANON)} | по эпохам: {eras}")
    print(f"Множители: ур.1={mult[0]:g}, ур.2={mult[1]:g}, ур.3={mult[2]:g}, ур.100={mult[99]:g}")
    print(f"Карт Аугура: {len(TAROT_DECK)}")
    print(f"Сборка: {BOT_BUILD}")
    if not DISCORD_CLIENT_SECRET:
        print("Примечание: DISCORD_CLIENT_SECRET не задан — веб-дашборд отключён (бот работает без панели).")
    else:
        print(f"Дашборд: порт {DASH_PORT}, redirect URI {OAUTH_REDIRECT}")
    if problems:
        print("ПРОБЛЕМЫ:")
        for p in problems:
            print(" -", p)
        return 1
    print("Selftest OK: данные корректны, бот готов к запуску.")
    return 0


def get_token() -> Optional[str]:
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if token:
        return token
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    return None


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    for _grp in (g_quotes, g_admins, g_xp, g_shop, g_loot, g_gw, g_welcome, g_grant, g_mig,
                 g_reaction, g_tickets, g_audit, g_board, g_doss, g_news):
        bot.tree.add_command(_grp)
    token = get_token()
    if not token:
        print(
            "НЕ НАЙДЕН ТОКЕН.\n"
            "Создайте файл token.txt рядом с bot.py и вставьте токен бота одной строкой,\n"
            "либо задайте переменную окружения DISCORD_TOKEN.\n"
            "Подробнее — в README.md (инструкция)."
        )
        sys.exit(1)
    token_source = "переменная окружения DISCORD_TOKEN" if os.environ.get("DISCORD_TOKEN", "").strip() else "файл token.txt"
    log.info("Запуск Adeptus Mechanicus… (источник токена: %s)", token_source)
    log.info("Сборка бота: %s", BOT_BUILD)
    log.info(
        "Интенты: members=%s (приветствие, тикеты, аудит), presences=%s (онлайн в дашборде). "
        "Управляются Env INTENTS_MEMBERS / INTENTS_PRESENCES.",
        bot.intents.members, bot.intents.presences,
    )
    # Аккуратное завершение работы: при любом выходе из bot.run() (Stop на хостинге,
    # KeyboardInterrupt, ошибка входа) сначала останавливаем веб-сервер дашборда и
    # закрываем HTTP-сессию переноса, и только потом закрываем сам клиент.
    # Иначе aiohttp при завершении процесса пишет «Unclosed client session».
    _orig_bot_close = bot.close

    async def _close_and_cleanup():
        msg_cache_save()  # кэш удалений переживает Restart на хостинге
        await stop_dashboard()
        await close_mig_session()
        await _orig_bot_close()

    bot.close = _close_and_cleanup
    try:
        bot.run(token)
    except discord.LoginFailure:
        log.error(
            "Токен отклонён Discord (LoginFailure). Проверьте токен: переменная DISCORD_TOKEN "
            "или файл token.txt. После Reset Token обновите значение и перезапустите бота."
        )
        sys.exit(1)
    except discord.errors.PrivilegedIntentsRequired:
        log.error(
            "Портал отклонил привилегированные интенты, несмотря на пробу. "
            "Отключите их вручную: Env INTENTS_PRESENCES=0 и/или INTENTS_MEMBERS=0, затем Restart."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
