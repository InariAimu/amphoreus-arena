import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from amphoreus_arena.harnesses import CodexHarness, DshHarness, RandomHarness
from amphoreus_arena.runner import MatchRunner, parse_action
from amphoreus_arena.xiangqi import XiangqiGame


class RulesTests(unittest.TestCase):
    def test_position_and_native_coordinates(self):
        game = XiangqiGame()
        self.assertEqual(len(game.legal_actions()), 44)
        game.step({"move": "h3e3"})
        self.assertEqual(game.current_player, "black")
        self.assertIn("1C2C4", game.fen)
        self.assertIsNone(game.outcome())

    def test_illegal_move_does_not_change_position(self):
        game = XiangqiGame()
        initial = game.snapshot()
        with self.assertRaises(ValueError):
            game.step({"move": "a1a10"})
        self.assertEqual(initial, game.snapshot())

    def test_checkmate_is_loss_for_side_to_move(self):
        game = XiangqiGame("4k4/4R4/2N2R3/9/9/9/9/9/9/4K4 b - - 0 1")
        self.assertEqual(game.legal_actions(), [])
        self.assertEqual(game.outcome()["winner"], "red")

    def test_action_parser_accepts_json_not_prose(self):
        self.assertEqual(parse_action('```json\n{"move":"h3e3"}\n```')["move"], "h3e3")
        for invalid in ['I choose h3e3', '[]', '{"move":"h3e3"} commentary']:
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                parse_action(invalid)


class NullRenderer:
    def render(self, state, metadata, destination):
        destination.write_bytes(b"test frame")


class Player:
    def __init__(self, action='{"move":"a1a10"}', delay=0):
        self.metadata = {"kind": "test"}
        self.action, self.delay, self.closed = action, delay, False

    async def start(self):
        pass

    async def act(self, prompt, schema, ply, images=None):
        await asyncio.sleep(self.delay)
        return self.action

    async def close(self):
        self.closed = True


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_smoke_limit_is_stopped_not_draw(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            path = Path(temp)
            players = {side: RandomHarness({"seed": seed}, path) for side, seed in (("red", 42), ("black", 43))}
            result = await MatchRunner(XiangqiGame(), players, NullRenderer(), path, 4, 1).run()
            self.assertEqual(result["status"], "stopped")
            self.assertIsNone(result["result"])
            self.assertEqual(len(result["turns"]), 4)
            self.assertEqual(len(result["snapshots"]), 5)
            self.assertTrue(all(turn["explanation"] for turn in result["turns"]))
            self.assertNotIn("explanation", result["turns"][0]["action"])
            self.assertEqual(json.loads((path / "match.json").read_text(encoding="utf-8"))["status"], "stopped")

    async def test_invalid_move_retains_response_and_closes_players(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            path = Path(temp)
            players = {side: Player() for side in ("red", "black")}
            result = await MatchRunner(XiangqiGame(), players, NullRenderer(), path, 2, 1).run()
            self.assertEqual(result["error"]["phase"], "action_validation")
            self.assertEqual(result["turns"], [])
            self.assertTrue(all(p.closed for p in players.values()))
            self.assertIn('a1a10', (path / "events.jsonl").read_text(encoding="utf-8"))

    async def test_timeout_persists_failure(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            players = {side: Player(delay=1) for side in ("red", "black")}
            result = await MatchRunner(XiangqiGame(), players, NullRenderer(), Path(temp), 2, 0.02).run()
            self.assertEqual(result["error"]["type"], "TimeoutError")
            self.assertEqual(result["status"], "failed")
            self.assertTrue(all(p.closed for p in players.values()))

    async def test_terminal_position_finishes_without_prompt(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            game = XiangqiGame("4k4/4R4/2N2R3/9/9/9/9/9/9/4K4 b - - 0 1")
            players = {side: Player() for side in ("red", "black")}
            result = await MatchRunner(game, players, NullRenderer(), Path(temp), 2, 1).run()
            self.assertEqual(result["status"], "finished")
            self.assertEqual(result["result"]["winner"], "red")
            self.assertEqual(result["turns"], [])

    async def test_mate_on_last_allowed_ply_is_finished_not_stopped(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            game = XiangqiGame("4k4/3Rp4/2N2R3/9/9/9/9/9/9/4K4 w - - 0 1")
            self.assertIsNone(game.outcome())
            players = {side: Player('{"move":"d9e9"}') for side in ("red", "black")}
            result = await MatchRunner(game, players, NullRenderer(), Path(temp), 1, 1).run()
            self.assertEqual(result["status"], "finished")
            self.assertEqual(result["result"]["winner"], "red")
            self.assertEqual(len(result["turns"]), 1)

    async def test_until_end_has_no_ply_cap_and_finishes(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            game = XiangqiGame("4k4/3Rp4/2N2R3/9/9/9/9/9/9/4K4 w - - 0 1")
            players = {side: Player('{"move":"d9e9","explanation":"吃卒将死。"}') for side in ("red", "black")}
            result = await MatchRunner(game, players, NullRenderer(), Path(temp), None, 1).run()
            self.assertEqual(result["status"], "finished")
            self.assertIsNone(result["max_plies"])
            self.assertEqual(result["turns"][0]["explanation"], "吃卒将死。")


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_dsh_model_and_effort_use_advertised_options(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            harness = DshHarness({}, Path(temp))
            harness.update_config_options([
                {"id": "model", "currentValue": '["provider","model-a"]'},
                {"id": "reasoning_effort", "currentValue": "low"}])
            self.assertEqual(harness.metadata["model"], "model-a")
            self.assertEqual(harness.metadata["reasoning_effort"], "low")

    async def test_codex_reports_effective_session_model(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            path = Path(temp)
            harness = CodexHarness({"executable": "codex-test", "model": "configured"}, path)
            harness.metadata["session_id"] = "specific-id"
            harness.session_log = path / "session.jsonl"
            harness.session_log.write_text(json.dumps({"type": "turn_context", "payload": {
                "model": "actual-model", "effort": "low"}})+'\n', encoding="utf-8")
            harness.refresh_session_metadata()
            self.assertEqual(harness.metadata["model"], "actual-model")
            self.assertEqual(harness.metadata["model_source"], "session_turn_context")
    async def test_acp_updates_diagnostics_and_session_reuse(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            path = Path(temp)
            harness = DshHarness({"command": [sys.executable, str(Path(__file__).with_name('fake_acp.py'))]}, path)
            try:
                await asyncio.wait_for(harness.start(), 10)
                for ply in (1, 3):
                    raw = await asyncio.wait_for(harness.act("test", {}, ply), 2)
                    self.assertEqual(parse_action(raw), {"move": "h3e3"})
                    self.assertEqual(harness.metadata["usage"]["kind"], "context")
                    self.assertEqual(harness.metadata["usage"]["context_tokens"], 7074)
                self.assertEqual(harness.metadata["session_id"], "stable-test-session")
                self.assertIn("diagnostic", (path / "launcher.stdout.log").read_text())
            finally:
                await harness.close()
            self.assertIsNotNone(harness.process.returncode)

    async def test_codex_resumes_exact_thread(self):
        with tempfile.TemporaryDirectory(prefix="arena-test-") as temp:
            path = Path(temp)
            harness = CodexHarness({"executable": "codex-test"}, path)
            harness.metadata["session_id"] = "specific-id"
            command = harness.command(path / "schema.json", path / "answer.txt")
            self.assertIn("resume", command)
            self.assertEqual(command[command.index("resume") + 1], "specific-id")
            self.assertNotIn("--last", command)


if __name__ == "__main__":
    unittest.main()
