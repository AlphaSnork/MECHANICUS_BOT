#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Функциональный харнес дашборда и связки «сайт ↔ Discord» (партия №45, r28).

Поднимает настоящий aiohttp-сервер дашборда на фейковой гильдии и ходит по нему
HTTP-запросами: прогрессия видит форумы, досье создаёт блоки и сохраняет
настройки, новости сохраняют черновики и публикуют (сразу и по сроку),
сообщения в ветках/форумах начисляют опыт и счётчики рейтинга.
Запуск: python3 check_dash.py
"""
import asyncio
import importlib.util
import json
import os
import sys

os.environ.setdefault("DISCORD_CLIENT_SECRET", "test-secret")
os.environ.setdefault("DASHBOARD_SECRET", "test-dash-secret")

spec = importlib.util.spec_from_file_location("bot", "bot.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

OK = FAIL = 0

def check(name, cond):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  OK  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")

GID = 930000000000000001
UID = 77

class FakeRole:
    managed = False
    def __init__(self, rid, name):
        self.id, self.name = rid, name
    @property
    def mention(self):
        return f"@{self.name}"

class FakeMsg:
    def __init__(self, mid):
        self.id = mid

class FakeChannel:
    def __init__(self, cid, name, kind="text"):
        self.id, self.name, self.kind = cid, name, kind
        self.sent = []
    @property
    def mention(self):
        return f"#{self.name}"
    async def send(self, content="", embed=None, file=None, allowed_mentions=None, view=None):
        self.sent.append({"content": content, "embed": embed, "file": file})
        return FakeMsg(9000 + len(self.sent))
    def permissions_for(self, member):
        return type("P", (), {"view_channel": True, "send_messages": True,
                              "embed_links": True, "manage_channels": True})()

class FakeCategory:
    def __init__(self, cid, name):
        self.id, self.name = cid, name

class FakeThread:
    def __init__(self, tid, name, parent_id):
        self.id, self.name, self.parent_id = tid, name, parent_id

class FakeMember:
    bot = False
    def __init__(self, uid, roles=()):
        self.id = uid
        self.roles = [FakeRole(r, f"r{r}") for r in roles]
        self.display_name = f"user{uid}"
        self.name = f"user{uid}"
        self.mention = f"<@{uid}>"

class FakeGuild:
    def __init__(self):
        self.id = GID
        self.name = "Тест-Кузня"
        self.member_count = 42
        self.ch_text = FakeChannel(101, "ролевой")
        self.ch_news = FakeChannel(102, "объявления")
        self.forum = FakeChannel(103, "анкеты-форум", kind="forum")
        self.text_channels = [self.ch_text, self.ch_news]
        self.forums = [self.forum]
        self.categories = [FakeCategory(201, "Анкеты")]
        self.role_review = FakeRole(301, "Проверяющие")
        self.roles = [self.role_review, FakeRole(302, "Игрок")]
        self._ch = {c.id: c for c in (self.ch_text, self.ch_news, self.forum)}
        self._roles = {r.id: r for r in self.roles}
        self._bot_member = FakeMember(999)
        self._bot_member.bot = True
        self.members = [FakeMember(11), FakeMember(22), self._bot_member]
    def get_channel(self, cid):
        return self._ch.get(cid)
    def get_role(self, rid):
        return self._roles.get(rid)
    def get_member(self, uid):
        return FakeMember(uid) if uid in (11, 22) else None

G = FakeGuild()
m.bot.get_guild = lambda gid: G if gid == GID else None
type(m.bot).guilds = property(lambda self: [G])  # харнес: бот «состоит» из фейковой гильдии

async def fake_fetch(token):
    return [{"id": str(GID), "permissions": 8}]
m.fetch_my_guilds = fake_fetch
m.is_web_admin = lambda uid, guild, entry: True

COOKIE = m.session_encode({"uid": UID, "token": "x", "name": "tester",
                           "exp": __import__("time").time() + 3600})
HEADERS = {"Cookie": f"{m.COOKIE_NAME}={COOKIE}"}

async def main():
    from aiohttp.test_utils import TestClient, TestServer
    app = m.build_dashboard_app()
    async with TestClient(TestServer(app)) as cli:
        # --- 1. прогрессия на сайте видит форумы ----------------------------- #
        r = await cli.get(f"/guild/{GID}/progress", headers=HEADERS)
        body = await r.text()
        check("GET /progress 200", r.status == 200)
        check("форум виден в выборе каналов прогрессии", str(G.forum.id) in body)

        r = await cli.post(f"/guild/{GID}/progress", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "xp": "10", "credits": "5", "cooldown": "60",
            "xp_base": "100", "cap": "1500"})
        pcfg = m.get_config(GID)
        check("прогрессия: volume/exclusive выключены, cap=1500 сохранены",
              pcfg["xp_volume"] is False and pcfg["level_roles_exclusive"] is False
              and pcfg["xp_char_cap"] == 1500)
        r = await cli.post(f"/guild/{GID}/progress", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "xp": "10", "credits": "5", "cooldown": "0",
            "xp_base": "100", "xp_step": "25", "mult_step": "0.2", "cap": "2000",
            "volume": "on", "exclusive": "on"})
        pcfg = m.get_config(GID)
        check("прогрессия: шаги кривых сохранены и таблица пересобрана",
              pcfg["xp_step"] == 25 and abs(pcfg["mult_step"] - 0.2) < 1e-9
              and abs(pcfg["multipliers"][1] - 1.2) < 1e-9
              and m.need_xp(pcfg, 3) == 150)
        r = await cli.post(f"/guild/{GID}/progress", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "xp": "10", "credits": "5", "cooldown": "0",
            "xp_base": "100", "xp_step": "10", "xp_mode": "mul", "xp_mul": "1.2",
            "mult_mode": "mul", "mult_mul": "1.05", "cap": "2000", "volume": "on", "exclusive": "on"})
        pcfg = m.get_config(GID)
        check("прогрессия: режимы-множители сохранены и кривые пересобраны",
              pcfg["xp_mode"] == "mul" and abs(pcfg["xp_mul"] - 1.2) < 1e-9
              and pcfg["mult_mode"] == "mul" and abs(pcfg["multipliers"][1] - 1.05) < 1e-9
              and m.need_xp(pcfg, 3) == 144)

        # --- 2. досье: настройки сохраняются ---------------------------------- #
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_cfg",
            "enabled": "on", "category": "201", "review_role": "301",
            "canon_role": "0", "canon_level": "7", "root_block": "",
            "core_blocks": "", "header": "ШАПКА {user}", "footer": "ПОДВАЛ"})
        check("POST dossier save_cfg -> redirect", r.status in (302, 303))
        cfg = m.doss_cfg(GID)
        check("досье: enabled сохранён", cfg["enabled"] is True)
        check("досье: категория и роль проверяющих сохранены",
              cfg["category"] == 201 and cfg["review_role"] == 301)
        check("досье: шапка сохранена", cfg["header"].startswith("ШАПКА"))
        on_disk = json.load(open(m.DOSSIER_PATH))
        check("досье: настройки на диске", str(GID) in on_disk and on_disk[str(GID)]["cfg"]["enabled"] is True)

        # --- 3. досье: блок создаётся и сохраняется --------------------------- #
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "block_new", "title": "Кто ты?"})
        loc = r.headers.get("Location", "")
        check("POST block_new -> redirect на редактор", "block=" in loc)
        bid = loc.split("block=")[-1].split("&")[0]
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "block_save", "block": bid,
            "title": "Кто ты?", "text": "Выбери путь",
            "ans_id": ["", ""], "label": ["Каноничный", "Самописный"],
            "fragment": ["", "Путь: самописный."], "next": ["", ""],
            "canon": ["c0"], "self": ["s1"]})
        blk = m.doss_block(GID, bid)
        check("блок сохранён с двумя ответами", blk and len(blk["answers"]) == 2)
        check("флаги канон/самописный сохранены",
              blk and blk["answers"][0].get("canon") and blk["answers"][1].get("self")
              and not blk["answers"][0].get("self"))
        r = await cli.get(f"/guild/{GID}/dossier", headers=HEADERS)
        body = await r.text()
        check("GET /dossier 200 и блок в списке", r.status == 200 and f"№{bid}" in body)

        # корень и ядро сохраняются
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_cfg",
            "category": "201", "review_role": "301", "canon_level": "7",
            "root_block": bid, "core_blocks": bid, "header": "ШАПКА {user}", "footer": "ПОДВАЛ"})
        cfg = m.doss_cfg(GID)
        check("досье: корень и ядро сохранены", str(cfg["root_block"]) == bid and bid in [str(b) for b in cfg["core_blocks"]])

        # --- 4. новости: черновик сохраняется и публикует --------------------- #
        r = await cli.post(f"/guild/{GID}/news", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_cfg",
            "channels": [str(G.ch_news.id)], "ping_role": "0"})
        ncfg = m.news_guild(GID)["cfg"]
        check("новости: канал сохранён", ncfg["channels"] == [G.ch_news.id])
        r = await cli.post(f"/guild/{GID}/news", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_draft", "draft": "0",
            "title": "Проба пера", "text": "Текст новости о кузне.", "image": ""})
        drafts = m.news_guild(GID)["drafts"]
        check("новости: черновик сохранён", len(drafts) == 1 and drafts[list(drafts)[0]]["title"] == "Проба пера")
        did = list(drafts)[0]
        r = await cli.post(f"/guild/{GID}/news", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "publish", "draft": did, "when": ""})
        g = m.news_guild(GID)
        check("новости: публикация ушла в канал", len(G.ch_news.sent) == 1 and not g["drafts"])
        check("новости: запись в ленте со статусом sent",
              g["published"] and g["published"][-1]["status"] == "sent" and g["published"][-1]["msgs"])

        # --- 5. новости: отложенная догоняется тикером ------------------------- #
        r = await cli.post(f"/guild/{GID}/news", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_draft", "draft": "0",
            "title": "Отложенная", "text": "Выйдет позже.", "image": ""})
        did2 = list(m.news_guild(GID)["drafts"])[0]
        r = await cli.post(f"/guild/{GID}/news", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "schedule", "draft": did2,
            "when": "2020-01-01 10:00"})
        g = m.news_guild(GID)
        check("новости: отложенная в статусе scheduled",
              g["published"] and g["published"][-1]["status"] == "scheduled")
        sent_before = len(G.ch_news.sent)
        await m.process_news()
        check("новости: тикер догнал просроченную", len(G.ch_news.sent) == sent_before + 1
              and g["published"][-1]["status"] == "sent")

        # --- 5b. публикация без каналов НЕ съедает черновик ---------------------- #
        m.news_guild(GID)["cfg"]["channels"] = []
        r = await cli.post(f"/guild/{GID}/news", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_draft", "draft": "0",
            "title": "Без каналов", "text": "Проверка защиты.", "image": ""})
        did3 = list(m.news_guild(GID)["drafts"])[0]
        r = await cli.post(f"/guild/{GID}/news", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "publish", "draft": did3, "when": ""})
        g3 = m.news_guild(GID)
        check("новости: публикация без каналов отклонена, черновик жив",
              did3 in g3["drafts"] and all(e["title"] != "Без каналов" for e in g3["published"]))
        m.news_guild(GID)["cfg"]["channels"] = [G.ch_news.id]

        # --- 5b2. формы без отмеченных чекбоксов не роняют обработчики --------- #
        r = await cli.post(f"/guild/{GID}/news", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_cfg", "ping_role": "0"})
        check("новости save_cfg без ключа channels не падает", r.status in (302, 303)
              and m.news_guild(GID)["cfg"]["channels"] == [])
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "block_save", "block": bid,
            "title": "Кто ты?", "text": "Выбери путь",
            "ans_id": ["", ""], "label": ["Канон", "Самописный"],
            "fragment": ["фраг1", "фраг2"], "next": ["", ""]})
        blk2 = m.doss_block(GID, bid)
        check("блок сохраняется без ключей canon/self", r.status in (302, 303)
              and blk2 and len(blk2["answers"]) == 2 and not blk2["answers"][0].get("canon"))
        dep_blk = next((b for b in m.doss_blocks(GID).values() if b.get("after")), None)
        check("ссылки after пережиты пересозданием id", dep_blk is None
              or dep_blk["after"].split(":")[1] in [a["id"] for a in blk2["answers"]])

        # --- 5b3. вспышка рендерится сервером на полной странице с ?msg= ------- #
        r = await cli.get(f"/guild/{GID}/news?msg=%D0%9A%D0%B0%D0%BD%D0%B0%D0%BB%D1%8B+%D1%81%D0%BE%D1%85%D1%80%D0%B0%D0%BD%D0%B5%D0%BD%D1%8B",
                          headers=HEADERS)
        body = await r.text()
        check("вспышка видна в серверном HTML (без JS)", "Каналы сохранены" in body)

        # --- 4b. редактор блоков: зависимости и бесшовные строки --------------- #
        r = await cli.get(f"/guild/{GID}/dossier?block={bid}&only=editor", headers=HEADERS,
                          allow_redirects=False)
        ed = await r.text()
        check("only=editor отдаёт карточку редактора без сбоя",
              r.status == 200 and "blk-editor" in ed and "name='after'" in ed and "blk-row-tpl" in ed)
        aid2 = m.doss_block(GID, bid)["answers"][1]["id"]
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "block_new", "title": "Псайкер?"})
        bid2 = r.headers.get("Location", "").split("block=")[-1].split("&")[0]
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "block_save", "block": bid2,
            "title": "Псайкер?", "text": "владеешь ли ты эфиром", "after": f"{bid}:{aid2}",
            "ans_id": [""], "label": ["Да"], "fragment": ["Псайкер: да."], "next": [""]})
        blk3 = m.doss_block(GID, bid2)
        if not (blk3 is not None and blk3.get("after") == f"{bid}:2"):
            print("DBG loc:", r.headers.get("Location"))
            print("DBG bid:", bid, "bid2:", bid2, "blk3:", blk3)
            print("DBG blocks:", {k: (v.get("title"), [a.get("id") for a in v.get("answers", [])], v.get("after")) for k, v in m.doss_blocks(GID).items()})
        check("зависимость after сохранена", blk3 is not None and blk3.get("after") == f"{bid}:{aid2}")
        r = await cli.get(f"/guild/{GID}/dossier", headers=HEADERS)
        body = await r.text()
        check("страница редактора несёт шаблон строки и JS свопа", "blk-row-tpl" in body and "only=editor" in body)
        r = await cli.get(f"/guild/{GID}/dossier?block={bid}", headers=HEADERS, allow_redirects=False)
        check("открытие редактора блока не падает (200)", r.status == 200)

        # --- 4c. кнопки не могут уйти в чужую форму (r35) ----------------------- #
        cfg_before = dict(m.doss_cfg(GID))
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False,
                           data={"csrf": m.csrf_token({"uid": UID}), "form": "cfg", "action": "save_cfg"})
        check("пустая форма настроек не затирает данные",
              "kind=err" in r.headers.get("Location", "") and m.doss_cfg(GID) == cfg_before)
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False,
                           data={"csrf": m.csrf_token({"uid": UID}), "form": "cfg", "action": "block_del",
                                 "block": bid})
        check("кнопка чужой формы игнорируется", "kind=err" in r.headers.get("Location", "")
              and m.doss_block(GID, bid) is not None)
        r = await cli.get(f"/guild/{GID}/dossier?block=%s" % bid, headers=HEADERS)
        body = await r.text()
        check("кнопки несут явные form= атрибуты", "form='blk-form'" in body and "form='cfg-form'" in body
              and "form='del-form'" in body)

        # --- 4c2. ядро: всеядный разбор, чипы, понятная ошибка (r37) ------------ #
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_cfg", "form": "cfg",
            "category": "201", "review_role": "301", "canon_level": "7",
            "root_block": bid, "core_blocks": f"{bid}; {bid2},№{bid2}\n{bid}",
            "header": "SHA", "footer": "F"})
        core = m.doss_cfg(GID)["core_blocks"]
        check("ядро разбирает любые разделители и схлопывает дубли",
              core == [bid, bid2] and r.status in (302, 303) and "kind=err" not in r.headers.get("Location", ""))
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_cfg", "form": "cfg",
            "category": "201", "root_block": bid, "core_blocks": "99", "header": "SHA", "footer": "F"})
        loc = r.headers.get("Location", "")
        check("ошибка ядра перечисляет существующие блоки", "kind=err" in loc and "99" in loc and bid in loc)
        r = await cli.get(f"/guild/{GID}/dossier", headers=HEADERS)
        body = await r.text()
        check("чипы-помощники ядра на странице", "core-add" in body and f"data-id='{bid}'" in body)

        # --- 4d. бесшовный POST без кнопки-отправителя = block_save (r36) ------- #
        before = dict(m.doss_block(GID, bid) or {})
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "form": "blk", "block": bid,
            "title": "Кто ты?", "text": "Выбери путь",
            "ans_id": ["3", "4"], "label": ["Каноничный", "Самописный"],
            "fragment": ["", "Путь: самописный."], "next": ["", ""]})
        blk4 = m.doss_block(GID, bid)
        check("POST с маркером blk без action сохранён как block_save",
              r.status in (302, 303) and "kind=err" not in r.headers.get("Location", "")
              and blk4 and len(blk4["answers"]) == 2)

        # --- 4e. r44: правила анкет — форма, сохранение, сброс, защита --------- #
        r = await cli.get(f"/guild/{GID}/dossier", headers=HEADERS)
        body = await r.text()
        check("карточка правил на странице (форма, чекбокс, textarea)",
              "rules-form" in body and "name='rules_send'" in body and "name='rules_text'" in body)
        check("заводской текст правил в textarea", "КРИТЕРИИ ОТКАЗА" in body and "☠" not in body)
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "form": "rules", "action": "save_rules",
            "rules_send": "on", "rules_text": "СВОИ ПРАВИЛА\nстрока два"})
        loc = r.headers.get("Location", "")
        from urllib.parse import unquote_plus
        loc_txt = unquote_plus(loc)
        dcfg = m.doss_cfg(GID)
        check("save_rules сохраняет текст и флаг отправки",
              r.status in (302, 303) and "kind=err" not in loc
              and dcfg["rules_text"] == "СВОИ ПРАВИЛА\nстрока два" and dcfg["rules_send"] is True)
        check("вспышка считает сообщения по лимитам", "1 сообщ" in loc_txt)
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "form": "rules", "action": "save_rules",
            "rules_text": ""})
        dcfg = m.doss_cfg(GID)
        check("save_rules без чекбокса выключает отправку, пустой текст допустим",
              dcfg["rules_send"] is False and dcfg["rules_text"] == "")
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "form": "rules", "action": "save_cfg"})
        check("кнопка чужой формы в rules игнорируется", "kind=err" in r.headers.get("Location", ""))
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "form": "rules", "action": "rules_default"})
        dcfg = m.doss_cfg(GID)
        check("rules_default возвращает заводской текст и включает отправку",
              r.status in (302, 303) and dcfg["rules_text"] == m.DEFAULT_DOSS_RULES
              and dcfg["rules_send"] is True)
        on_disk = json.load(open(m.DOSSIER_PATH))
        check("правила доехали до диска", on_disk[str(GID)]["cfg"]["rules_text"] == m.DEFAULT_DOSS_RULES)

        # --- 5c. предохранитель: сбой обработчика виден вспышкой, не 500 ------- #
        orig_save = m.save_dossier
        def boom():
            raise OSError("диск отверг запись")
        m.save_dossier = boom
        r = await cli.post(f"/guild/{GID}/dossier", headers=HEADERS, allow_redirects=False, data={
            "csrf": m.csrf_token({"uid": UID}), "action": "save_cfg", "category": "201"})
        loc = r.headers.get("Location", "")
        check("сбой сохранения -> редирект со вспышкой-причиной", r.status in (302, 303) and "OSError" in loc and "kind=err" in loc)
        m.save_dossier = orig_save

        # --- 5d. healthz и баннер сборки --------------------------------------- #
        r = await cli.get("/healthz")
        hj = await r.json()
        check("GET /healthz 200 и маркер сборки", r.status == 200 and hj.get("build", "").startswith("2026-10-23.r44"))
        r = await cli.get(f"/guild/{GID}/dossier", headers=HEADERS)
        body = await r.text()
        check("баннер сборки в шапке панели", "сборка 2026-10-23.r44" in body)

        # --- 6. рейтинг: вкладка открывается ----------------------------------- #
        r = await cli.get(f"/guild/{GID}/board", headers=HEADERS)
        check("GET /board 200", r.status == 200)

    # --- 7. опыт и рейтинг из веток и форумов (уровень ядра) ------------------- #
    thread = FakeThread(501, "ветка-ролёвки", G.ch_text.id)
    forum_post = FakeThread(502, "пост форума", G.forum.id)
    cfg = m.get_config(GID)
    cfg["xp_channels"] = [G.ch_text.id, G.forum.id]
    cfg["xp_cooldown"] = 0
    cfg["board_enabled"] = True
    cfg["board_min_messages"] = 1
    m.save_configs()
    author = FakeMember(11)
    msg = type("Msg", (), {"guild": G, "author": author, "channel": thread, "content": "слово " * 5})()
    await m.grant_activity(msg, cfg)
    entry = m.prog_entry(GID, 11)
    check("сообщение в ветке начислило опыт (родитель в xp_channels)", entry["xp"] > 0)
    msg2 = type("Msg", (), {"guild": G, "author": author, "channel": forum_post, "content": "слово " * 7})()
    await m.grant_activity(msg2, cfg)
    xp_before = m.prog_entry(GID, 22)["xp"]
    msg3 = type("Msg", (), {"guild": G, "author": FakeMember(22), "channel": FakeThread(503, "ветка", G.ch_text.id),
                            "webhook_id": None, "content": "три слова здесь"})()
    await m.on_message(msg3)
    check("on_message: сообщение в ветке прошло гейт каналов", m.prog_entry(GID, 22)["xp"] > xp_before)
    day, week = m.board_cycle_ids(cfg)
    rows = await m.board_top(G, cfg, "d", day)
    check("счётчики рейтинга пополнились из ветки и форума",
          any(uid == 11 and xp > 0 and msgs == 2 for uid, xp, msgs in rows))

    print(f"\nИТОГ: {OK} OK, {FAIL} FAIL")
    return 1 if FAIL else 0

if __name__ == "__main__":
    # уборка тестовых данных
    try:
        code = asyncio.run(main())
    finally:
        m.DOSSIER.pop(str(GID), None)
        m.NEWS.pop(str(GID), None)
        m.BOARD.pop(str(GID), None)
        m.PROGRESS.pop(str(GID), None)
        m.CONFIGS.pop(str(GID), None)
        m.save_dossier(); m.save_news(); m.save_board(); m.save_progress(); m.save_configs()
    sys.exit(code)
