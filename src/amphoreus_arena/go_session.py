"""Small continuation inputs for one persistent Go session per seat."""
from copy import deepcopy
import json

from .go import COLUMNS
from .go_prompt import go_instructions


SOFT_TURN_SECONDS = 120


def session_schema(game):
    schema = deepcopy(game.action_schema())
    if getattr(game, "phase", "play") == "play":
        # Enumerating hundreds of legal coordinates here duplicates the input on
        # every Codex exec/resume. The game still validates the returned move.
        move = schema["properties"]["move"]
        move.pop("enum", None)
        ranks = "[1-9]" if game.board_size == 9 else f"(?:[1-9]|1[0-{game.board_size - 10}])"
        move["pattern"] = f"^(?:[{COLUMNS[:game.board_size]}]{ranks}|pass|resign)$"
    return schema


def session_input(game, turns, snapshots, *, version=2):
    """Return the actual prompt, schema and dependency metadata without mutation.

    Delta base is the seat's previous *input* position. Its own committed action
    is acknowledged (including captures), followed by all intervening opponent
    actions. Agreement operations can give the same seat consecutive turns.
    """
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Unsupported Go input version")
    side = game.current_player
    previous = next((t for t in reversed(turns) if t["player"] == side), None)
    schema = session_schema(game)
    if previous is None:
        instruction = go_instructions(game, incremental=True)
        payload, kind, base = game.observation(), "initial", len(turns)
        if version == 2:
            state = game.snapshot()
            payload = {k: state[k] for k in ("board", "ply")}
            if getattr(game, "phase", "play") == "play":
                payload["illegal_empty"] = illegal_empty(game)
            if "phase" in state:
                payload["phase"] = state["phase"]
                if state["phase"] == "agreement":
                    payload["negotiation"] = state["negotiation"]
    else:
        def change(turn):
            value = {"ply": turn["ply"], "action": deepcopy(turn["action"])}
            captured = snapshots[turn["ply"]]["state"]["last_captures"]
            if captured:
                value["captured"] = captured.copy()
            return value

        own = change(previous)
        own["elapsed_seconds"] = previous["elapsed_seconds"]
        if previous["elapsed_seconds"] > SOFT_TURN_SECONDS:
            own["warning"] = (
                "Previous thinking exceeded 120 seconds. Decide more promptly; "
                "avoid repeating analysis and return your best legal action."
            )
        state = game.snapshot()
        payload = {"base_ply": previous["ply"] - 1, "source_ply": len(turns),
                   "your_last_turn": own,
                   "opponent_updates": [change(t) for t in turns[previous["ply"]:]]}
        if getattr(game, "phase", "play") == "play":
            payload["illegal_empty"] = illegal_empty(game)
        if "phase" in state:
            payload["phase"] = state["phase"]
            before = snapshots[previous["ply"] - 1]["state"]
            if state["phase"] == "agreement" or state["negotiation"] != before["negotiation"]:
                payload["negotiation"] = state["negotiation"]
        instruction = "Apply this Go session update and return your next JSON action."
        kind, base = "delta", previous["ply"] - 1
    prompt = instruction + "\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    details = {"prompt_mode": "incremental", "kind": kind, "base_ply": base}
    if version == 2:
        details["input_version"] = 2
    return prompt, schema, details


def illegal_empty(game):
    legal = set(game.legal_actions())
    return [game._coordinate(point) for point, stone in enumerate(game._board)
            if stone == "." and game._coordinate(point) not in legal]


def recovery_input(game, turns, snapshots, *, rejection=None):
    """Replace session board memory after a failed request; never repeat deltas."""
    normal, schema, _ = session_input(game, turns, snapshots)
    feedback = json.loads(normal.split("\n", 1)[1]).get("your_last_turn")
    state = game.snapshot()
    payload = {"board":state["board"], "ply":len(turns), "current_player":game.current_player,
               "illegal_empty":illegal_empty(game)}
    if "phase" in state:
        payload.update(phase=state["phase"], negotiation=state["negotiation"])
    if feedback:
        payload["your_last_turn"] = feedback
    instruction = (
        f"The previous request for ply {len(turns)+1} failed; no action was committed. "
        "Replace your stored position with this authoritative board. Do not reapply previous updates; "
        "your_last_turn is timing feedback only. Keep the original rules and tool restrictions. "
        'Return exactly one JSON object, no prose or Markdown: {"move":"...","explanation":"1-2句中文公开说明"}.'
    )
    if rejection:
        move = rejection["action"]["move"]
        stone = game._board[game._point(move)]
        owner = {"B":"black", "W":"white", ".":"no"}[stone]
        instruction = (
            f"Your previous move {move} for ply {len(turns)+1} was rejected: {rejection['message']}. "
            f"{move} currently has {owner} stone. That move was NOT committed; it is still {game.current_player}'s turn. "
            "Replace your stored position with this authoritative board; do not reapply earlier updates. "
            "your_last_turn is timing feedback only. Keep the original rules and tool restrictions. "
            "Choose an empty point outside illegal_empty, or pass/resign. Use go_get_state if uncertain. "
            'Return exactly one JSON object, no prose or Markdown: {"move":"...","explanation":"1-2句中文公开说明"}.'
        )
        payload.update(rejected_action=rejection["action"], rejection_reason=rejection["message"])
    prompt = instruction + "\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return prompt, schema, {"prompt_mode":"incremental", "kind":"recovery", "base_ply":len(turns),
                            "input_version":2, "recovery_of_ply":len(turns)+1}


def recovery_rejection(record, ply):
    """The latest archived rejection that preceded a committed recovery input."""
    return next((c["recovery"] for c in reversed(record.get("continuations", []))
                 if c.get("recovery", {}).get("ply") == ply
                 and c["recovery"].get("reason") == "illegal_move"), None)
