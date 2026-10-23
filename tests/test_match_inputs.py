import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amphoreus_arena import cli
from amphoreus_arena.harnesses import DshHarness, dsh_command
from amphoreus_arena.runner import MatchRunner
from amphoreus_arena.vision import render_observation
from amphoreus_arena.xiangqi import XiangqiGame
from test_arena import NullRenderer, Player


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class MatchInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_board_reaches_both_acp_sessions_and_is_recorded(self):
        with tempfile.TemporaryDirectory(prefix="arena-input-") as temp:
            directory = Path(temp)
            command = [sys.executable, str(Path(__file__).with_name("fake_acp.py")), "--arena-moves"]
            players = {side: DshHarness({"command": command}, directory / side) for side in ("red", "black")}
            result = await MatchRunner(XiangqiGame(), players, NullRenderer(), directory, 4, None,
                                       observation_mode="text_image", observation_renderer=render_observation).run()
            self.assertEqual(result["status"], "stopped", result["error"])
            game = XiangqiGame()
            digests = set()
            for turn in result["turns"]:
                ply, side, observation = turn["ply"], turn["player"], turn["input"]
                manifest = read_json(directory / f"observations/{ply:04d}.json")
                self.assertEqual(manifest["state"], game.snapshot())
                self.assertEqual(manifest["action_schema"], game.action_schema())
                self.assertEqual(observation["source_ply"], ply - 1)
                self.assertEqual(result["snapshots"][ply - 1]["state"], manifest["state"])
                prompt_path = directory / observation["prompt"]["path"]
                prompt = prompt_path.read_text(encoding="utf-8")
                self.assertEqual(json.loads(prompt.split("\n", 1)[1]), game.observation())
                self.assertTrue(prompt.endswith(game.prompt()))
                self.assertEqual(hashlib.sha256(prompt_path.read_bytes()).hexdigest(), observation["prompt"]["sha256"])
                self.assertEqual((directory / side / f"{ply:04d}.prompt.txt").read_text(encoding="utf-8"), prompt)
                image = observation["images"][0]
                # Independent pre-move reconstruction catches stale / post-move attachments.
                render_observation(game.snapshot(), directory / "expected.png")
                self.assertEqual((directory / image["path"]).read_bytes(), (directory / "expected.png").read_bytes())
                self.assertEqual(hashlib.sha256((directory / image["path"]).read_bytes()).hexdigest(), image["sha256"])
                self.assertEqual(turn["explanation"], image["sha256"])  # Echoed from bytes decoded by the ACP peer.
                self.assertEqual(read_json(directory / side / f"{ply:04d}.images.json")[0]["sha256"], image["sha256"])
                digests.add(image["sha256"])
                game.step(turn["action"])
            self.assertEqual(len(digests), 4)
            for side in players:
                frames = [json.loads(line)["frame"] for line in (directory / side / "protocol.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(sum(f.get("method") == "session/new" for f in frames), 1)
                self.assertEqual(sum(f.get("method") == "session/prompt" for f in frames), 2)
                self.assertIsNotNone(players[side].process.returncode)

    async def test_text_mode_preserves_prompt_and_sends_no_images(self):
        calls = []
        class TextPlayer(Player):
            async def act(self, prompt, schema, ply, images=None):
                calls.append((prompt, images))
                return '{"move":"h3e3","explanation":"test"}'
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            game = XiangqiGame()
            original = game.prompt()
            players = {side: TextPlayer() for side in ("red", "black")}
            result = await MatchRunner(game, players, NullRenderer(), directory, 1, None,
                                       observation_mode="text", observation_renderer=render_observation).run()
            self.assertEqual(result["status"], "stopped")
            self.assertEqual(calls, [(original, None)])
            self.assertEqual(result["turns"][0]["input"]["images"], [])
            self.assertEqual(list((directory / "observations").glob("*.png")), [])

    async def test_failed_turn_retains_input_and_active_turn_is_saved_before_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            seen = []
            class BadPlayer(Player):
                async def act(self, prompt, schema, ply, images=None):
                    seen.append(read_json(directory / "match.json")["active_turn"])
                    return self.action
            players = {side: BadPlayer() for side in ("red", "black")}
            result = await MatchRunner(XiangqiGame(), players, NullRenderer(), directory, 1, None,
                                       observation_mode="text_image", observation_renderer=render_observation).run()
            self.assertEqual(result["error"]["phase"], "action_validation")
            self.assertEqual(result["error"]["input"], seen[0]["input"])
            self.assertTrue((directory / seen[0]["input"]["images"][0]["path"]).is_file())
            self.assertEqual(result["turns"], [])
            self.assertTrue(all(player.closed for player in players.values()))

    async def test_unlimited_inference_is_independent_of_startup_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            players = {side: Player('{"move":"h3e3"}', delay=0.05) for side in ("red", "black")}
            result = await MatchRunner(XiangqiGame(), players, NullRenderer(), directory, 1, None,
                                       startup_timeout=0.01).run()
            self.assertEqual(result["status"], "stopped")
            self.assertGreater(result["turns"][0]["elapsed_seconds"], result["startup_timeout"])
            self.assertIsNone(result["turn_timeout"])

    async def test_startup_still_has_its_own_timeout(self):
        class SlowStartup(Player):
            async def start(self):
                await asyncio.sleep(1)
        with tempfile.TemporaryDirectory() as temp:
            players = {side: SlowStartup() for side in ("red", "black")}
            result = await MatchRunner(XiangqiGame(), players, NullRenderer(), Path(temp), 1, None,
                                       startup_timeout=0.01).run()
            self.assertEqual(result["error"]["phase"], "startup")
            self.assertEqual(result["error"]["message"], "Timed out after 0.01s")
            self.assertTrue(all(player.closed for player in players.values()))


class InputConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_default_config_selects_vision_before_acp_initialize(self):
        with patch("amphoreus_arena.cli.Path.exists", return_value=False):
            config = cli.load_config(None)
        self.assertEqual(config["observation_mode"], "text_image")
        self.assertIsNone(config["turn_timeout"])
        command, _ = dsh_command({**config["red"], "command": ["dsh", "--profile", "acp"]})
        patch_path = Path(command[command.index("--patch") + 1])
        self.assertTrue(patch_path.is_absolute())
        self.assertIn("model: deepseek-flash", patch_path.read_text())
        self.assertEqual(json.loads(config["red"]["model"]), ["deepseek-official", "deepseek-flash"])

    async def test_cli_overrides_mode_and_both_timeout_choices(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            config.write_text(json.dumps({"observation_mode": "text_image", "turn_timeout": 1}), encoding="utf-8")
            for index, (no_timeout, timeout, expected) in enumerate(((True, None, None), (False, 3, 3))):
                output = root / str(index)
                args = argparse.Namespace(config=str(config), until_end=False, max_plies=1, timeout=timeout,
                                          no_timeout=no_timeout, observation_mode="text", random=True, output=str(output))
                code = await cli.run(args)
                self.assertEqual(code, 0)
                result = read_json(output / "match.json")
                self.assertEqual(result["turn_timeout"], expected)
                self.assertEqual(result["observation_mode"], "text")
                self.assertEqual(result["turns"][0]["input"]["images"], [])


if __name__ == "__main__":
    unittest.main()
