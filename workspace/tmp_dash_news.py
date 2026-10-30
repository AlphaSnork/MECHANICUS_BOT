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
        action = post.get("action", "save_cfg")
        if action == "save_cfg":
            cfg["channels"] = [int(x) for x in post.getall("channels") if str(x).isdigit()]
            cfg["ping_role"] = _int(post.get("ping_role")) or None
            save_news()
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
        + f"<form method='post' style='display:inline'>{csrf_input(session)}"
        f"<input type='hidden' name='news' value='{e['id']}'>"
        + (f"<button class='ghost' name='action' value='cancel'>Отменить</button>"
           if e.get("status") == "scheduled" else "")
        + f"<button class='ghost' name='action' value='edit_pub' form='pubform{e['id']}'>Правка формой ниже</button>"
        + "</form></li>"
        for e in g["published"][-8:][::-1]) or "<li>новостей ещё не было</li>"
    body = (
        flash_block(request)
        + "<div class='card'><h2>Каналы и пинг</h2><div class='sub'>команды: /новости черновик • "
          "опубликовать • каналы • роль • правка • история</div>"
        f"<form method='post'>{csrf_input(session)}<label>Каналы публикации</label>{ch_boxes}"
        f"<label>Роль-пинг</label><select name='ping_role'><option value='0'>— без пинга —</option>{role_opts}</select>"
        "<p><button name='action' value='save_cfg'>Сохранить</button></p></form></div>"
        + f"<div class='card'><h2>Черновик {('№' + str(d['id'])) if d['id'] else '(новый)'}</h2>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='draft' value='{d['id']}'>"
        f"<label>Заголовок</label><input type='text' name='title' maxlength='256' value=\"{d['title']}\">"
        f"<label>Текст</label><textarea name='text'>{d['text']}</textarea>"
        f"<label>Изображение (ссылка https)</label><input type='text' name='image' value=\"{d['image']}\">"
        "<p><button name='action' value='save_draft'>Сохранить черновик</button> "
        "<button name='action' value='publish'>Опубликовать сейчас</button></p>"
        "<label>Или отложить: «45» (минуты) / «2026-10-24 20:00»</label>"
        "<input type='text' name='when' placeholder='45'>"
        "<p><button class='ghost' name='action' value='schedule'>Запланировать</button></p></form>"
        f"<form method='post'>{csrf_input(session)}<input type='hidden' name='draft' value='{d['id']}'>"
        "<p><button class='ghost' name='action' value='del_draft'>Удалить черновик</button></p></form></div>"
        + f"<div class='card'><h2>Черновики</h2><ul>{drafts}</ul></div>"
        + f"<div class='card'><h2>Лента</h2><ul>{pub}</ul>"
    )
    for e in g["published"][-8:][::-1]:
        body += (f"<form method='post' id='pubform{e['id']}'>{csrf_input(session)}"
                 f"<input type='hidden' name='news' value='{e['id']}'>"
                 f"<label>Правка №{e['id']}: заголовок</label><input type='text' name='title' maxlength='256' value=\"{e['title']}\">"
                 f"<label>Текст</label><textarea name='text'>{e['text']}</textarea>"
                 f"<label>Изображение</label><input type='text' name='image' value=\"{e.get('image', '')}\">"
                 "<p><button name='action' value='edit_pub'>Обновить сообщения</button></p></form>")
    body += "</div>"
    return web.Response(text=dash_page("Новости", body, session, guild_tabs(gid, "news"), guild=guild), content_type="text/html")

