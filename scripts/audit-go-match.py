"""Reconstruct a recorded Go match and verify every input, image and transition.

Offline integrity audit only; this does not evaluate playing strength or use an
engine to adjudicate dead stones. Uses the recorded, versioned Arena rules.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile

from amphoreus_arena.go_agreement import create_go, CONTROL_MOVES
from copy import deepcopy
from amphoreus_arena.go_rendering import render_go_observation
from amphoreus_arena.go_session import session_input, recovery_input, recovery_rejection


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory):
    record = read_json(directory / "match.json")
    if record["game_id"] != "go":
        raise ValueError("Expected a Go match")
    if record["status"] in ("starting", "running"):
        raise ValueError("Wait for the match to stop before auditing")
    game = create_go(**record["game_options"])
    snapshots = record["snapshots"]
    if len(snapshots) != len(record["turns"]) + 1 or snapshots[0]["state"] != game.snapshot():
        raise ValueError("Initial state or snapshot count mismatch")
    rows, captures = [], []
    with tempfile.TemporaryDirectory(prefix="arena-go-audit-") as temp:
        expected_image = Path(temp) / "expected.png"
        for index, turn in enumerate(record["turns"], 1):
            side, move = turn["player"], turn["action"]["move"]
            observation = turn["input"]
            manifest = read_json(directory / f"observations/{index:04d}.json")
            prompt_path = directory / observation["prompt"]["path"]
            supplied = json.loads(prompt_path.read_text(encoding="utf-8").split("\n", 1)[1])
            expected_observation, expected_schema = game.observation(), game.action_schema()
            input_details = {}
            if record.get("prompt_mode", "full") == "incremental":
                if observation.get("kind") == "recovery":
                    expected_prompt, expected_schema, input_details = recovery_input(game, record["turns"][:index-1], snapshots,
                                                                                   rejection=recovery_rejection(record,index))
                else:
                    expected_prompt, expected_schema, input_details = session_input(
                        game, record["turns"][:index - 1], snapshots, version=observation.get("input_version", 1))
                expected_observation = json.loads(expected_prompt.split("\n", 1)[1])
            valid = move in game.legal_actions()
            if move in CONTROL_MOVES:
                deepcopy(game).step(turn["action"])
                valid = any(call.get("name") == "go_" + move and
                            call.get("result", {}).get("action") == turn["action"] and
                            call.get("result", {}).get("submitted") is True
                            for call in turn.get("tool_calls", []))
            checks = {
                "turn_order": index == turn["ply"] and side == game.current_player,
                "legal": valid,
                "observation": supplied == expected_observation,
                "session_input": all(observation.get(k) == v for k, v in input_details.items()),
                "input_state": manifest["state"] == game.snapshot() and observation["source_ply"] == index - 1,
                "action_schema": manifest["action_schema"] == expected_schema,
                "prompt_hash": digest(prompt_path) == observation["prompt"]["sha256"],
            }
            if observation["mode"] == "text_image":
                render_go_observation(game.snapshot(), expected_image)
                checks["image"] = (len(observation["images"]) == 1 and all(
                    digest(directory / image["path"]) == image["sha256"] == digest(expected_image)
                    for image in observation["images"]))
            else:
                checks["image"] = observation["images"] == []
            provider_prompt = directory / side / f"{index:04d}.prompt.txt"
            if provider_prompt.exists():
                # Harness logs use text-mode writes (CRLF on Windows); the actual
                # prompt content must match after normalizing those newlines.
                checks["harness_prompt"] = provider_prompt.read_text(encoding="utf-8") == prompt_path.read_text(encoding="utf-8")
            if not all(checks.values()):
                raise ValueError(f"Input verification failed at ply {index}: {checks}")
            before = sum(len(row.replace(".", "")) for row in game.snapshot()["board"])
            game.step(turn["action"])
            after_state = game.snapshot()
            after = sum(len(row.replace(".", "")) for row in after_state["board"])
            checks["snapshot"] = snapshots[index]["state"] == after_state
            checks["stone_conservation"] = after == before + (0 if move in {"pass", "resign"} | CONTROL_MOVES else 1) - len(game.last_captures)
            if not all(checks.values()):
                raise ValueError(f"Transition verification failed at ply {index}: {checks}")
            if game.last_captures:
                captures.append({"ply": index, "player": side, "move": move, "captured": game.last_captures.copy()})
            rows.append({"ply": index, "player": side, "move": move, **checks})
    if record["result"] != game.outcome():
        raise ValueError("Recorded result differs from reconstructed result")
    if (record["status"] == "finished") != (game.outcome() is not None):
        raise ValueError("Terminal status does not match game outcome")
    duration = None
    if record.get("ended_at"):
        duration = round((datetime.fromisoformat(record["ended_at"]) - datetime.fromisoformat(record["started_at"])).total_seconds(), 2)
    summary = {
        "passed": True, "scope": "Rule and input integrity; no playing-strength evaluation or engine score",
        "match": directory.name, "board_size": game.board_size, "rules": game.rules,
        "status": record["status"], "turns": len(rows), "snapshots": len(snapshots),
        "result": game.outcome(), "elapsed_seconds": duration,
        "players": {side: {"kind": player["kind"], "model": player.get("model"),
                            "moves": sum(t["player"] == side for t in record["turns"]),
                            "inference_seconds": round(sum(t["elapsed_seconds"] for t in record["turns"] if t["player"] == side), 3)}
                    for side, player in record["players"].items()},
        "captures": game.captures, "capture_events": captures,
        "passes": [t["ply"] for t in record["turns"] if t["action"]["move"] == "pass"],
        "final_board_score": game.score(), "per_turn": rows, "real_model_calls": 0,
    }
    destination = directory / "audit-go-match.json"
    destination.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("per_turn", "capture_events")}, ensure_ascii=True))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    audit(parser.parse_args().directory.resolve())
