"""Explicit recovery of proven, uncommitted overloads or rejected Go moves."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify_failed_attempt(directory, record, *, session_root=None):
    """Fail closed on partial output, tools, changed sessions or unknown failures."""
    error = record.get("error") or {}
    ply, side = len(record["turns"]) + 1, error.get("player")
    if (record.get("status") != "failed" or record.get("stop_reason") != "error"
            or record.get("result") is not None or record.get("active_turn")
            or not record.get("ended_at") or error.get("phase") != "inference"
            or error.get("ply") != ply or side not in record["players"]
            or record["players"][side]["kind"] != "codex"
            or record["game_id"] != "go" or record.get("prompt_mode") != "incremental"):
        raise ValueError("Failed recovery requires an uncommitted Codex Go inference failure")
    seat = directory / side
    if (seat / f"{ply:04d}.response.txt").exists():
        raise ValueError("Refusing recovery with a response file")
    events = [json.loads(line) for line in (seat / f"{ply:04d}.events.jsonl").read_text(encoding="utf-8").splitlines()]
    if [e.get("type") for e in events] != ["thread.started", "turn.started", "error", "turn.failed"]:
        raise ValueError("Unsupported or partially completed native attempt")
    session_id = record["players"][side]["session_id"]
    message = events[-1].get("error", {}).get("message", "")
    if (events[0].get("thread_id") != session_id or "model is at capacity" not in message
            or events[2].get("message") != message):
        raise ValueError("Only an explicit same-session model-capacity failure is recoverable")
    arena_events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    starts = [i for i, e in enumerate(arena_events) if e.get("type") == "turn.started" and e.get("ply") == ply]
    if not starts:
        raise ValueError("Missing failed turn event")
    tail = arena_events[starts[-1]:]
    if (any(e.get("type") in ("turn.response", "turn.applied") for e in tail)
            or not any(e.get("type") == "tools.closed" and e.get("calls") == [] for e in tail)
            or any(e.get("type") == "tools.closed" and e.get("calls") for e in tail)
            or tail[-1].get("type") != "match.ended" or tail[-1].get("status") != "failed"):
        raise ValueError("Failed turn contains a response, tool call or ambiguous completion")
    observation = error.get("input") or {}
    if observation.get("prompt", {}).get("path") != f"observations/{ply:04d}.prompt.txt":
        raise ValueError("Invalid failed input path")
    prompt_file = directory / observation["prompt"]["path"]
    prompt = prompt_file.read_text(encoding="utf-8")
    manifest = read(directory / f"observations/{ply:04d}.json")
    if (hashlib.sha256(prompt_file.read_bytes()).hexdigest() != observation["prompt"]["sha256"]
            or observation.get("source_ply") != ply - 1
            or manifest.get("state") != record["snapshots"][-1]["state"]
            or (seat / f"{ply:04d}.prompt.txt").read_text(encoding="utf-8") != prompt
            or tail[0].get("input") != observation):
        raise ValueError("Failed input does not match the committed position")
    root = session_root or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    logs = list(root.rglob(f"*{session_id}.jsonl"))
    if len(logs) != 1:
        raise ValueError("Cannot uniquely locate the original native session")
    native = [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]
    starts = [i for i, e in enumerate(native) if e.get("type") == "event_msg" and e.get("payload", {}).get("type") == "task_started"]
    if not starts:
        raise ValueError("Missing native task boundary")
    native = native[starts[-1]:]
    contexts = [e["payload"] for e in native if e.get("type") == "turn_context"]
    users = [e["payload"] for e in native if e.get("type") == "response_item" and e.get("payload", {}).get("type") == "message" and e["payload"].get("role") == "user"]
    completed = [e["payload"] for e in native if e.get("type") == "event_msg" and e.get("payload", {}).get("type") == "task_complete"]
    has_output = any(e.get("type") == "response_item" and (
        e.get("payload", {}).get("type") in ("function_call", "custom_tool_call", "reasoning")
        or e.get("payload", {}).get("role") == "assistant") for e in native)
    if (has_output or len(users) != 1 or not any(c.get("text") == prompt for c in users[0].get("content", []))
            or len(completed) != 1 or completed[0].get("last_agent_message") is not None
            or completed[0].get("error", {}).get("codex_error_info") != "server_overloaded"
            or not contexts or contexts[-1].get("model") != record["players"][side]["model"]
            or contexts[-1].get("effort") != record["players"][side]["reasoning_effort"]):
        raise ValueError("Native session is changed, partially completed or not an overload failure")
    return {"ply": ply, "player": side, "session_id": session_id, "reason": "server_overloaded",
            "message": message, "native_turn_id": completed[0].get("turn_id"),
            "failed_input_in_context": True, "no_output_or_tools": True}


def archive_failed_attempt(directory, evidence):
    """Copy all files that a retry can overwrite before permitting another call."""
    ply = evidence["ply"]
    parent = directory / "attempts"
    parent.mkdir(exist_ok=True)
    index = 1
    while (parent / f"{ply:04d}-{index:02d}").exists():
        index += 1
    archive = parent / f"{ply:04d}-{index:02d}"
    archive.mkdir()
    paths = [p for p in directory.iterdir() if p.is_file() and p.suffix in (".json", ".jsonl", ".log", ".md")]
    paths += list((directory / "observations").glob(f"{ply:04d}.*"))
    paths += list((directory / evidence["player"]).glob(f"{ply:04d}.*"))
    paths += list((directory / "black").glob("protocol.jsonl"))
    entries = []
    for source in paths:
        relative = source.relative_to(directory)
        destination = archive / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
            raise ValueError("Failed attempt archive verification failed")
        entries.append({"path": relative.as_posix(), "sha256": digest, "bytes": source.stat().st_size})
    (archive / "archive-manifest.json").write_text(json.dumps({"at":datetime.now(timezone.utc).isoformat(),
        "evidence":evidence, "files":entries}, ensure_ascii=False, indent=2), encoding="utf-8")
    return archive.relative_to(directory).as_posix()


def verify_illegal_attempt(directory, record):
    """Verify a normal DSH completion whose canonical Go point was rejected."""
    from .go_agreement import create_go
    from .runner import parse_action
    error = record.get("error") or {}
    ply, side = len(record["turns"]) + 1, error.get("player")
    reasons = {"Go intersection is occupied", "Go suicide is forbidden",
               "Go positional superko: repeated board"}
    if (record.get("status") != "failed" or record.get("stop_reason") != "error"
            or record.get("result") is not None or record.get("active_turn")
            or not record.get("ended_at") or error.get("phase") != "action_validation"
            or error.get("type") != "ValueError" or error.get("message") not in reasons
            or error.get("ply") != ply or side not in record["players"]
            or record["players"][side]["kind"] != "dsh" or record["game_id"] != "go"
            or record.get("prompt_mode") != "incremental"):
        raise ValueError("Illegal recovery requires an uncommitted DSH Go point rejection")
    game = create_go(**record["game_options"])
    for turn in record["turns"]:
        game.step(turn["action"])
    if (game.snapshot() != record["snapshots"][-1]["state"] or game.current_player != side
            or getattr(game,"phase","play") != "play" or game.outcome() is not None):
        raise ValueError("Rejected move does not match the restored play position")
    raw = (directory / side / f"{ply:04d}.response.txt").read_text(encoding="utf-8")
    action = parse_action(raw)
    explanation = action.pop("explanation", "")
    if set(action) != {"move"} or not isinstance(action["move"], str) or not isinstance(explanation, str):
        raise ValueError("Only a canonical Go point action can be retried")
    point = game._point(action["move"])
    if action["move"] in game.legal_actions():
        raise ValueError("Refusing to retry a legal action that might have been committed")
    before = game.snapshot()
    try:
        game.step(action)
    except ValueError as exc:
        if str(exc) != error["message"] or game.snapshot() != before:
            raise ValueError("Rejected action changed state or has a different validation error") from exc
    else:
        raise ValueError("Rejected move was not independently reproduced")
    observation = error.get("input") or {}
    if observation.get("prompt", {}).get("path") != f"observations/{ply:04d}.prompt.txt":
        raise ValueError("Invalid rejected input path")
    prompt_file = directory / observation["prompt"]["path"]
    prompt = prompt_file.read_text(encoding="utf-8")
    manifest = read(directory / f"observations/{ply:04d}.json")
    if (hashlib.sha256(prompt_file.read_bytes()).hexdigest() != observation["prompt"]["sha256"]
            or observation.get("source_ply") != ply - 1 or manifest.get("state") != before
            or (directory / side / f"{ply:04d}.prompt.txt").read_text(encoding="utf-8") != prompt):
        raise ValueError("Rejected input does not match the committed position")
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    starts = [i for i, e in enumerate(events) if e.get("type") == "turn.started" and e.get("ply") == ply]
    if not starts:
        raise ValueError("Missing rejected turn boundary")
    tail = events[starts[-1]:]
    responses = [e for e in tail if e.get("type") == "turn.response"]
    if (tail[0].get("input") != observation or len(responses) != 1 or responses[0].get("raw") != raw
            or responses[0].get("player") != side or responses[0].get("ply") != ply
            or any(e.get("type") == "turn.applied" for e in tail)
            or not any(e.get("type") == "tools.closed" and e.get("calls") == [] for e in tail)
            or any(e.get("type") == "tools.closed" and e.get("calls") for e in tail)
            or tail[-1].get("type") != "match.ended" or tail[-1].get("status") != "failed"):
        raise ValueError("Rejected attempt has tools, a commit or ambiguous response evidence")
    frames = [json.loads(line) for line in (directory / side / "protocol.jsonl").read_text(encoding="utf-8").splitlines()]
    starts = [i for i, e in enumerate(frames) if e.get("direction") == "out" and e["frame"].get("method") == "session/prompt"]
    if not starts:
        raise ValueError("Missing native rejected prompt")
    frames = frames[starts[-1]:]
    request = frames[0]["frame"]
    session_id = record["players"][side]["session_id"]
    updates = [e["frame"]["params"] for e in frames if e.get("direction") == "in" and e["frame"].get("method") == "session/update"]
    completions = [e["frame"] for e in frames if e.get("direction") == "in" and e["frame"].get("id") == request.get("id")]
    text = "".join(u["update"].get("content", {}).get("text", "") for u in updates
                   if u["update"].get("sessionUpdate") == "agent_message_chunk")
    if (request["params"].get("sessionId") != session_id
            or not any(c.get("type") == "text" and c.get("text") == prompt for c in request["params"]["prompt"])
            or any(u.get("sessionId") != session_id for u in updates)
            or any(u["update"].get("sessionUpdate") in ("tool_call", "tool_call_update") for u in updates)
            or text != raw or len(completions) != 1 or completions[0].get("result", {}).get("stopReason") != "end_turn"):
        raise ValueError("Native attempt is changed, incomplete or contains tools")
    return {"ply":ply, "player":side, "session_id":session_id, "reason":"illegal_move",
            "action":action, "message":error["message"], "point_stone":game._board[point],
            "elapsed_seconds":round(responses[0]["elapsed_seconds"],3),
            "native_end_turn":True, "no_commit_or_tools":True}
