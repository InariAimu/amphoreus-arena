"""Minimal stdio MCP adapter for the Arena's private, seat-bound Go endpoint."""
import json
import os
from pathlib import Path
import sys
import urllib.request

# Launched by absolute filename, independently of the model workspace cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from amphoreus_arena.go_tools import TOOLS


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        message = {}
        try:
            message = json.loads(line)
            if "id" not in message:
                continue
            method = message.get("method")
            if method == "initialize":
                result = {"protocolVersion": message.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "arena_go", "version": "1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                request = urllib.request.Request(os.environ["ARENA_GO_URL"],
                    data=json.dumps(message["params"]).encode("utf-8"),
                    headers={"Authorization": "Bearer " + os.environ["ARENA_GO_TOKEN"], "Content-Type": "application/json"})
                # Loopback traffic must never be sent to an ambient proxy.
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10) as response:
                    answer = json.load(response)
                result = {"content": [{"type": "text", "text": json.dumps(answer.get("value", answer), ensure_ascii=False)}],
                          "isError": not answer["ok"]}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "Unknown method"}}), flush=True)
                continue
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        except Exception as exc:
            reply = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                     "error": {"code": -32603, "message": str(exc)}}
        print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
