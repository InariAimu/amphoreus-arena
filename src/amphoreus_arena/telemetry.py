"""Small, explicit usage adapters; never equate context occupancy with consumption."""
import json
from pathlib import Path


def dsh_usage(update: dict) -> dict:
    return {"kind": "context", "source": "acp.usage_update",
            "context_tokens": update.get("used"), "context_limit": update.get("size")}


def codex_usage(usage: dict) -> dict:
    # exec/resume 0.162.0 reports the session's running counters, including cached
    # input. Preserve the native scope rather than summing these on every move.
    return {"kind": "session", "source": "codex.turn.completed", **usage}


def enrich_usage(directory: Path, record: dict) -> dict:
    """Rebuild missing per-move telemetry from this match's logs. No inference."""
    for side, player in record["players"].items():
        turns = [turn for turn in record["turns"] if turn["player"] == side]
        if player["kind"] == "codex":
            for turn in turns:
                path = directory / side / f"{turn['ply']:04d}.events.jsonl"
                if not path.exists() or turn.get("usage"):
                    continue
                for line in path.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    if event.get("type") == "turn.completed" and event.get("usage"):
                        turn["usage"] = codex_usage(event["usage"])
        elif player["kind"] == "dsh":
            path = directory / side / "protocol.jsonl"
            if not path.exists():
                continue
            turn_index, active_id, usage = -1, None, None
            with path.open(encoding="utf-8") as source:
                for line in source:
                    event = json.loads(line)
                    frame = event["frame"]
                    if event["direction"] == "out" and frame.get("method") == "session/prompt":
                        turn_index += 1
                        active_id, usage = frame.get("id"), None
                    elif event["direction"] == "in" and active_id is not None:
                        update = frame.get("params", {}).get("update", {})
                        if update.get("sessionUpdate") == "usage_update":
                            usage = dsh_usage(update)
                        if frame.get("id") == active_id:
                            if usage and 0 <= turn_index < len(turns) and frame.get("result", {}).get("stopReason") == "end_turn":
                                if not turns[turn_index].get("usage"):
                                    turns[turn_index]["usage"] = usage
                            active_id = None
    return record
