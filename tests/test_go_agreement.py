import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from amphoreus_arena.go import GoGame
from amphoreus_arena.go_agreement import AgreementGoGame, AGREEMENT_RULESET, create_go
from amphoreus_arena.go_tools import GoToolBridge
from amphoreus_arena.harnesses import DshHarness, CodexHarness
from amphoreus_arena.process import spawn, stop
from amphoreus_arena.runner import MatchRunner
from test_arena import NullRenderer
from test_go import play


def settled():
    game = AgreementGoGame()
    play(game, "A2", "A1", "B2", "T19", "C1", "T18", "pass", "pass")
    return game


class AgreementTests(unittest.TestCase):
    def test_original_rules_and_new_factory_are_separate(self):
        self.assertEqual(play(GoGame(19), "pass", "pass").outcome()["reason"], "two_passes")
        game = create_go(board_size=19, rules=AGREEMENT_RULESET)
        play(game, "pass", "pass")
        self.assertIsNone(game.outcome())
        self.assertEqual(game.phase, "agreement")

    def test_proposal_requires_other_player_and_preserves_physical_board(self):
        game = settled()
        before, seen = game._board, game._seen.copy()
        game.step({"move": "propose_dead", "stones": ["A1"], "revision": 1})
        self.assertIsNone(game.outcome())
        self.assertEqual(game._board, before)
        expected = {"black": 5, "white": 9.5, "stones": {"black": 3, "white": 2},
                    "territory": {"black": 2, "white": 0}, "neutral": 354, "komi": 7.5}
        game.step({"move": "accept_dead", "proposal_id": game.proposal["id"], "revision": 1})
        result = game.outcome()
        self.assertEqual(result["confirmed_by"], ["black", "white"])
        self.assertEqual(result["dead"], ["A1"])
        self.assertEqual(result["score"], expected)
        self.assertEqual(game._board, before)
        self.assertEqual(game._seen, seen)
        self.assertEqual(game.captures, {"black": 0, "white": 0})
        with self.assertRaisesRegex(ValueError, "finished"):
            game.step({"move": "resume_play", "revision": 1})

    def test_dispute_restores_proposers_turn_and_keeps_ko_history(self):
        game = settled()
        game.step({"move": "propose_dead", "stones": ["A1"], "revision": 1})
        before, seen = game._board, game._seen.copy()
        game.step({"move": "resume_play", "revision": 1, "disputed_stone": "A1"})
        self.assertEqual(game.current_player, "black")
        self.assertEqual(game.phase, "play")
        self.assertEqual(game.consecutive_passes, 0)
        self.assertEqual((game._board, game._seen), (before, seen))
        play(game, "B1")
        self.assertEqual(game.last_captures, ["A1"])
        play(game, "pass", "pass")
        self.assertEqual(game.revision, 2)
        self.assertIsNone(game.proposal)

    def test_no_proposal_resume_restores_first_passer(self):
        game = play(AgreementGoGame(), "D4", "pass", "pass")
        self.assertEqual(game.current_player, "white")
        game.step({"move": "resume_play", "revision": 1, "disputed_stone": None})
        self.assertEqual(game.current_player, "white")

    def test_unlisted_disputed_group_gives_challenger_first_move(self):
        game = settled()
        game.step({"move": "propose_dead", "stones": [], "revision": 1})
        game.step({"move": "resume_play", "revision": 1, "disputed_stone": "A1"})
        self.assertEqual(game.current_player, "white")

    def test_invalid_decisions_and_stale_acceptance_are_atomic(self):
        game = settled()
        invalid = [
            {"move": "propose_dead", "stones": ["K10"], "revision": 1},
            {"move": "propose_dead", "stones": ["A1"], "revision": 0},
            {"move": "accept_dead", "proposal_id": "unknown", "revision": 1},
            {"move": "D4", "revision": 1},
        ]
        for action in invalid:
            before = deepcopy(game.__dict__)
            with self.assertRaises(ValueError):
                game.step(action)
            self.assertEqual(game.__dict__, before)
        game.step({"move": "propose_dead", "stones": [], "revision": 1})
        before = deepcopy(game.__dict__)
        with self.assertRaises(ValueError):
            game.step({"move": "accept_dead", "proposal_id": "old", "revision": 1})
        self.assertEqual(game.__dict__, before)

    def test_whole_groups_preview_and_snapshot_isolation(self):
        game = AgreementGoGame()
        play(game, "A2", "A1", "B2", "B1", "pass", "pass")
        before = deepcopy(game.__dict__)
        preview = game.preview_score(["A1", "A1"])
        self.assertEqual(set(preview["dead"]), {"A1", "B1"})
        self.assertEqual(game.__dict__, before)
        game.step({"move": "propose_dead", "stones": ["A1"], "revision": 1})
        snapshot = game.snapshot()
        snapshot["negotiation"]["proposal"]["dead"].clear()
        self.assertEqual(len(game.proposal["dead"]), 2)

    def test_bridge_seats_staging_idempotency_and_text_spoof(self):
        game = settled()
        bridge = GoToolBridge(game)
        try:
            bridge.begin_turn("black", 9)
            with self.assertRaises(ValueError):
                bridge.call("white", "go_get_state", {})
            with self.assertRaisesRegex(ValueError, "No native"):
                bridge.finish_turn({"move": "tool"})
            bridge.begin_turn("black", 9)
            args = {"stones": ["A1"], "revision": 1}
            result = bridge.call("black", "go_propose_dead", args)
            self.assertIsNone(game.proposal)
            self.assertEqual(bridge.call("black", "go_propose_dead", args), result)
            with self.assertRaisesRegex(ValueError, "One decision"):
                bridge.call("black", "go_resume_play", {"revision": 1, "disputed_stone": None})
            game.step(bridge.finish_turn({"move": "tool"}))
            self.assertEqual(game.proposal["dead"], ["A1"])
            with self.assertRaises(ValueError):
                bridge.call("black", "go_get_state", {})
        finally:
            bridge.close()


class NativeToolTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_acp_tool_commentary_is_not_part_of_final_json(self):
        with tempfile.TemporaryDirectory() as temp:
            harness = DshHarness({}, Path(temp))
            harness.game_tools = {"name": "arena_go"}
            async def update(value):
                await harness.on_request({"method": "session/update", "params": {"update": value}})
            await update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Checking the proposal."}})
            await update({"sessionUpdate": "tool_call", "title": "mcp__arena_go__go_accept_dead"})
            await update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": '{"move":"tool"}'}})
            self.assertEqual(''.join(harness.messages), '{"move":"tool"}')
            self.assertIn("Checking", ''.join(harness.all_messages))

    async def test_stdio_mcp_discovery_and_actual_decision_roundtrip(self):
        game = settled()
        bridge = GoToolBridge(game)
        process = None
        try:
            import os
            connection = bridge.connection("black")
            process = await spawn([connection["command"], *connection["args"]],
                                  env={**os.environ, **connection["env"]}, stdin=asyncio.subprocess.PIPE,
                                  stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            async def rpc(method, params):
                process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})+'\n').encode())
                await process.stdin.drain()
                return json.loads(await asyncio.wait_for(process.stdout.readline(), 5))["result"]
            init = await rpc("initialize", {"protocolVersion": "2024-11-05"})
            self.assertEqual(init["serverInfo"]["name"], "arena_go")
            self.assertEqual(len((await rpc("tools/list", {}))["tools"]), 5)
            bridge.begin_turn("black", 9)
            result = await rpc("tools/call", {"name": "go_propose_dead", "arguments": {"stones": ["A1"], "revision": 1}})
            self.assertFalse(result["isError"])
            game.step(bridge.finish_turn({"move": "tool"}))
            self.assertEqual(game.proposal["dead"], ["A1"])
        finally:
            await stop(process)
            await asyncio.to_thread(bridge.close)

    async def test_acp_native_server_configuration_and_codex_resume_command(self):
        import sys
        bridge = GoToolBridge(settled())
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)
            harness = DshHarness({"command": [sys.executable, str(Path(__file__).with_name("fake_acp.py"))]}, path)
            try:
                harness.configure_game_tools(bridge.connection("black"))
                await harness.start()
                log = (path / "protocol.jsonl").read_text(encoding="utf-8")
                self.assertIn('"name": "arena_go"', log)
                self.assertNotIn(bridge.tokens["black"], log)
                codex = CodexHarness({"executable": "codex-test"}, path)
                codex.configure_game_tools(bridge.connection("white"))
                for session in (None, "exact-session"):
                    codex.metadata["session_id"] = session
                    command = codex.command(path / "schema.json", path / "answer.json")
                    self.assertIn('mcp_servers.arena_go.required=true', command)
                    self.assertNotIn("-c", command[command.index("exec")+1:])
                    self.assertNotIn(bridge.tokens["white"], ' '.join(command))
                    if session:
                        self.assertEqual(command[command.index("resume")+1], session)
            finally:
                await harness.close()
                await asyncio.to_thread(bridge.close)

    async def test_runner_records_native_decisions_and_does_not_count_disagreement_as_result(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("agreement_fixtures", Path(__file__).parents[1] / "scripts/prepare-go-agreement-checks.py")
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "fixtures"
            records = await fixtures.prepare(root, images=False)
            self.assertEqual(records[0]["result"]["reason"], "agreement")
            self.assertEqual(records[0]["result"]["dead"], ["A1"])
            self.assertEqual(records[1]["result"]["dead"], [])
            resumed = next(t for t in records[1]["turns"] if t["action"]["move"] == "resume_play")
            following = records[1]["turns"][resumed["ply"]]
            self.assertEqual(following["player"], "black")
            self.assertEqual(following["action"]["move"], "B1")
            for record in records:
                for turn in record["turns"]:
                    if turn["action"]["move"] in ("propose_dead", "accept_dead", "resume_play"):
                        self.assertTrue(any(c.get("result", {}).get("submitted") for c in turn["tool_calls"]))
                        self.assertEqual(turn["input"]["source_ply"], turn["ply"]-1)
