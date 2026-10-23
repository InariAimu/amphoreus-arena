"""A local protocol peer for adapter tests; never used as a real match player."""
import json
import base64
import hashlib
import sys

# ACP is UTF-8, independently of the Windows console's default code page.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

if "--version" in sys.argv:
    print("test-acp 1")
    sys.exit(0)


def send(value):
    print(json.dumps({"jsonrpc": "2.0", **value}), flush=True)


for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        continue
    if method == "initialize":
        print("[test launcher] diagnostic on stdout", flush=True)
        result = {"protocolVersion": 1, "agentInfo": {"version": "test"},
                  "agentCapabilities": {"promptCapabilities": {"image": True}}}
    elif method == "session/new":
        result = {"sessionId": "stable-test-session", "configOptions": []}
    elif method == "session/prompt":
        send({"method": "session/update", "params": {"sessionId": "stable-test-session",
              "update": {"sessionUpdate": "usage_update", "used": 7074, "size": 1000000}}})
        images = [part for part in request["params"]["prompt"] if part["type"] == "image"]
        chunks = ['{"move":', '"h3e3"}']
        if images:
            assert images[0]["mimeType"] == "image/png"
            data = base64.b64decode(images[0]["data"], validate=True)
            chunks = [json.dumps({"image_sha256": hashlib.sha256(data).hexdigest()})]
        if "--arena-moves" in sys.argv:
            prompt = next(part["text"] for part in request["params"]["prompt"] if part["type"] == "text")
            observation = json.loads(prompt.split("\n", 1)[1])
            chunks = [json.dumps({"move": observation["legal_moves"][0],
                                  "explanation": hashlib.sha256(data).hexdigest() if images else "text only"})]
        for text in chunks:
            send({"method": "session/update", "params": {"sessionId": "stable-test-session",
                  "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
        result = {"stopReason": "end_turn"}
    else:
        result = {}
    send({"id": request["id"], "result": result})
