#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регресс-харнес модуля «Досье» (анкеты персонажей, партия №44, r26).

Запуск из корня проекта: python3 check_dossier.py
Тестовые данные живут на изолированных gid и вычищаются в конце.
"""
import asyncio
import importlib.util
import sys

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

class R:
    def __init__(self, rid): self.id = rid

class M:
    def __init__(self, uid, gid, roles=(), level=1):
        self.id = uid
        self.guild = type("G", (), {"id": gid})()
        self.roles = [R(r) for r in roles]
        self._level = level
    # prog_entry читает PROGRESS напрямую, уровень подставляем через данные ниже

GID = 910000000000000001
bot.DOSSIER[str(GID)] = {
    "cfg": {"enabled": True, "category": None, "review_role": 555, "canon_role": 777,
            "canon_level": 10, "root_block": "1", "core_blocks": ["2", "3"],
            "header": "АНКЕТА — {user}\n", "footer": "\nКОНЕЦ"},
    "blocks": {
        "1": {"id": 1, "title": "Кто ты?", "text": "Выбери путь",
              "answers": [{"id": "a", "label": "Канон", "fragment": "", "next": "", "canon": True, "self": False},
                          {"id": "b", "label": "Самописный", "fragment": "Путь: самописный.", "next": "", "canon": False, "self": True}]},
        "2": {"id": 2, "title": "Сторона", "text": "Кому служишь?",
              "answers": [{"id": "i", "label": "Империум", "fragment": "Сторона: Империум.", "next": "3"},
                          {"id": "x", "label": "Ксеносы", "fragment": "Сторона: ксеносы.", "next": "4"}]},
        "3": {"id": 3, "title": "Псайкер?", "text": "да/нет",
              "answers": [{"id": "y", "label": "Да", "fragment": "Псайкер: да."},
                          {"id": "n", "label": "Нет", "fragment": "Псайкер: нет."}]},
        "4": {"id": 4, "title": "Фракция ксеносов", "text": "кто?",
              "answers": [{"id": "d", "label": "Друkhари", "fragment": "Фракция: друkhари."}]},
    },
    "arch": [],
}
cfg = bot.doss_cfg(GID)

# --- 1. валидация блоков ----------------------------------------------------- #
bad_title = {"id": 9, "title": "", "text": "q", "answers": [{"id": "a", "label": "x", "fragment": ""}]}
check("блок без заголовка отвергнут", "заголовка" in bot.doss_validate_block(GID, bad_title))
bad_ans = {"id": 9, "title": "t", "text": "q", "answers": []}
check("блок без ответов отвергнут", "ответ" in bot.doss_validate_block(GID, bad_ans))
many = {"id": 9, "title": "t", "text": "q", "answers": [{"id": str(i), "label": f"l{i}", "fragment": ""} for i in range(26)]}
check("26 ответов отвергнуто", "25" in bot.doss_validate_block(GID, many))
dup = {"id": 9, "title": "t", "text": "q", "answers": [{"id": "1", "label": "Один", "fragment": ""}, {"id": "2", "label": "один", "fragment": ""}]}
check("дубликат подписи отвергнут", "дубликат" in bot.doss_validate_block(GID, dup))
badnext = {"id": 9, "title": "t", "text": "q", "answers": [{"id": "1", "label": "x", "fragment": "", "next": "99"}]}
check("next в несуществующий блок отвергнут", "несуществующий" in bot.doss_validate_block(GID, badnext))
check("хороший блок принят", bot.doss_validate_block(GID, bot.doss_block(GID, "2")) == "")

# --- 2. циклы переходов ------------------------------------------------------- #
bot.DOSSIER[str(GID)]["blocks"]["5"] = {"id": 5, "title": "петля", "text": "q",
    "answers": [{"id": "p", "label": "назад", "fragment": "", "next": "6"}]}
bot.DOSSIER[str(GID)]["blocks"]["6"] = {"id": 6, "title": "петля2", "text": "q",
    "answers": [{"id": "q", "label": "вперёд", "fragment": "", "next": "5"}]}
cyc = bot.doss_find_cycles(GID)
check("цикл 5<->6 найден", "5" in cyc and "6" in cyc)
for b in ("5", "6"):
    bot.DOSSIER[str(GID)]["blocks"].pop(b)
check("после уборки циклов нет", bot.doss_find_cycles(GID) == [])

# --- 3. допуск канона: роль ИЛИ ранг ------------------------------------------ #
m_role = M(1, GID, roles=[777])
m_lvl = M(2, GID, roles=[], level=1)
bot.prog_entry(GID, 2)["level"] = 12
m_low = M(3, GID, roles=[])
bot.prog_entry(GID, 3)["level"] = 4
ok1, _ = bot.doss_canon_ok(m_role, cfg)
ok2, _ = bot.doss_canon_ok(m_lvl, cfg)
ok3, reason3 = bot.doss_canon_ok(m_low, cfg)
check("канон по роли допущен", ok1)
check("канон по рангу допущен", ok2)
check("канон без роли и ранга отвергнут", not ok3 and "ранг" in reason3)
cfg_free = dict(cfg, canon_role=None, canon_level=0)
check("без настроенного допуска канон открыт всем", bot.doss_canon_ok(m_low, cfg_free)[0])

# --- 4. переходы дерева и ядро ------------------------------------------------ #
a_imp = bot.doss_block(GID, "2")["answers"][0]
a_xen = bot.doss_block(GID, "2")["answers"][1]
check("явный next ведёт в свой блок", bot.doss_next_block(GID, "2", a_imp) == "3")
check("ветвь ксеносов ведёт в свой блок", bot.doss_next_block(GID, "2", a_xen) == "4")
a_psy = bot.doss_block(GID, "3")["answers"][0]
check("блок ядра без next тянет следующий блок ядра", bot.doss_next_block(GID, "2", dict(a_imp, next="")) == "3")
check("последний блок ядра завершает путь", bot.doss_next_block(GID, "3", a_psy) is None)
a_kse4 = bot.doss_block(GID, "4")["answers"][0]
check("не-ядерный блок без next завершает путь", bot.doss_next_block(GID, "4", a_kse4) is None)

# --- 5. сборка шаблона -------------------------------------------------------- #
path = [["1", "b"], ["2", "i"], ["3", "y"]]
tpl = bot.doss_assemble(GID, "Тестус", path)
check("шапка с подстановкой имени", tpl.startswith("АНКЕТА — Тестус"))
check("фрагменты в порядке пути", tpl.find("самописный") < tpl.find("Империум") < tpl.find("Псайкер: да"))
check("подвал приклеен", tpl.endswith("КОНЕЦ"))
tpl_canon = bot.doss_assemble(GID, "Канон", [["1", "a"]])
check("каноничный путь: фрагментов ядра нет", "Сторона" not in tpl_canon)

# --- 5b. зависимости блоков: «после ответа Q:A показать блок B» ---------------- #
bot.DOSSIER[str(GID)]["blocks"]["7"] = {"id": 7, "title": "Санкция псайкера", "text": "какая?",
    "after": "2:i",
    "answers": [{"id": "t", "label": "Телепат", "fragment": "Санкция: телепат."},
                {"id": "n", "label": "Нет санкции", "fragment": "Санкция: отсутствует."}]}
sess = {"path": [], "stack": []}
a_imp = bot.doss_block(GID, "2")["answers"][0]
nxt = bot.doss_flow_next(GID, sess, bot.doss_block(GID, "2"), a_imp)
check("триггерный блок подставляется после своего ответа", nxt == "7")
check("базовая линия запомнена в стеке", sess["stack"] == ["3"])
sess["path"].append(["2", "i"])
a_t = bot.doss_block(GID, "7")["answers"][0]
sess["path"].append(["7", "t"])
nxt2 = bot.doss_flow_next(GID, sess, bot.doss_block(GID, "7"), a_t)
check("после финиша триггерной цепочки возврат на базовую линию", nxt2 == "3")
sess2 = {"path": [["7", "t"]], "stack": []}
nxt3 = bot.doss_flow_next(GID, sess2, bot.doss_block(GID, "2"), a_imp)
check("пройденный триггерный блок повторно не показывается", nxt3 == "3")
a_xen = bot.doss_block(GID, "2")["answers"][1]
check("ответ без триггера идёт по базовой линии", bot.doss_flow_next(GID, {"path": [], "stack": []}, bot.doss_block(GID, "2"), a_xen) == "4")
bad_after = {"id": 8, "title": "t", "text": "q", "after": "99:zz",
             "answers": [{"id": "a", "label": "x", "fragment": ""}]}
check("зависимость на несуществующий ответ отвергнута", "несуществующий" in bot.doss_validate_block(GID, bad_after))
bot.DOSSIER[str(GID)]["blocks"]["7"]["answers"][0]["next"] = "2"
cyc2 = bot.doss_find_cycles(GID)
check("цикл через триггерную зависимость найден", "2" in cyc2 and "7" in cyc2)
bot.DOSSIER[str(GID)]["blocks"]["7"]["answers"][0]["next"] = ""
bot.DOSSIER[str(GID)]["blocks"].pop("7")

# --- 6. сессии ---------------------------------------------------------------- #
bot.doss_set_session(4242, {"gid": GID, "uid": 1, "path": [], "cur": "1", "status": "flow"})
check("сессия пишется и читается", bot.doss_session(4242)["cur"] == "1")
bot.doss_drop_session(4242)
check("сессия удаляется", bot.doss_session(4242) is None)

# --- 7. views и дерево команд -------------------------------------------------- #
view = bot.DossierFlowView(GID, 1, bot.doss_block(GID, "1"))
check("кнопки по числу ответов", len(view.children) == 2)
panel = bot.DossierPanelView()
check("персистентная кнопка панели с custom_id", panel.children[0].custom_id == "doss:new" and panel.timeout is None)
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
check("у /анкета 9 подкоманд", len(bot.g_doss.commands) == 9)
app = bot.build_dashboard_app()
check("маршрут досье есть", "/guild/{gid}/dossier" in {r.resource.canonical for r in app.router.routes()})
check("вкладка досье в TAB_META и навигации", "dossier" in bot.TAB_META and 'data-tab=\'dossier\'' in bot.guild_tabs(GID, ""))

# --- 8. r38: мёртвые сессии и лимит анкет ------------------------------------- #
class _G38:
    def __init__(self, gid, channels):
        self.id = gid
        self.name = f"guild{gid}"
        self._ch = {c: object() for c in channels}
    def get_channel(self, cid):
        return self._ch.get(cid)
guild_ch = _G38(GID, [9001])
bot.doss_set_session(9001, {"gid": GID, "uid": 11, "path": [], "cur": "1", "status": "flow"})
bot.doss_set_session(9002, {"gid": GID, "uid": 11, "path": [], "cur": "1", "status": "flow"})
gone = bot.doss_purge_dead(guild_ch)
check("мёртвая сессия удалённого канала вычищена", gone == 1 and bot.doss_session(9002) is None)
check("живая сессия не тронута", bot.doss_session(9001) is not None)
payload = type("P", (), {"channel_id": 9001, "guild_id": GID})()
asyncio.run(bot.on_raw_guild_channel_delete(payload))
check("удаление канала в Discord закрывает сессию сразу", bot.doss_session(9001) is None)
src_bot = open("bot.py", encoding="utf-8").read()
check("лимит одной активной анкеты убран", "уже есть активная анкета" not in src_bot)

# --- 9. r44: правила анкет (отправка после формирования, редактура) ------------ #
import io as _io
import re as _re
check("в cfg по умолчанию rules_send=True и заводской текст",
      bot.doss_cfg(GID).get("rules_send") is True
      and bot.doss_cfg(GID).get("rules_text") == bot.DEFAULT_DOSS_RULES)
_bad = [s for s in ("\u2620", "\u2020", "\u2720", "\u2715", "\u2669", "\u2563", "\u2560")
        if s in bot.DEFAULT_DOSS_RULES]
check(f"в заводских правилах нет декоративных символов (найдено: {_bad})", not _bad)
_md = _io.open("ПРАВИЛА_АНКЕТ.md", encoding="utf-8").read()
_body = _re.search(r"<!-- НАЧАЛО ТЕКСТА ПРАВИЛ -->\n(.*?)\n<!-- КОНЕЦ ТЕКСТА ПРАВИЛ -->", _md, _re.S).group(1)
check("ПРАВИЛА_АНКЕТ.md синхронен с DEFAULT_DOSS_RULES", _body.rstrip("\n") + "\n" == bot.DEFAULT_DOSS_RULES)
check("разделители ═ в правилах остались", "═" * 10 in bot.DEFAULT_DOSS_RULES)

check("нарезка пустого текста = []", bot.doss_rules_chunks("") == [] and bot.doss_rules_chunks("   \n ") == [])
_long = "\n".join(f"строка {i} " + "x" * 60 for i in range(200))
_ch = bot.doss_rules_chunks(_long)
check("нарезка длинного текста: >1 части, все <=1800", len(_ch) > 1 and all(len(c) <= 1800 for c in _ch))
check("нарезка не теряет строки", "\n".join(_ch) == _long.strip())
_one_long_line = "ы" * 5000
_ch2 = bot.doss_rules_chunks(_one_long_line)
check("одна сверхдлинная строка тоже режется", all(len(c) <= 1800 for c in _ch2) and "".join(_ch2) == _one_long_line)

class _ChanR:
    def __init__(self):
        self.sent = []
        self.name = "anketa-test"
        self.guild = type("G", (), {"id": GID})()
    async def send(self, content=""):
        self.sent.append(content)
        return object()

class _UserR:
    mention = "<@11>"

_cfg_on = dict(bot.doss_cfg(GID), rules_send=True)
_chan = _ChanR()
_n = asyncio.run(bot.doss_send_rules(_chan, _cfg_on, _UserR()))
check("правила отправлены всеми частями", _n == len(_chan.sent) == len(bot.doss_rules_chunks(bot.DEFAULT_DOSS_RULES)))
check("каждая часть <=2000 и первая содержит mention",
      all(len(s) <= 2000 for s in _chan.sent) and _chan.sent[0].startswith("<@11>"))
check("весь текст правил доехал", bot.DEFAULT_DOSS_RULES.split("\n")[-2] in "".join(_chan.sent))
_chan2 = _ChanR()
_n2 = asyncio.run(bot.doss_send_rules(_chan2, dict(_cfg_on, rules_send=False), _UserR()))
check("rules_send=False — ничего не отправлено", _n2 == 0 and not _chan2.sent)
_chan3 = _ChanR()
_n3 = asyncio.run(bot.doss_send_rules(_chan3, dict(_cfg_on, rules_text=""), _UserR()))
check("пустой текст правил — ничего не отправлено", _n3 == 0 and not _chan3.sent)
_src44 = open("bot.py", encoding="utf-8").read()
check("правила уходят на обоих путях финиша (шаблон и канон)",
      _src44.count("doss_send_rules(interaction.channel, cfg, interaction.user)") == 2)

# --- уборка -------------------------------------------------------------------- #
bot.DOSSIER.pop(str(GID), None)
bot.PROGRESS.pop(str(GID), None)
bot.save_dossier()
bot.save_progress()
print(f"\nИТОГ: {OK} OK, {FAIL} FAIL")
sys.exit(1 if FAIL else 0)
