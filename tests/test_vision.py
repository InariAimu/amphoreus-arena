import asyncio
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from PIL import Image

from amphoreus_arena.harnesses import CodexHarness, DshHarness, dsh_command
from amphoreus_arena.vision import pieces_from_fen, render_observation
from amphoreus_arena.xiangqi import XiangqiGame

spec = importlib.util.spec_from_file_location("eval_vision", Path(__file__).parents[1] / "scripts/eval-vision.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class VisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_none_timeout_allows_completion_and_numeric_timeout_retains_failure(self):
        class DelayedHarness:
            def __init__(self):
                self.metadata = {"kind": "codex", "session_id": "fake"}
                self.closed = False
            async def start(self):
                pass
            async def act(self, prompt, schema, ply, images=None):
                await asyncio.sleep(0.05)
                return '{"move":"h3e3","explanation":"test"}'
            async def close(self):
                self.closed = True
        game = XiangqiGame()
        fixture = {"turn": 12, "initial_fen": game.initial_fen, "state": game.snapshot(),
                   "prompt": game.prompt(), "legal_moves": game.legal_actions(), "original_move": "h3e3"}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for repeat, timeout in ((1, None), (2, 0.001)):
                harness = DelayedHarness()
                def factory(config, directory):
                    directory.mkdir(parents=True)
                    return harness
                with patch.object(evaluation, "make_harness", factory):
                    result = await evaluation.run_case(root, "codex", {}, fixture, "text", repeat, timeout)
                self.assertTrue(harness.closed)
                self.assertEqual(result["status"], "completed" if timeout is None else "failed")
                if timeout is not None:
                    self.assertEqual(result["error"]["type"], "TimeoutError")

    async def test_acp_transmits_image_bytes_and_keeps_a_hash_in_log(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            picture = directory / "board.png"
            Image.new("RGB", (10, 10), "red").save(picture)
            harness = DshHarness({"command": [sys.executable, str(Path(__file__).with_name("fake_acp.py"))]}, directory)
            expected = hashlib.sha256(picture.read_bytes()).hexdigest()
            try:
                await asyncio.wait_for(harness.start(), 10)
                response = await asyncio.wait_for(harness.act("Read picture", {}, 1, [picture]), 3)
                self.assertEqual(json.loads(response)["image_sha256"], expected)
                frames = [json.loads(line)["frame"] for line in (directory / "protocol.jsonl").read_text().splitlines()]
                prompt = next(f for f in frames if f.get("method") == "session/prompt")
                self.assertEqual(prompt["params"]["prompt"][1]["data"]["sha256"], expected)
                harness.metadata["agent_capabilities"] = {}
                with self.assertRaisesRegex(RuntimeError, "did not advertise image"):
                    await harness.act("Read picture", {}, 2, [picture])
            finally:
                await harness.close()

    def test_codex_image_attached_on_initial_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            harness = CodexHarness({"executable": "codex-test"}, directory)
            picture = directory / "image with spaces.png"
            for session_id in (None, "exact-session"):
                harness.metadata["session_id"] = session_id
                command = harness.command(directory / "schema.json", directory / "answer.txt", [picture])
                self.assertEqual(command[command.index("--image") + 1], str(picture.resolve()))
                self.assertEqual(command[-1], "-")
                self.assertEqual("resume" in command, session_id is not None)

    def test_acp_patch_path_survives_changing_working_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            patch = Path(temp) / "vision patch.yml"
            patch.write_text("[]")
            command, _ = dsh_command({"command": ["dsh", "--profile", "acp"], "patches": [str(patch)]})
            self.assertEqual(command[-2:], ["--patch", str(patch.resolve())])

    def test_board_coordinates_and_exact_read_scoring(self):
        game = XiangqiGame()
        pieces = pieces_from_fen(game.fen)
        self.assertEqual(len(pieces), 32)
        self.assertEqual(pieces["a1"], "R")
        self.assertEqual(pieces["e10"], "k")
        self.assertEqual(pieces["h3"], "C")
        answer = {"to_move": "red", "pieces": [{"square": k, "piece": v} for k, v in pieces.items()]}
        score = evaluation.score_read(answer, {"state": game.snapshot()})
        self.assertTrue(score["exact_board"])
        self.assertEqual(score["correct_squares"], 90)
        answer["pieces"][0]["square"] = "a9"
        score = evaluation.score_read(answer, {"state": game.snapshot()})
        self.assertFalse(score["exact_board"])
        self.assertEqual(score["correct_squares"], 88)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "board.png"
            render_observation(game.snapshot(), path)
            with Image.open(path) as image:
                self.assertEqual(image.size, (900, 1000))

    def test_move_scoring_checks_opponent_mate_and_rejects_illegal_input(self):
        # From the original endgame, no history is needed to establish this one-ply tactic.
        game = XiangqiGame("3aka3/9/9/7RC/2n6/9/P1P1P3P/4BA3/2c2K3/9 b - - 6 29")
        fixture = {"initial_fen": game.initial_fen, "state": game.snapshot(), "legal_moves": game.legal_actions(),
                   "turn": 58, "original_move": "d10e9"}
        bad = evaluation.score_move({"move": "d10e9"}, fixture)
        self.assertTrue(bad["legal"])
        self.assertEqual(bad["opponent_mate_in_one"], ["i7i10"])
        safe = evaluation.score_move({"move": "f10e9"}, fixture)
        self.assertTrue(safe["legal"])
        self.assertFalse(safe["allows_mate_in_one"])
        illegal = evaluation.score_move({"move": "a1a10"}, fixture)
        self.assertEqual(illegal, {"legal": False, "same_as_original": False})


if __name__ == "__main__":
    unittest.main()
