"""Generate two short, scripted 19x19 native MCP replay fixtures. No model calls."""
import argparse
import asyncio
import json
import os
from pathlib import Path

from amphoreus_arena.go_agreement import AgreementGoGame, AGREEMENT_RULESET
from amphoreus_arena.go_rendering import GoRenderer, render_go_observation
from amphoreus_arena.process import spawn, stop
from amphoreus_arena.runner import MatchRunner


class ScriptedMcpPlayer:
    def __init__(self, moves):
        self.moves = iter(moves)
        self.metadata = {"kind": "scripted-mcp", "model": "local fixture", "reasoning_effort": "none"}
        self.connection = None
        self.process = None
        self.counter = 0

    def configure_game_tools(self, connection):
        self.connection = connection

    async def rpc(self, method, params):
        self.counter += 1
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": self.counter, "method": method, "params": params})+'\n').encode())
        await self.process.stdin.drain()
        reply = json.loads(await asyncio.wait_for(self.process.stdout.readline(), 5))
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply["result"]

    async def start(self):
        c = self.connection
        self.process = await spawn([c["command"], *c["args"]], env={**os.environ, **c["env"]},
                                   stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                   stderr=asyncio.subprocess.PIPE)
        await self.rpc("initialize", {"protocolVersion": "2024-11-05"})

    async def act(self, prompt, schema, ply, images=None):
        move = next(self.moves)
        explanation = "本地固定规则样例，不调用模型。"
        if isinstance(move, dict):
            state = json.loads(prompt.split("\n", 1)[1])
            arguments = {"revision": state["negotiation"]["revision"]}
            if move["tool"] == "propose_dead":
                arguments["stones"] = move["stones"]
                explanation = "提议图中标记的棋块为死子，等待对方确认。"
            elif move["tool"] == "accept_dead":
                arguments["proposal_id"] = state["negotiation"]["proposal"]["id"]
                explanation = "接受对方死子提案，双方确认后数子。"
            else:
                arguments["disputed_stone"] = move["disputed_stone"]
                explanation = "对这块棋的死活有异议，请主张它死亡的一方先行续弈。"
            result = await self.rpc("tools/call", {"name": "go_"+move["tool"], "arguments": arguments})
            if result["isError"]:
                raise RuntimeError(result)
            move = "tool"
        return json.dumps({"move": move, "explanation": explanation}, ensure_ascii=False)

    async def close(self):
        await stop(self.process)


async def prepare(root, images=True):
    root.mkdir(parents=True, exist_ok=False)
    opening = ["A2", "A1", "B2", "T19", "C1", "T18", "pass", "pass"]
    propose = {"tool": "propose_dead", "stones": ["A1"]}
    accept = {"tool": "accept_dead"}
    cases = [
        ("agreement", opening + [propose, accept]),
        ("dispute", opening + [propose, {"tool": "resume_play", "disputed_stone": "A1"},
                               "B1", "pass", "pass", {"tool": "propose_dead", "stones": []}, accept]),
    ]
    records = []
    for name, moves in cases:
        # This particular dispute restores Black, matching alternation at index 10.
        players = {side: ScriptedMcpPlayer(moves[i::2]) for i, side in enumerate(("black", "white"))}
        runner = MatchRunner(AgreementGoGame(), players, GoRenderer(), root/name, len(moves), 10,
                             observation_mode="text_image" if images else "text",
                             observation_renderer=render_go_observation)
        runner.record["game_options"] = {"board_size": 19, "komi": 7.5, "rules": AGREEMENT_RULESET}
        record = await runner.run()
        if record["status"] != "finished":
            raise RuntimeError(record["error"])
        records.append(record)
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(prepare(parser.parse_args().output.resolve()))
