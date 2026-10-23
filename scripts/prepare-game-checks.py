"""Generate short, model-free replay fixtures in a new directory."""
import argparse
import asyncio
import json
from pathlib import Path

from amphoreus_arena.games import configure_game
from amphoreus_arena.harnesses import make_harness
from amphoreus_arena.runner import MatchRunner


class FixtureHarness:
    def __init__(self, moves):
        self.moves = iter(moves)
        self.metadata = {"kind": "scripted"}

    async def start(self):
        pass

    async def close(self):
        pass

    async def act(self, prompt, schema, ply, images=None):
        return json.dumps({"move": next(self.moves), "explanation": "固定规则样例，用于核对提子、停一手和计分回放。"})


async def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    cases = [("go-smoke", "go", None), ("xiangqi-smoke", "xiangqi", None),
             ("go-capture-fixture", "go", ["A2", "A1", "H9", "B1", "B2", "J9", "C1"]),
             ("go-score-fixture", "go", ["D4", "F6", "pass", "pass"])]
    for name, game_id, moves in cases:
        directory = root / name
        plugin, game, options, configs, presentation = configure_game({"game": game_id}, random_players=True)
        players = {side: FixtureHarness(moves[i::2]) if moves else make_harness(configs[side], directory / side)
                   for i, side in enumerate(plugin.player_ids)}
        runner = MatchRunner(game, players, plugin.create_renderer(), directory, len(moves) if moves else 4, 2,
                             observation_mode="text_image", observation_renderer=plugin.observation_renderer)
        runner.record.update(game_options=options, presentation=presentation)
        result = await runner.run()
        if result["status"] not in ("stopped", "finished"):
            raise RuntimeError(result["error"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(prepare(parser.parse_args().output.resolve()))
