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
        action = post.get("action", "save_cfg")
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
            core = [b.strip() for b in (post.get("core_blocks") or "").replace(";", ",").split(",") if b.strip()]
            for b in core:
                if b not in blocks:
                    raise web.HTTPFound(f"/guild/{gid}/dossier?msg=В+ядре+нет+блока+{b}&kind=err")
            cfg["core_blocks"] = core
            cfg["header"] = (post.get("header") or DEFAULT_DOSS_HEADER)[:1500]
            cfg["footer"] = (post.get("footer") or DEFAULT_DOSS_FOOTER)[:1500]
            save_dossier()
            raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Настройки+досье+сохранены")
        if action == "block_new":
            bid = doss_new_block_id(gid)
            blocks[str(bid)] = {"id": bid, "title": (post.get("title") or "Новый блок")[:80],
                                "text": "Вопрос блока…", "answers": []}
            save_dossier()
            raise web.HTTPFound(f"/guild/{gid}/dossier?block={bid}")
        if action == "block_save":
            bid = str(_int(post.get("block")))
            blk = blocks.get(bid)
            if not blk:
                raise web.HTTPFound(f"/guild/{gid}/dossier?msg=Блок+не+найден&kind=err")
            labels = post.getall("label") or []
            frags = post.getall("fragment") or []
            nexts = post.getall("next") or []
            ids = post.getall("ans_id") or []
            canon = post.getall("canon") or []
            selff = post.getall("self") or []
            answers = []
            for i, lab in enumerate(labels):
                lab = (lab or "").strip()
                if not lab:
                    continue
                aid = ids[i] if i < len(ids) and ids[i] not in ("", "0") else str(1000 + i)
                nxt = nexts[i] if i < len(nexts) else ""
                answers.append({
                    "id": aid, "label": lab[:80],
                    "fragment": (frags[i] if i < len(frags) else "")[:1900],
                    "next": nxt if nxt and nxt != bid else "",
                    "canon": f"c{i}" in canon, "self": f"s{i}" in selff,
                })
            cand = {"id": blk.get("id"), "title": (post.get("title") or "")[:80],
                    "text": (post.get("text") or "")[:1900], "answers": answers}
            err = doss_validate_block(gid, cand, ignore_id=bid)
            if err:
                raise web.HTTPFound(f"/guild/{gid}/dossier?block={bid}&msg=" + err.replace(" ", "+") + "&kind=err")
            blocks[bid] = cand
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
                f"<option value='{b}'{' selected' if str(nxt) == b else ''}>№{b}: {blocks[b].get('title', '')[:30]}</option>"
                for b in sorted(blocks, key=lambda x: int(x) if x.isdigit() else 0) if b != edit_id)
            rows.append(
                f"<div class='row'><input type='hidden' name='ans_id' value='{(a or {}).get('id', '')}'>"
                f"<input type='text' name='label' maxlength='80' placeholder='Подпись кнопки' value=\"{(a or {}).get('label', '')}\">"
                f"<textarea name='fragment' placeholder='Фрагмент шаблона при этом ответе'>{(a or {}).get('fragment', '')}</textarea>"
                f"<select name='next'><option value=''>— финиш / по ядру —</option>{next_opts}</select>"
                f"<label><input type='checkbox' name='canon' value='c{i}'{' checked' if (a or {}).get('canon') else ''}> канон</label>"
                f"<label><input type='checkbox' name='self' value='s{i}'{' checked' if (a or {}).get('self') else ''}> самописный</label></div>")
        editor = (
            f"<div class='card'><h2>Блок №{edit_id}</h2>"
            f"<form method='post'>{csrf_input(session)}<input type='hidden' name='block' value='{edit_id}'>"
            f"<label>Заголовок</label><input type='text' name='title' maxlength='80' value=\"{blk.get('title', '')}\">"
            f"<label>Текст вопроса</label><textarea name='text'>{blk.get('text', '')}</textarea>"
            "<label>Ответы: подпись кнопки, фрагмент шаблона, следующий блок, флаги корневого выбора</label>"
            + "".join(rows) +
            "<p><button name='action' value='block_save'>Сохранить блок</button></p></form>"
            f"<form method='post'>{csrf_input(session)}<input type='hidden' name='block' value='{edit_id}'>"
            "<p><button class='ghost' name='action' value='block_del'>Удалить блок</button></p></form></div>")
    body = (
        flash_block(request)
        + "<div class='card'><h2>Модуль анкет</h2><div class='sub'>команды: /анкета панель • включить • "
          "настройки • список • продолжить • закрыть • шаблон</div>"
        f"<form method='post'>{csrf_input(session)}"
        f"<label><input type='checkbox' name='enabled'{' checked' if cfg.get('enabled') else ''}> Включить анкеты</label>"
        f"<label>Категория приватных каналов</label><select name='category'><option value='0'>— не задана —</option>{cat_opts}</select>"
        f"<label>Роль проверяющих (пинг о выдаче шаблона и о каноне)</label><select name='review_role'><option value='0'>— не задана —</option>{role_opts}</select>"
        f"<label>Роль допуска каноничных героев (или ранг ниже)</label><select name='canon_role'><option value='0'>— не задана —</option>{canon_role_opts}</select>"
        f"<label>Минимальный ранг для канона (0 — только роль)</label><input type='number' name='canon_level' min='0' max='100' value='{int(cfg.get('canon_level') or 0)}'>"
        f"<label>Корневой блок (каноничный / самописный)</label><select name='root_block'><option value=''>— не задан —</option>{block_opts}</select>"
        f"<label>Ядро: id блоков через запятую, обязательны для самописных</label><input type='text' name='core_blocks' value='{','.join(str(b) for b in cfg.get('core_blocks', []))}'>"
        f"<label>Шапка шаблона (плейсхолдер {{user}})</label><textarea name='header'>{cfg.get('header', '')}</textarea>"
        f"<label>Подвал шаблона</label><textarea name='footer'>{cfg.get('footer', '')}</textarea>"
        "<p><button name='action' value='save_cfg'>Сохранить настройки</button></p></form></div>"
        + f"<div class='card'><h2>Блоки схемы</h2><ul>{blist}</ul>"
        f"<form method='post'>{csrf_input(session)}<label>Заголовок нового блока</label>"
        "<input type='text' name='title' maxlength='80' placeholder='Сторона конфликта'>"
        "<p><button name='action' value='block_new'>Создать блок</button></p></form></div>"
        + editor
        + f"<div class='card'><h2>Архив анкет</h2><ul>{arch}</ul></div>"
    )
    return web.Response(text=dash_page("Досье", body, session, guild_tabs(gid, "dossier"), guild=guild), content_type="text/html")

