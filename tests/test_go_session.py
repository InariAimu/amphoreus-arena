import importlib.util
import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

from amphoreus_arena.go import GoGame, COLUMNS
from amphoreus_arena.go_agreement import AgreementGoGame
from amphoreus_arena.go_rendering import render_go_observation
from amphoreus_arena.go_session import session_input, session_schema
from amphoreus_arena.harnesses import DshHarness, CodexHarness
from amphoreus_arena.runner import MatchRunner
from amphoreus_arena import cli
from test_arena import NullRenderer, Player


def append(game, turns, snapshots, action, seconds=1):
    if isinstance(action, str):
        action = {"move": action}
    turn = {"ply": len(turns) + 1, "player": game.current_player,
            "action": action, "elapsed_seconds": seconds}
    game.step(action)
    turns.append(turn)
    snapshots.append({"ply": len(turns), "state": game.snapshot()})


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), Path(__file__).parents[1] / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GoSessionTests(unittest.TestCase):
    def test_concise_initial_input_keeps_necessary_rules_and_tool_boundary(self):
        for game in (GoGame(board_size=19), AgreementGoGame()):
            prompt, _, details = session_input(game, [], [{"ply": 0, "state": game.snapshot()}])
            instructions, data = prompt.split("\n", 1)
            payload = json.loads(data)
            self.assertEqual(details["input_version"], 2)
            self.assertEqual(payload["board"], game.snapshot()["board"])
            self.assertNotIn("legal_moves", payload)
            self.assertNotIn("moves", payload)
            self.assertNotIn("rules_reference", payload)
            self.assertEqual(payload["illegal_empty"], [])
            self.assertLess(len(prompt), 2000)
            for needed in ("19x19 Go", "positional superko", "komi 7.5", "tools and helper scripts", "Go-playing software/engines", "Go AIs", "120s", '"move"'):
                self.assertIn(needed, instructions)
            self.assertNotIn("Only the arena_go", instructions)
            self.assertIn("dead-stone agreement" if getattr(game, "native_tools", False) else "score the board", instructions)

    def test_v1_payload_remains_available_for_historical_audits(self):
        game = AgreementGoGame()
        prompt, _, details = session_input(game, [], [{"ply": 0, "state": game.snapshot()}], version=1)
        self.assertEqual(json.loads(prompt.split("\n", 1)[1]), game.observation())
        self.assertNotIn("input_version", details)
        with self.assertRaisesRegex(ValueError, "input version"):
            session_input(game, [], [], version=999)

    def test_initial_agreement_sends_proposal_context_without_coordinate_list(self):
        game = AgreementGoGame()
        for move in ("A2", "A1", "B2", "T19", "C1", "T18", "pass", "pass"):
            game.step({"move": move})
        prompt, schema, _ = session_input(game, [], [{"ply": 0, "state": game.snapshot()}])
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(payload["phase"], "agreement")
        self.assertEqual(payload["negotiation"]["revision"], 1)
        self.assertNotIn("illegal_empty", payload)
        self.assertNotIn("legal_moves", payload)
        self.assertEqual(schema["properties"]["move"]["enum"], ["tool", "resign"])

    def test_each_seat_reconstructs_board_using_only_initial_input_and_deltas(self):
        game, turns = GoGame(), []
        snapshots, memory = [{"ply": 0, "state": game.snapshot()}], {}
        moves = ["A2", "A1", "B2", "J9", "C1", "J8", "B1", "J7", "H8", "H7"]
        saw_capture = False
        for move in moves:
            prompt, schema, details = session_input(game, turns, snapshots)
            payload = json.loads(prompt.split("\n", 1)[1])
            side = game.current_player
            if side not in memory:
                self.assertEqual(details["kind"], "initial")
                memory[side] = [list(row) for row in payload["board"]]
            else:
                self.assertEqual(details["kind"], "delta")
                for absent in ("board", "moves", "legal_moves", "rules", "komi"):
                    self.assertNotIn(absent, payload)
                self.assertLess(len(prompt), 600)
                changes = [(payload["your_last_turn"], side)] + [(c, "white" if side == "black" else "black") for c in payload["opponent_updates"]]
                for change, player in changes:
                    action = change["action"]["move"]
                    if action not in ("pass", "resign"):
                        x, y = COLUMNS.index(action[0]), 9 - int(action[1:])
                        memory[side][y][x] = "B" if player == "black" else "W"
                    for coord in change.get("captured", []):
                        memory[side][9 - int(coord[1:])][COLUMNS.index(coord[0])] = "."
                        saw_capture = True
                self.assertTrue(all(c["ply"] > payload["your_last_turn"]["ply"] for c in payload["opponent_updates"]))
            self.assertEqual(["".join(row) for row in memory[side]], game.snapshot()["board"])
            self.assertRegex(move, schema["properties"]["move"]["pattern"])
            append(game, turns, snapshots, move)
        self.assertTrue(saw_capture)

    def test_superko_point_is_supplied_without_full_legal_list(self):
        game, turns = GoGame(), []
        snapshots = [{"ply": 0, "state": game.snapshot()}]
        for move in ("A2", "B2", "C2", "A3", "B1", "C3", "J9", "B4", "B3"):
            append(game, turns, snapshots, move)
        prompt, _, _ = session_input(game, turns, snapshots)
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertIn("B2", payload["illegal_empty"])
        self.assertNotIn("legal_moves", payload)

    def test_timing_feedback_warns_only_above_120_seconds(self):
        for seconds, warning in ((119.9, False), (120, False), (120.1, True), (782.7, True)):
            game, turns = GoGame(), []
            snapshots = [{"ply": 0, "state": game.snapshot()}]
            append(game, turns, snapshots, "A2", seconds)
            append(game, turns, snapshots, "A1", 900)
            prompt, _, _ = session_input(game, turns, snapshots)
            own = json.loads(prompt.split("\n", 1)[1])["your_last_turn"]
            self.assertEqual(own["elapsed_seconds"], seconds)
            self.assertEqual("warning" in own, warning)

    def test_agreement_and_consecutive_same_seat_turns_keep_revision_and_proposal(self):
        game, turns = AgreementGoGame(), []
        snapshots = [{"ply": 0, "state": game.snapshot()}]
        for move in ("A2", "A1", "B2", "T19", "C1", "T18", "pass", "pass"):
            append(game, turns, snapshots, move)
        payload = json.loads(session_input(game, turns, snapshots)[0].split("\n", 1)[1])
        self.assertEqual(payload["phase"], "agreement")
        self.assertEqual(payload["negotiation"]["revision"], 1)
        append(game, turns, snapshots, {"move": "resume_play", "revision": 1, "disputed_stone": None})
        prompt, _, details = session_input(game, turns, snapshots)
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(game.current_player, "black")
        self.assertEqual(payload["opponent_updates"], [])
        self.assertEqual(details["base_ply"], 8)
        self.assertEqual(payload["phase"], "play")
        for move in ("B1", "pass", "pass"):
            append(game, turns, snapshots, move)
        append(game, turns, snapshots, {"move": "propose_dead", "revision": 2, "stones": []})
        payload = json.loads(session_input(game, turns, snapshots)[0].split("\n", 1)[1])
        self.assertEqual(payload["negotiation"]["proposal"], game.proposal)
        self.assertEqual(session_schema(game)["properties"]["move"]["enum"], ["tool", "resign"])

    def test_compact_schema_boundaries_for_each_board_size(self):
        for size, last, bad in ((9, "J9", "K9"), (13, "N13", "N14"), (19, "T19", "T20")):
            pattern = session_schema(GoGame(board_size=size))["properties"]["move"]["pattern"]
            for valid in ("A1", last, "pass", "resign"):
                self.assertIsNotNone(re.fullmatch(pattern, valid))
            for invalid in (bad, "I1", "A0", "A01", "a1", "tool"):
                self.assertIsNone(re.fullmatch(pattern, invalid))


class GoSessionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_auxiliary_tools_get_single_use_permission_and_writable_match_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            options = [{"kind": "allow_always", "optionId": "forever"},
                       {"kind": "allow_once", "optionId": "once"}]
            for enabled in (False, True):
                harness = DshHarness({"allow_auxiliary_tools": enabled}, root / str(enabled))
                replies = []
                async def capture(frame):
                    replies.append(frame)
                harness.send = capture
                await harness.on_request({"id": 12, "method": "session/request_permission", "params": {"options": options}})
                expected = {"outcome": "selected", "optionId": "once"} if enabled else {"outcome": "cancelled"}
                self.assertEqual(replies[-1]["result"]["outcome"], expected)
                if enabled:
                    await harness.on_request({"id": 13, "method": "session/request_permission", "params": {"options": options[:1]}})
                    self.assertEqual(replies[-1]["result"]["outcome"], {"outcome": "cancelled"})
                codex = CodexHarness({"executable": "codex-test", "allow_auxiliary_tools": enabled}, root / ('codex-' + str(enabled)))
                for session in (None, "same-session"):
                    codex.metadata["session_id"] = session
                    command = codex.command(root / "schema.json", root / "answer.txt")
                    self.assertIn('sandbox_mode="workspace-write"' if enabled else 'sandbox_mode="read-only"', command)
                    self.assertNotIn("-c", command[command.index("exec") + 1:])

    async def test_invalid_auxiliary_tool_flag_cannot_enable_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            for kind in (DshHarness, CodexHarness):
                for value in ("false", "true", 1, None):
                    with self.assertRaisesRegex(ValueError, "must be a boolean"):
                        kind({"executable": "codex-test", "allow_auxiliary_tools": value}, Path(temp))

    async def test_cli_defaults_native_go_to_incremental_and_allows_full_override(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "config.json"
            config.write_text(json.dumps({"game": "go", "observation_mode": "text",
                                          "players": {"black": {"kind": "dsh"}, "white": {"kind": "codex"}}}), encoding="utf-8")
            command = [sys.executable, str(Path(__file__).with_name("fake_acp.py")), "--go-session"]
            for mode in (None, "full"):
                output = root / (mode or "default")
                args = argparse.Namespace(config=str(config), output=str(output), game=None, until_end=False,
                                          max_plies=4, timeout=None, no_timeout=True, observation_mode=None,
                                          prompt_mode=mode, random=False)
                with patch.object(cli, "make_harness", side_effect=lambda _, directory: DshHarness({"command": command}, directory)):
                    self.assertEqual(await cli.run(args), 0)
                record = json.loads((output / "match.json").read_text(encoding="utf-8"))
                self.assertEqual(record["prompt_mode"], mode or "incremental")
                self.assertEqual(record["turns"][2]["input"].get("kind"), None if mode == "full" else "delta")

    async def test_cli_rejects_incremental_xiangqi_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "match"
            args = argparse.Namespace(config=None, output=str(output), game="xiangqi", until_end=False,
                                      max_plies=4, timeout=None, no_timeout=True, observation_mode="text",
                                      prompt_mode="incremental", random=False)
            with patch.object(cli, "make_harness") as factory:
                with self.assertRaisesRegex(ValueError, "persistent dsh/codex Go"):
                    await cli.run(args)
                factory.assert_not_called()
            self.assertFalse(output.exists())

    async def test_lost_session_stops_before_sending_a_delta(self):
        with tempfile.TemporaryDirectory() as temp:
            class SessionPlayer(Player):
                def __init__(self, side):
                    super().__init__()
                    self.metadata = {"kind": "dsh", "session_id": "original"}
                    self.side = side

                async def act(self, prompt, schema, ply, images=None):
                    if self.side == "white":
                        players["black"].metadata["session_id"] = "replacement"
                    return json.dumps({"move": "A1" if self.side == "black" else "B1"})

            players = {side: SessionPlayer(side) for side in ("black", "white")}
            record = await MatchRunner(GoGame(), players, NullRenderer(), Path(temp), 3, None,
                                       prompt_mode="incremental").run()
            self.assertEqual(len(record["turns"]), 2)
            self.assertEqual(record["error"]["phase"], "observation")
            self.assertIn("changed harness session", record["error"]["message"])

    async def test_acp_reuses_both_sessions_and_sends_images_only_on_first_input(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            command = [sys.executable, str(Path(__file__).with_name("fake_acp.py")), "--go-session"]
            players = {side: DshHarness({"command": command}, directory / side) for side in ("black", "white")}
            runner = MatchRunner(GoGame(), players, NullRenderer(), directory, 8, None,
                                 observation_mode="text_image", observation_renderer=render_go_observation,
                                 prompt_mode="incremental")
            runner.record["game_options"] = {"board_size": 9, "komi": 7.5, "rules": runner.game.rules}
            record = await runner.run()
            self.assertEqual(record["status"], "stopped", record["error"])
            for turn in record["turns"]:
                first = turn["ply"] <= 2
                self.assertEqual(turn["input"]["kind"], "initial" if first else "delta")
                self.assertEqual(len(turn["input"]["images"]), 1 if first else 0)
                self.assertEqual(turn["input"]["mode"], "text_image" if first else "text")
            for side in players:
                frames = [json.loads(line)["frame"] for line in (directory / side / "protocol.jsonl").read_text(encoding="utf-8").splitlines()]
                self.assertEqual(sum(f.get("method") == "session/new" for f in frames), 1)
                prompts = [f["params"]["prompt"] for f in frames if f.get("method") == "session/prompt"]
                self.assertEqual([sum(c["type"] == "image" for c in p) for p in prompts], [1, 0, 0, 0])
            self.assertTrue(load_script("audit-go-match").audit(directory)["passed"])

    async def test_incremental_schema_does_not_allow_occupied_move(self):
        with tempfile.TemporaryDirectory() as temp:
            players = {"black": Player('{"move":"A1"}'), "white": Player('{"move":"B1"}')}
            record = await MatchRunner(GoGame(), players, NullRenderer(), Path(temp), 3, None,
                                       prompt_mode="incremental").run()
            self.assertEqual(len(record["turns"]), 2)
            self.assertEqual(record["error"]["phase"], "action_validation")
            self.assertEqual(record["error"]["input"]["kind"], "delta")

    async def test_native_mcp_agreement_dispute_and_audit_with_incremental_inputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "fixtures"
            records = await load_script("prepare-go-agreement-checks").prepare(root, images=False, prompt_mode="incremental")
            for folder, record in zip(("agreement", "dispute"), records):
                self.assertEqual(record["status"], "finished")
                self.assertTrue(load_script("audit-go-match").audit(root / folder)["passed"])
