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

def doss_guild(guild_id: int) -> dict:
    g = DOSSIER.setdefault(str(guild_id), {})
    g.setdefault("cfg", {})
    g.setdefault("blocks", {})
    g.setdefault("arch", [])
    cfg = g["cfg"]
    for key, val in (("enabled", False), ("category", None), ("review_role", None),
                     ("canon_role", None), ("canon_level", 0), ("root_block", None),
                     ("core_blocks", []), ("header", DEFAULT_DOSS_HEADER),
                     ("footer", DEFAULT_DOSS_FOOTER)):
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
    return ""

def doss_find_cycles(guild_id: int) -> list:
    """Блоки, участвующие в циклах переходов: обход с запоминанием стека."""
    blocks = doss_blocks(guild_id)
    bad = set()

    def walk(bid, stack):
        if bid in stack:
            bad.update(stack[stack.index(bid):])
            return
        blk = blocks.get(str(bid))
        if not blk:
            return
        for a in blk.get("answers", []):
            nxt = a.get("next")
            if nxt not in (None, "", "0", 0):
                walk(str(nxt), stack + [str(bid)])

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
    """Куда вести после ответа: явный next, иначе следующий блок ядра, иначе финиш."""
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
    return head + "\n\n".join(parts) + foot

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
                await doss_ping_review(interaction.channel, cfg,
                                       f"{interaction.user.mention} подаёт **каноничного героя**. Проверьте допуск и анкету.")
                await doss_archive(gid, interaction.user, interaction.channel, sess, "канон")
                return
            # Обычный ответ: пишем путь и идём дальше по дереву.
            sess["path"].append([str(self.block.get("id")), str(answer.get("id"))])
            nxt = doss_next_block(gid, self.block.get("id"), answer)
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
        existing = [ch for ch, s in DOSS_SESS.items()
                    if s.get("gid") == gid and s.get("uid") == interaction.user.id
                    and s.get("status") == "flow"]
        if existing:
            ch = interaction.guild.get_channel(int(existing[0]))
            where = ch.mention if ch else "ваш канал анкеты"
            await interaction.response.send_message(
                f"У вас уже есть активная анкета: {where}. Допишите её или позовите `/анкета закрыть`.",
                ephemeral=True)
            return
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
                                   "cur": str(cfg["root_block"]), "status": "flow",
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
                        "В конце бот соберёт шаблон анкеты из ваших ответов.",
            color=GOLD_COLOR).set_footer(text=FOOTER_TEXT))
        await chan.send(embed=doss_block_embed(gid, root), view=DossierFlowView(gid, interaction.user.id, root))

