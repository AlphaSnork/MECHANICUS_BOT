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
        chan = interaction.guild.get_channel(mine[-1].get("chan", 0))
        tmpl = None
    if not tmpl:
        # шаблон храним в сессии; из архива пересобираем по пути
        if mine:
            tmpl = doss_assemble(interaction.guild.id, interaction.user.display_name, mine[-1]["path"])
    if not tmpl:
        await interaction.response.send_message("У вас ещё нет собранного шаблона анкеты.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=discord.Embed(title="📜 Ваш последний шаблон", description=f"```\n{tmpl[:3900]}\n```",
                            color=GOLD_COLOR).set_footer(text=FOOTER_TEXT))

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

