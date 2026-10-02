from tests.conftest import ask, button_update, draft_turn, text_update


async def test_ask_then_draft_then_create(make_bot):
    bot, llm, tg, gh = make_bot([ask("Ce rol vede asta?"), draft_turn()])

    await bot.handle_update(text_update("vreau sesiuni active in settings"))
    assert "runda 1/2" in tg.sent[-1][1]

    await bot.handle_update(text_update("doar managerii"))
    preview_markup = tg.sent[-1][2]
    assert preview_markup is not None and "area: webapp" in tg.sent[-1][1]

    await bot.handle_update(button_update("create"))
    assert len(gh.created) == 1
    assert "issues/1" in tg.sent[-1][1]


async def test_preview_shows_default_milestone_and_create_keeps_it(make_bot):
    bot, llm, tg, gh = make_bot([draft_turn()])
    await bot.handle_update(text_update("idee"))
    assert "Milestone: v0.2.0" in tg.sent[-1][1]
    await bot.handle_update(button_update("create"))
    assert gh.created[0].milestone == "v0.2.0"


async def test_explicit_milestone_is_kept(make_bot):
    bot, llm, tg, gh = make_bot([draft_turn(milestone="v0.3.0")])
    await bot.handle_update(text_update("idee, pune-l in v0.3.0"))
    assert "Milestone: v0.3.0" in tg.sent[-1][1]


async def test_duplicate_update_id_is_processed_once(make_bot):
    bot, llm, tg, gh = make_bot([ask("q1")])
    update = {"update_id": 5, **text_update("idee")}
    await bot.handle_update(update)
    await bot.handle_update(update)
    assert len(tg.sent) == 1 and llm.force_flags == [False]


async def test_non_allowlisted_user_is_ignored(make_bot):
    bot, llm, tg, gh = make_bot([draft_turn()])
    await bot.handle_update(text_update("hello", user_id=999))
    assert tg.sent == [] and llm.force_flags == []


async def test_draft_is_forced_after_max_rounds(make_bot):
    bot, llm, tg, gh = make_bot([ask("q1"), ask("q2"), draft_turn()])
    for msg in ("a", "b", "c"):
        await bot.handle_update(text_update(msg))
    assert llm.force_flags == [False, False, True]


async def test_draft_command_forces_draft(make_bot):
    bot, llm, tg, gh = make_bot([ask("q1"), draft_turn()])
    await bot.handle_update(text_update("idee vaga"))
    await bot.handle_update(text_update("/draft"))
    assert llm.force_flags == [False, True]
    assert tg.sent[-1][2] is not None


async def test_cancel_clears_state(make_bot):
    bot, llm, tg, gh = make_bot([draft_turn()])
    await bot.handle_update(text_update("idee"))
    await bot.handle_update(button_update("cancel"))
    await bot.handle_update(button_update("create"))
    assert gh.created == [] and tg.sent[-1][1] == "Nu am un draft activ."
