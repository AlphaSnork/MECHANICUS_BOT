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


