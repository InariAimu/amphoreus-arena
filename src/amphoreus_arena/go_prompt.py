"""Shared, concise Go instructions; tool schemas carry operation details."""


def go_instructions(game, *, incremental=False):
    text = (
        f"Play {game.board_size}x{game.board_size} Go as {game.current_player}. "
        f"Coordinates skip I; ranks 1-{game.board_size} increase upward. "
        "Board rows run top to bottom: B/W/. = black/white/empty. "
        "No suicide or repeated board (positional superko); pass is exempt. "
        f"Area scoring, white komi {game.komi:g}; prisoners add no points. "
    )
    if getattr(game, "native_tools", False):
        text += (
            "Two passes enter dead-stone agreement. Use arena_go tools to propose/accept "
            "dead stones or resume a dispute; after a successful decision return move=tool. "
        )
    else:
        text += "Two passes score the board as it stands; capture dead stones before passing. "
    text += (
        "You may use tools and helper scripts within your match workspace. "
        "Do not install, deploy, implement or use Go-playing software/engines, "
        "or ask Go AIs, services or other agents for moves or analysis. "
    )
    if incremental:
        text += (
            "Keep this position in session memory. Later inputs: apply your_last_turn, "
            "then opponent_updates; captured lists are removals. "
            "In play choose an empty point outside illegal_empty, or pass/resign. "
            "Use go_get_state if available to recover state. "
            "Aim for 120s per turn (soft target); elapsed_seconds reports your last time. "
        )
    else:
        text += "Choose from legal_moves, including pass/resign. "
    return text + 'Final reply: exactly one JSON object, no surrounding prose or Markdown fences: {"move":"...","explanation":"1-2句中文公开说明"}.'
