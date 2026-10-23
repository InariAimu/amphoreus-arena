"""Per-match native MCP bridge. Stage one decision; Runner commits it once.

The loopback endpoint is private to the two stdio MCP adapters. Credentials bind
each adapter to a seat; callers cannot choose an identity or edit the board.
"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading


def schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


REVISION = {"type": "integer", "description": "Current negotiation.revision from the supplied position"}
STONES = {"type": "array", "items": {"type": "string"}, "maxItems": 361,
          "description": "One or more coordinates per dead group, either color; [] means all live"}
TOOLS = [
    {"name": "go_get_state", "description": "Read the current Go position, agreement proposal and your seat.",
     "inputSchema": schema({}), "annotations": {"readOnlyHint": True}},
    {"name": "go_preview_score", "description": "Preview area score after hypothetical dead-group removal. Does not verify death or submit a decision.",
     "inputSchema": schema({"stones": STONES}), "annotations": {"readOnlyHint": True}},
    {"name": "go_propose_dead", "description": "Submit your complete dead-group proposal for the opponent to review. Does not remove stones. After success return move=tool.",
     "inputSchema": schema({"stones": STONES, "revision": REVISION})},
    {"name": "go_accept_dead", "description": "Accept the opponent's exact proposal and end the game with agreed area scoring. After success return move=tool.",
     "inputSchema": schema({"proposal_id": {"type": "string"}, "revision": REVISION})},
    {"name": "go_resume_play", "description": "Dispute a stone's status and resume with the player claiming it dead first. Listed stone: you claim it alive; unlisted: you claim it dead. Without a proposal use null and the first passer resumes. Preserves board and ko history. After success return move=tool.",
     "inputSchema": schema({"revision": REVISION, "disputed_stone": {"type": ["string", "null"]}})},
]
for tool in TOOLS:
    tool.setdefault("annotations", {"readOnlyHint": False, "destructiveHint": False,
                                    "idempotentHint": True, "openWorldHint": False})


class GoToolBridge:
    def __init__(self, game):
        self.game = game
        self.tokens = {side: secrets.token_urlsafe(32) for side in ("black", "white")}
        self.lock = threading.Lock()
        self.active = None
        self.pending = None
        self.calls = []
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    if self.path != "/call":
                        raise ValueError("Unknown endpoint")
                    token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    side = next((s for s, t in bridge.tokens.items() if secrets.compare_digest(t, token)), None)
                    if side is None:
                        self.send_error(403)
                        return
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 65536:
                        raise ValueError("Invalid request length")
                    request = json.loads(self.rfile.read(length))
                    value = bridge.call(side, request["name"], request.get("arguments", {}))
                    response = {"ok": True, "value": value}
                except (ValueError, TypeError, KeyError) as exc:
                    response = {"ok": False, "error": str(exc)}
                body = json.dumps(response, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def connection(self, side):
        return {"name": "arena_go", "command": sys.executable,
                "args": [str(Path(__file__).with_name("go_mcp.py").resolve())],
                "env": {"ARENA_GO_URL": f"http://127.0.0.1:{self.server.server_port}/call",
                        "ARENA_GO_TOKEN": self.tokens[side]}}

    def begin_turn(self, side, ply):
        with self.lock:
            self.active = (side, ply)
            self.pending = None
            self.calls = []

    def call(self, side, name, arguments):
        with self.lock:
            entry = {"player": side, "name": name, "arguments": deepcopy(arguments)}
            try:
                if not self.active or self.active[0] != side or self.game.current_player != side:
                    raise ValueError("This seat has no active turn")
                spec = next((t for t in TOOLS if t["name"] == name), None)
                if spec is None or not isinstance(arguments, dict) or set(arguments) != set(spec["inputSchema"]["properties"]):
                    raise ValueError("Unknown tool or invalid arguments")
                if name == "go_get_state":
                    result = {"player": side, "state": self.game.observation(), "pending": deepcopy(self.pending)}
                else:
                    if self.game.phase != "agreement":
                        raise ValueError("This tool requires the agreement phase")
                    if "revision" in arguments and type(arguments["revision"]) is not int:
                        raise ValueError("revision must be an integer")
                    if "stones" in arguments and (not isinstance(arguments["stones"], list) or len(arguments["stones"]) > 361):
                        raise ValueError("stones must contain at most 361 coordinates")
                    if name == "go_preview_score":
                        result = self.game.preview_score(arguments["stones"])
                    else:
                        action = {"move": name.removeprefix("go_"), **arguments}
                        # Validate on a copy. Invalid or late calls cannot alter game state.
                        deepcopy(self.game).step(action)
                        if self.pending is not None and self.pending != action:
                            raise ValueError("One decision per turn; a decision is already staged")
                        self.pending = deepcopy(action)
                        result = {"submitted": True, "action": action,
                                  "commit": "Return move=tool to commit this decision; state is unchanged until then"}
                entry["result"] = deepcopy(result)
                return result
            except (ValueError, TypeError, KeyError) as exc:
                entry["error"] = str(exc)
                raise
            finally:
                self.calls.append(entry)

    def finish_turn(self, action):
        with self.lock:
            self.active = None
            if self.pending is not None:
                if action != {"move": "tool"}:
                    raise ValueError("Staged native decision requires move=tool acknowledgement")
                return deepcopy(self.pending)
            if self.game.phase == "agreement" and action != {"move": "resign"}:
                raise ValueError("No native decision tool was called")
            return action

    def close(self):
        with self.lock:
            self.active = None
            self.tokens.clear()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
