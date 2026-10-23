"""Explicit short native-tool smoke on fixed 19x19 positions, never a full game.

Calls both real harnesses twice: propose/accept and propose/dispute. Requires
--real to acknowledge model calls. This is a transport fixture, not a match.
"""
import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import time

from amphoreus_arena.go_agreement import AgreementGoGame
from amphoreus_arena.go_tools import GoToolBridge
from amphoreus_arena.harnesses import make_harness
from amphoreus_arena.runner import parse_action


def fixture():
    game = AgreementGoGame()
    for move in ("A2", "A1", "B2", "T19", "C1", "T18", "pass", "pass"):
        game.step({"move": move})
    return game


async def run(args):
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    directory = Path(args.output).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    game = fixture()
    bridge = GoToolBridge(game)
    players = {side: make_harness(value, directory / side) for side, value in config["players"].items()}
    rows = []
    try:
        for side, player in players.items():
            player.configure_game_tools(bridge.connection(side))
            print(f"Connecting {side}: {player.metadata['kind']}", flush=True)
            await asyncio.wait_for(player.start(), 60)
        for index, (actor, decision) in enumerate((("black", "propose_dead"), ("white", "accept_dead"),
                                                  ("white", "propose_dead"), ("black", "resume_play")), 1):
            if index == 3:
                game = fixture()
                game._current_player = "white"
                game.resume_player = "white"
                bridge.game = game
            instruction = {
                "propose_dead": 'Call go_get_state, then go_preview_score with stones=["A1"], then go_propose_dead with stones=["A1"] and the current revision.',
                "accept_dead": 'Call go_get_state, then go_accept_dead using the exact current proposal ID and revision.',
                "resume_play": 'Call go_get_state, then go_resume_play with disputed_stone="A1" and the current revision. This deliberately exercises the dispute path.'
            }[decision]
            prompt = ("This is an explicitly scripted native MCP transport fixture, not a competitive game. "
                      "Do not reassess or substitute the requested decision. " + instruction +
                      ' After successful tool submission return ONLY {"move":"tool","explanation":"简短中文操作说明"}.\n' +
                      json.dumps(game.observation(), ensure_ascii=False))
            bridge.begin_turn(actor, index)
            print(f"Native call {index}: {actor} / {decision}", flush=True)
            started = time.monotonic()
            raw = await asyncio.wait_for(players[actor].act(prompt, game.action_schema(), index), 180)
            action = parse_action(raw)
            explanation = action.pop("explanation", "")
            applied = bridge.finish_turn(action)
            if applied["move"] != decision:
                raise AssertionError(f"Unexpected native decision: {applied}")
            game.step(applied)
            rows.append({"player": actor, "action": applied, "explanation": explanation,
                         "elapsed_seconds": round(time.monotonic()-started, 2),
                         "calls": deepcopy(bridge.calls), "state": game.snapshot()})
            (directory / "checks.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Passed {decision}: phase={game.phase}", flush=True)
        assert rows[1]["state"]["result"]["reason"] == "agreement"
        assert game.phase == "play" and game.current_player == "white"
        report = {"passed": True, "scope": "Native MCP fixed-position transport only; no full match", "model_turns": 4,
                  "players": {side: player.metadata for side, player in players.items()}, "checks": rows}
        (directory / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "model_turns": 4, "output": str(directory)}), flush=True)
    finally:
        (directory / "last-tool-calls.json").write_text(json.dumps(bridge.calls, ensure_ascii=False, indent=2), encoding="utf-8")
        await asyncio.to_thread(bridge.close)
        for player in players.values():
            await player.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", required=True)
    parser.add_argument("--config", default="configs/go-19x19.json")
    parser.add_argument("--output", required=True)
    asyncio.run(run(parser.parse_args()))
