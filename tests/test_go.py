import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from amphoreus_arena import cli
from amphoreus_arena.games import configure_game, get_game_plugin, register_game, registered_games
from amphoreus_arena.go import GoGame, RULESET
from amphoreus_arena.go_rendering import GoRenderer, render_go_observation
from amphoreus_arena.rendering import write_replay
from amphoreus_arena.runner import MatchRunner
from test_arena import NullRenderer


def play(game, *moves):
    for move in moves:
        game.step({"move": move})
    return game


def position(black=(), white=(), komi=7.5, board_size=9):
    """Explicit local rule fixture, with the given position as history root."""
    game = GoGame(board_size=board_size, komi=komi)
    board = list(game._board)
    for moves, stone in ((black, "B"), (white, "W")):
        for move in moves:
            board[game._point(move)] = stone
    game._board = tuple(board)
    game._seen = {game._board}
    return game


class GoRulesTests(unittest.TestCase):
    def test_initial_board_and_coordinate_orientation(self):
        game = GoGame()
        self.assertEqual(len(game.legal_actions()), 83)
        self.assertEqual(game.current_player, "black")
        play(game, "A1", "J9")
        self.assertEqual(game.snapshot()["board"][8][0], "B")
        self.assertEqual(game.snapshot()["board"][0][8], "W")
        self.assertEqual(game.current_player, "black")
        self.assertEqual(game.action_schema()["properties"]["move"]["enum"], game.legal_actions())

    def test_invalid_actions_are_atomic(self):
        game = play(GoGame(), "A1")
        for move in ("A1", "I1", "a2", "A0", "A10", "B01", "B１", "", None, 42, "PASS"):
            before = deepcopy(game.__dict__)
            with self.subTest(move=move), self.assertRaises(ValueError):
                game.step({"move": move})
            self.assertEqual(game.__dict__, before)

    def test_connected_group_capture_is_counted_once(self):
        game = play(GoGame(), "A2", "A1", "H9", "B1", "B2", "J9", "C1")
        state = game.snapshot()
        self.assertEqual(state["board"][8][:3], "..B")
        self.assertEqual(state["last_captures"], ["A1", "B1"])
        self.assertEqual(state["captures"], {"black": 2, "white": 0})

    def test_one_move_captures_four_separate_groups_before_suicide_check(self):
        game = position(black=("A1", "A3", "C1", "C3", "D2", "B4"), white=("A2", "B1", "C2", "B3"))
        play(game, "B2")
        self.assertEqual(game.captures["black"], 4)
        self.assertEqual(set(game.last_captures), {"A2", "B1", "C2", "B3"})
        self.assertEqual(game._board[game._point("B2")], "B")

    def test_suicide_and_shared_liberties(self):
        game = play(GoGame(), "J9", "A2", "H9", "B1")
        before = deepcopy(game.__dict__)
        with self.assertRaisesRegex(ValueError, "suicide"):
            play(game, "A1")
        self.assertEqual(game.__dict__, before)
        self.assertNotIn("A1", game.legal_actions())
        # A new stone without an empty immediate neighbor may join a live group.
        game = position(black=("A2",), white=("B1",))
        play(game, "A1")
        self.assertEqual(game._board[game._point("A1")], "B")

    def test_ko_rejects_immediate_recapture_then_allows_changed_board(self):
        game = play(GoGame(), "A2", "B2", "C2", "A3", "B1", "C3", "J9", "B4", "B3")
        self.assertEqual(game.last_captures, ["B2"])
        before = deepcopy(game.__dict__)
        with self.assertRaisesRegex(ValueError, "superko"):
            play(game, "B2")
        self.assertEqual(game.__dict__, before)
        self.assertNotIn("B2", game.legal_actions())
        play(game, "J1", "H1", "B2")
        self.assertEqual(game.last_captures, ["B3"])

    def test_superko_checks_older_history_not_just_previous_position(self):
        # Three independent kos: five captures are legal, the sixth would restore
        # the starting board. A check against only the preceding board misses this.
        game = position(black=("A2", "C2", "B1", "F2", "E3", "G3", "F4", "A6", "C6", "B5"),
                        white=("B2", "A3", "C3", "B4", "E2", "G2", "F1", "B6", "A7", "C7", "B8"))
        play(game, "B3", "F3", "B7", "B2", "F2")
        before = deepcopy(game.__dict__)
        with self.assertRaisesRegex(ValueError, "superko"):
            play(game, "B6")
        self.assertEqual(game.__dict__, before)
        self.assertNotIn("B6", game.legal_actions())

    def test_pass_is_superko_exempt_and_play_resets_pass_count(self):
        game = play(GoGame(), "pass")
        self.assertEqual(game.consecutive_passes, 1)
        self.assertIsNone(game.outcome())
        play(game, "D4")
        self.assertEqual(game.consecutive_passes, 0)
        play(game, "pass", "pass")
        self.assertEqual(game.outcome()["reason"], "two_passes")
        self.assertEqual(game.outcome()["winner"], "white")
        self.assertEqual(game.legal_actions(), [])
        before = deepcopy(game.__dict__)
        with self.assertRaisesRegex(ValueError, "finished"):
            play(game, "D5")
        self.assertEqual(game.__dict__, before)

    def test_empty_board_komi_and_draw(self):
        for komi, winner in ((7.5, "white"), (0, None), (-.5, "black")):
            game = play(GoGame(komi=komi), "pass", "pass")
            result = game.outcome()
            self.assertEqual(result["winner"], winner)
            self.assertEqual(result["score"]["neutral"], 81)
            self.assertEqual(result["score"]["black"], 0)
            self.assertEqual(result["score"]["white"], komi)

    def test_area_counts_stones_territory_neutral_and_komi_not_prisoners(self):
        game = GoGame()
        rows = ["BBBB.WWWW"] * 9
        rows[0], rows[-1] = ".BBB.WWWW", "BBBB.WWW."
        game._board = tuple("".join(rows))
        game.captures = {"black": 15, "white": 7}
        score = game.score()
        self.assertEqual(score, {"black": 36, "white": 43.5, "stones": {"black": 35, "white": 35},
                                 "territory": {"black": 1, "white": 1}, "neutral": 9, "komi": 7.5})
        play(game, "pass", "pass")
        self.assertEqual(game.outcome()["margin"], 7.5)

    def test_remaining_stones_are_not_automatically_declared_dead(self):
        game = GoGame(komi=0)
        rows = ["BBBBBBBBB"] * 9
        rows[4] = "BBBBW.BBB"
        game._board = tuple("".join(rows))
        play(game, "pass", "pass")
        self.assertEqual(game.outcome()["score"]["white"], 1)
        self.assertEqual(game.outcome()["score"]["neutral"], 1)

    def test_resignation_both_colors_and_snapshot_isolation(self):
        for prefix, winner in (([], "white"), (["D4"], "black")):
            game = play(GoGame(), *prefix, "resign")
            self.assertEqual(game.outcome()["winner"], winner)
            state = game.snapshot()
            state["moves"].append("A1")
            state["captures"]["black"] = 500
            state["result"]["winner"] = "changed"
            self.assertEqual(game.outcome()["winner"], winner)
            self.assertEqual(game.captures["black"], 0)
            self.assertEqual(len(game.moves), len(prefix) + 1)

    def test_invalid_rules_options(self):
        for options in ({"board_size": 8}, {"board_size": True}, {"komi": float("nan")},
                        {"komi": float("inf")}, {"komi": True}, {"komi": 6.1}, {"rules": "japanese"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                GoGame(**options)
        for size in (13, 19):
            game = GoGame(board_size=size)
            self.assertEqual(len(game.legal_actions()), size * size + 2)


class Go19Tests(unittest.TestCase):
    def test_full_board_coordinates_and_legal_schema(self):
        game = GoGame(board_size=19)
        self.assertEqual(len(game.legal_actions()), 363)
        play(game, "A1", "T19", "T1", "A19", "J10", "K10")
        state = game.snapshot()
        self.assertEqual(len(state["board"]), 19)
        self.assertTrue(all(len(row) == 19 for row in state["board"]))
        self.assertEqual(state["board"][0][0], "W")
        self.assertEqual(state["board"][0][18], "W")
        self.assertEqual(state["board"][18][0], "B")
        self.assertEqual(state["board"][18][18], "B")
        self.assertEqual(state["board"][9][8:10], "BW")
        self.assertEqual(len(game.action_schema()["properties"]["move"]["enum"]), 357)
        self.assertIn("19x19", game.prompt())
        for move in ("I10", "U1", "T20", "A0"):
            with self.subTest(move=move), self.assertRaises(ValueError):
                play(game, move)

    def test_capture_on_opposite_corner_and_board_does_not_wrap(self):
        game = position(black=("S18", "R19"), white=("S19", "T19"), board_size=19)
        play(game, "T18")
        self.assertEqual(set(game.last_captures), {"S19", "T19"})
        self.assertEqual(game.captures["black"], 2)
        self.assertEqual(game.snapshot()["board"][0][-2:], "..")
        # A stone at the end of one row must not connect to the next row's start.
        game = position(black=("T19",), white=("B18", "A17", "A19"), board_size=19)
        with self.assertRaisesRegex(ValueError, "suicide"):
            play(game, "A18")

    def test_full_size_scoring_and_observation(self):
        game = GoGame(board_size=19)
        rows = ["BBBBBBBBB.WWWWWWWWW"] * 19
        rows[0], rows[-1] = ".BBBBBBBB.WWWWWWWWW", "BBBBBBBBB.WWWWWWWW."
        game._board = tuple("".join(rows))
        score = game.score()
        self.assertEqual(score["stones"], {"black": 170, "white": 170})
        self.assertEqual(score["territory"], {"black": 1, "white": 1})
        self.assertEqual(score["neutral"], 19)
        self.assertEqual((score["black"], score["white"]), (171, 178.5))
        with tempfile.TemporaryDirectory() as temp:
            picture = Path(temp) / "board.png"
            render_go_observation(game.snapshot(), picture)
            with Image.open(picture) as image:
                self.assertEqual(image.size, (900, 1000))
                # Opposite stone colors are distinguishable away from highlights.
                self.assertNotEqual(image.getpixel((130, 135)), image.getpixel((770, 135)))


class GameConfigTests(unittest.TestCase):
    def test_registry_and_legacy_xiangqi(self):
        self.assertIn("go", registered_games())
        self.assertIn("xiangqi", registered_games())
        config = {"red": {"kind": "random"}, "black": {"kind": "random"}}
        original = deepcopy(config)
        plugin, game, options, players, presentation = configure_game(config)
        self.assertEqual(plugin.player_ids, ("red", "black"))
        self.assertEqual(len(game.legal_actions()), 44)
        self.assertEqual(list(players), ["red", "black"])
        self.assertEqual(config, original)
        self.assertEqual(presentation["players"]["red"]["side_name"], "红方")
        with self.assertRaises(ValueError):
            register_game(plugin)
        with self.assertRaises(ValueError):
            get_game_plugin("missing")

    def test_go_config_preserves_seat_order_options_and_display_override(self):
        config = {"game": "go", "game_options": {"komi": 6.5},
                  "players": {"white": {"kind": "random"}, "black": {"kind": "random"}},
                  "presentation": {"players": {"black": {"name": "先手", "side_name": "执黑"}}}}
        plugin, game, options, players, presentation = configure_game(config)
        self.assertEqual(list(players), ["black", "white"])
        self.assertEqual(options, {"board_size": 9, "komi": 6.5, "rules": RULESET})
        self.assertEqual(presentation["players"]["black"]["side_name"], "执黑")
        self.assertEqual(presentation["players"]["white"]["side_name"], "白方")

    def test_bad_config_fails_before_creating_output(self):
        bad = ({"game": "missing"}, {"game": "go", "initial_fen": "bad"},
               {"game": "go", "game_options": {"size": 9}}, {"game": "go", "game_options": []},
               {"game": "go", "players": {"red": {"kind": "random"}, "black": {"kind": "random"}}},
               {"players": {"red": {"kind": "random"}, "black": {"kind": "unknown"}}},
               {"players": {"red": {"kind": "random"}, "black": {"kind": "random"}}, "red": {}},
               {"game_options": {"initial_fen": "different"}, "initial_fen": "conflict"})
        for config in bad:
            with self.subTest(config=config), self.assertRaises(ValueError):
                configure_game(config)

    def test_registered_scripts_and_unknown_game_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for game_id, script in (("go", "go.js"), ("xiangqi", "xiangqi.js"), ("unknown", None)):
                write_replay(directory, {"game_id": game_id, "unsafe": "</script><script>alert(1)</script>"})
                html = (directory / "broadcast.html").read_text(encoding="utf-8")
                self.assertNotIn("__GAME_SCRIPTS__", html)
                self.assertNotIn("</script><script>alert", html)
                if script:
                    self.assertIn(f'src="web/{script}"', html)
                else:
                    self.assertNotIn('src="web/go.js"', html)
                    self.assertNotIn('src="web/xiangqi.js"', html)


class ScriptedPlayer:
    def __init__(self, moves):
        self.moves = iter(moves)
        self.metadata = {"kind": "scripted"}
        self.closed = False

    async def start(self):
        pass

    async def act(self, prompt, schema, ply, images=None):
        move = next(self.moves)
        observation = json.loads(prompt.split("\n", 1)[1])
        if move not in observation["legal_moves"]:
            raise AssertionError(f"Scripted move {move} is not legal")
        return json.dumps({"move": move, "explanation": "规则与回放验证。"})

    async def close(self):
        self.closed = True


class GoMatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_invalid_config_does_not_create_output_or_start_players(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "match"
            config = Path(temp) / "bad.json"
            config.write_text(json.dumps({"game": "go", "game_options": {"rules": "unsupported"}}))
            args = argparse.Namespace(config=str(config), game=None, random=True, output=str(directory))
            with patch.object(cli, "make_harness") as factory:
                with self.assertRaisesRegex(ValueError, "Unsupported Go rules"):
                    await cli.run(args)
                factory.assert_not_called()
            self.assertFalse(directory.exists())

    async def test_short_match_records_pre_move_images_and_stops_without_result(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            players = {"black": ScriptedPlayer(["D4", "F6"]), "white": ScriptedPlayer(["F4", "D6"])}
            record = await MatchRunner(GoGame(), players, GoRenderer(), directory, 4, 2,
                                       observation_mode="text_image", observation_renderer=render_go_observation).run()
            self.assertEqual(record["status"], "stopped", record["error"])
            self.assertEqual(record["stop_reason"], "max_plies")
            self.assertIsNone(record["result"])
            self.assertEqual(len(record["snapshots"]), 5)
            self.assertTrue(all(p.closed for p in players.values()))
            for turn in record["turns"]:
                ply = turn["ply"]
                manifest = json.loads((directory / f"observations/{ply:04d}.json").read_text())
                self.assertEqual(manifest["state"], record["snapshots"][ply - 1]["state"])
                picture = directory / turn["input"]["images"][0]["path"]
                self.assertEqual(hashlib.sha256(picture.read_bytes()).hexdigest(), turn["input"]["images"][0]["sha256"])
                with Image.open(picture) as image:
                    self.assertEqual(image.size, (900, 1000))
            with Image.open(directory / "frames/0004.png") as image:
                self.assertEqual(image.size, (1120, 850))

    async def test_two_passes_and_resign_finish_on_last_allowed_ply(self):
        for moves, reason, winner in ((["pass", "pass"], "two_passes", "white"), (["resign"], "resignation", "white")):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                players = {"black": ScriptedPlayer(moves[::2]), "white": ScriptedPlayer(moves[1::2])}
                record = await MatchRunner(GoGame(), players, NullRenderer(), directory, len(moves), 2).run()
                self.assertEqual(record["status"], "finished", record["error"])
                self.assertEqual(record["result"]["reason"], reason)
                self.assertEqual(record["result"]["winner"], winner)
                self.assertEqual(record["snapshots"][-1]["state"]["result"], record["result"])

    async def test_cli_runs_go_and_legacy_xiangqi_with_same_entry_point(self):
        with tempfile.TemporaryDirectory() as temp:
            for game_id in ("go", "xiangqi"):
                directory = Path(temp) / game_id
                config = Path(temp) / f"{game_id}.json"
                config.write_text(json.dumps({"game": game_id, "observation_mode": "text"}))
                args = argparse.Namespace(config=str(config), output=str(directory), game=None, until_end=False,
                                          max_plies=4, timeout=2, no_timeout=False, observation_mode=None, random=True)
                self.assertEqual(await cli.run(args), 0)
                record = json.loads((directory / "match.json").read_text(encoding="utf-8"))
                self.assertEqual(record["game_id"], game_id)
                self.assertEqual(len(record["turns"]), 4)
                self.assertEqual(record["status"], "stopped")
                self.assertEqual(list(record["players"]), list(get_game_plugin(game_id).player_ids))
                self.assertIn("game_options", record)


if __name__ == "__main__":
    unittest.main()
