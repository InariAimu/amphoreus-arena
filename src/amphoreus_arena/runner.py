import asyncio
from copy import deepcopy
import hashlib
import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .contracts import Game, Harness, ObservationRenderer, Renderer
from .rendering import write_replay


def now():
    return datetime.now(timezone.utc).isoformat()


def parse_action(raw: str) -> dict:
    # Accept a single Markdown JSON fence, but never scrape an arbitrary move out of prose.
    value = raw.strip()
    if value.startswith("```") and value.endswith("```"):
        value = value.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    action = json.loads(value)
    if not isinstance(action, dict):
        raise ValueError("Expected one JSON action object")
    return action


class MatchRunner:
    def __init__(self, game: Game, players: dict[str, Harness], renderer: Renderer,
                 directory: Path, max_plies: int | None, turn_timeout: float | None,
                 *, observation_mode: str = "text",
                 observation_renderer: ObservationRenderer | None = None,
                 startup_timeout: float = 60):
        if observation_mode not in ("text", "text_image"):
            raise ValueError("observation_mode must be text or text_image")
        if observation_mode == "text_image" and observation_renderer is None:
            raise ValueError("text_image requires an observation renderer for this game")
        self.game, self.players, self.renderer = game, players, renderer
        self.directory = directory
        self.max_plies, self.turn_timeout = max_plies, turn_timeout
        self.observation_mode, self.observation_renderer = observation_mode, observation_renderer
        self.startup_timeout = startup_timeout
        self.game_tools = None
        self.record = {"schema_version": 1, "game_id": game.game_id, "started_at": now(),
                       "status": "starting", "stop_reason": None, "result": None,
                       "max_plies": max_plies, "turn_timeout": turn_timeout,
                       "startup_timeout": startup_timeout, "observation_mode": observation_mode,
                       "players": {k: v.metadata for k, v in players.items()},
                       "snapshots": [], "turns": [], "error": None}
        (directory / "frames").mkdir(parents=True, exist_ok=True)
        (directory / "observations").mkdir(exist_ok=True)

    def prepare_input(self, ply: int):
        # Capture the pre-move state, never the post-move audience frame.
        state = deepcopy(self.game.snapshot())
        prompt = self.game.prompt()
        schema = self.game.action_schema()
        images, attachments = [], []
        if self.observation_mode == "text_image":
            relative = f"observations/{ply:04d}.png"
            path = self.directory / relative
            self.observation_renderer(state, path)
            data = path.read_bytes()
            images.append(path.resolve())
            attachments.append({"path": relative, "mime_type": "image/png", "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()})
            prompt = ("The attached image shows the current position before your move. "
                      "Use the supplied text as authoritative if they differ. " + prompt)
        relative = f"observations/{ply:04d}.prompt.txt"
        data = prompt.encode("utf-8")
        (self.directory / relative).write_bytes(data)
        observation = {"mode": self.observation_mode, "source_ply": len(self.record["turns"]),
                       "prompt": {"path": relative, "sha256": hashlib.sha256(data).hexdigest()},
                       "images": attachments}
        (self.directory / f"observations/{ply:04d}.json").write_text(
            json.dumps({**observation, "state": state, "action_schema": schema}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        return prompt, schema, images, observation

    def event(self, kind: str, **data):
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now(), "type": kind, **data}, ensure_ascii=False) + "\n")

    def persist(self):
        self.record["updated_at"] = now()
        temp = self.directory / "match.json.tmp"
        temp.write_text(json.dumps(self.record, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.directory / "match.json")
        write_replay(self.directory, self.record)

    def snapshot(self, refresh=False):
        ply = len(self.record["turns"])
        relative = f"frames/{ply:04d}.png"
        state = self.game.snapshot()
        self.renderer.render(state, self.record, self.directory / relative)
        entry = {"ply": ply, "state": state, "frame": relative}
        if refresh:
            self.record["snapshots"][-1] = entry
        else:
            self.record["snapshots"].append(entry)
        self.persist()

    async def run(self):
        self.event("match.started", game_id=self.game.game_id)
        self.snapshot()
        phase, player_id, ply = "startup", None, 0
        try:
            if getattr(self.game, "native_tools", False):
                from .go_tools import GoToolBridge
                self.game_tools = GoToolBridge(self.game)
                for side, player in self.players.items():
                    if not hasattr(player, "configure_game_tools"):
                        raise ValueError(f"{side} harness does not support native game tools")
                    player.configure_game_tools(self.game_tools.connection(side))
            for player_id, player in self.players.items():
                print(f"Connecting {player_id}: {player.metadata['kind']}", flush=True)
                await asyncio.wait_for(player.start(), self.startup_timeout)
                self.event("harness.ready", player=player_id, metadata=player.metadata)
                self.persist()
            self.record["status"] = "running"
            self.snapshot(refresh=True)
            while self.max_plies is None or len(self.record["turns"]) < self.max_plies:
                result = self.game.outcome()
                if result is not None:
                    self.record.update(status="finished", result=result, stop_reason="game_over")
                    break
                player_id = self.game.current_player
                player = self.players[player_id]
                ply = len(self.record["turns"]) + 1
                phase = "observation"
                prompt, schema, images, observation = self.prepare_input(ply)
                phase = "inference"
                self.event("turn.started", ply=ply, player=player_id, input=observation)
                self.record["active_turn"] = {"ply": ply, "player": player_id, "started_at": now(),
                                              "input": observation}
                self.persist()
                print(f"Ply {ply}: {player_id} / {player.metadata['kind']} thinking...", flush=True)
                started = time.monotonic()
                if self.game_tools:
                    self.game_tools.begin_turn(player_id, ply)
                raw = await asyncio.wait_for(player.act(prompt, schema, ply, images=images or None),
                                             self.turn_timeout)
                elapsed = time.monotonic() - started
                self.event("turn.response", ply=ply, player=player_id, raw=raw, elapsed_seconds=elapsed)
                phase = "action_validation"
                action = parse_action(raw)
                explanation = action.pop("explanation", "")
                if not isinstance(explanation, str):
                    raise ValueError("Action explanation must be a string")
                if self.game_tools:
                    action = self.game_tools.finish_turn(action)
                self.game.step(action)
                turn = {"ply": ply, "player": player_id, "action": action,
                        "input": observation,
                        "explanation": explanation, "raw": raw, "elapsed_seconds": round(elapsed, 3),
                        "model": player.metadata.get("model"),
                        "reasoning_effort": player.metadata.get("reasoning_effort"),
                        "usage": deepcopy(player.metadata.get("usage"))}
                if self.game_tools:
                    turn["tool_calls"] = deepcopy(self.game_tools.calls)
                self.record["active_turn"] = None
                self.record["turns"].append(turn)
                self.record["last_elapsed_seconds"] = elapsed
                self.event("turn.applied", **turn)
                phase = "render"
                self.snapshot()
                print(f"  {action} ({elapsed:.1f}s)", flush=True)
            if self.record["status"] == "running":
                result = self.game.outcome()
                if result is not None:
                    self.record.update(status="finished", result=result, stop_reason="game_over")
                else:
                    self.record.update(status="stopped", stop_reason="max_plies")
        except asyncio.CancelledError:
            self.record.update(status="cancelled", stop_reason="cancelled")
            raise
        except Exception as exc:
            timeout = self.startup_timeout if phase == "startup" else self.turn_timeout
            self.record.update(status="failed", stop_reason="error", error={
                "type": type(exc).__name__, "message": str(exc) or (
                    f"Timed out after {timeout}s" if isinstance(exc, asyncio.TimeoutError) else type(exc).__name__),
                "phase": phase, "player": player_id, "ply": ply,
                "input": (self.record.get("active_turn") or {}).get("input")})
            (self.directory / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
            self.event("match.error", **self.record["error"])
            print(f"FAILED: {self.record['error']}", flush=True)
        finally:
            if self.game_tools:
                # Include calls on failed turns too; never silently replace them
                # with a textual claim of tool use.
                self.event("tools.closed", calls=deepcopy(self.game_tools.calls))
                await asyncio.to_thread(self.game_tools.close)
            for player in self.players.values():
                try:
                    await player.close()
                except Exception as exc:
                    self.event("harness.close_error", message=str(exc))
            self.record["ended_at"] = now()
            self.record["active_turn"] = None
            self.event("match.ended", status=self.record["status"], stop_reason=self.record["stop_reason"])
            self.snapshot(refresh=True)
        return self.record
