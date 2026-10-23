import argparse
import asyncio
import importlib.metadata
import json
import shutil
import sys
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .harnesses import dsh_command, make_harness
from .games import configure_game, registered_games
from .rendering import export, font, write_replay
from .telemetry import enrich_usage
from .runner import MatchRunner


def load_config(path):
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    local = Path("arena.local.json")
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8-sig"))
    return {"game": "xiangqi", "max_plies": 12, "turn_timeout": None,
            "startup_timeout": 60, "observation_mode": "text_image",
            "red": {"kind": "dsh", "reasoning_effort": "low",
                    "model": '["deepseek-official","deepseek-flash"]',
                    "patches": [str(Path(__file__).with_name("dsh-vision.patch.yml").resolve())]},
            "black": {"kind": "codex", "model": "gpt-6-astra", "reasoning_effort": "low"}}


async def run(args):
    config = load_config(args.config)
    if getattr(args, "game", None):
        config["game"] = args.game
    plugin, game, options, player_configs, presentation = configure_game(config, random_players=args.random)
    max_plies = None if args.until_end else args.max_plies if args.max_plies is not None else config.get("max_plies", 12)
    timeout = None if args.no_timeout else args.timeout if args.timeout is not None else config.get("turn_timeout")
    startup_timeout = config.get("startup_timeout", 60)
    observation_mode = args.observation_mode or config.get("observation_mode", "text_image")
    if ((max_plies is not None and max_plies < 1) or (timeout is not None and timeout <= 0)
            or startup_timeout <= 0):
        raise ValueError("max_plies and turn_timeout must be positive or null; startup_timeout must be positive")
    if observation_mode not in ("text", "text_image"):
        raise ValueError("observation_mode must be text or text_image")
    if observation_mode == "text_image" and plugin.observation_renderer is None:
        raise ValueError(f"{plugin.game_id} does not provide an observation renderer; select text mode")
    directory = Path(args.output or ("output/matches/" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    players = {side: make_harness(player_configs[side], directory / side) for side in plugin.player_ids}
    runner = MatchRunner(game, players, plugin.create_renderer(), directory, max_plies, timeout,
                         observation_mode=observation_mode, observation_renderer=plugin.observation_renderer,
                         startup_timeout=startup_timeout)
    runner.record["game_options"] = options
    runner.record["presentation"] = presentation
    print(f"Match: {directory}\nView: {directory / 'replay.html'}", flush=True)
    record = await runner.run()
    print(json.dumps({"status": record["status"], "plies": len(record["turns"]),
                      "stop_reason": record["stop_reason"], "output": str(directory)}, ensure_ascii=False))
    return 1 if record["status"] == "failed" else 0


def main():
    parser = argparse.ArgumentParser(description="Amphoreus Arena: native harness board-game matches")
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="Run a real match (or labelled random plumbing test)")
    run_parser.add_argument("--config")
    run_parser.add_argument("--game", choices=registered_games(), help="Override the configured game")
    run_parser.add_argument("--output")
    limits = run_parser.add_mutually_exclusive_group()
    limits.add_argument("--max-plies", type=int)
    limits.add_argument("--until-end", action="store_true", help="Play until the rules report a terminal result; no ply cap")
    timeouts = run_parser.add_mutually_exclusive_group()
    timeouts.add_argument("--timeout", type=float, help="Override the per-move inference timeout in seconds")
    timeouts.add_argument("--no-timeout", action="store_true", help="Disable the per-move inference timeout")
    run_parser.add_argument("--observation-mode", choices=["text", "text_image"],
                            help="Override player input mode (default: text_image)")
    run_parser.add_argument("--random", action="store_true")
    doctor = commands.add_parser("doctor", help="Check local dependencies and executable paths; no inference")
    doctor.add_argument("--config")
    export_parser = commands.add_parser("export", help="Export existing frames without calling any model")
    export_parser.add_argument("directory", type=Path)
    export_parser.add_argument("--format", choices=["gif", "mp4"], default="mp4")
    export_parser.add_argument("--seconds", type=float, default=0.8)
    serve = commands.add_parser("serve", help="Serve a match for live viewing and replay")
    serve.add_argument("directory", type=Path)
    serve.add_argument("--port", type=int, default=8765)
    replay = commands.add_parser("replay", help="Rebuild display pages and recover usage from existing logs; no inference")
    replay.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "run":
        try:
            sys.exit(asyncio.run(run(args)))
        except KeyboardInterrupt:
            sys.exit(130)
    elif args.command == "export":
        print(export(args.directory.resolve(), args.format, args.seconds))
    elif args.command == "replay":
        directory = args.directory.resolve()
        record = enrich_usage(directory, json.loads((directory / "match.json").read_text(encoding="utf-8")))
        temp = directory / "match.json.tmp"
        temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(directory / "match.json")
        write_replay(directory, record)
        print(directory / "replay.html")
    elif args.command == "doctor":
        config = load_config(args.config)
        plugin, _, _, player_configs, _ = configure_game(config)
        data = {"python": sys.version.split()[0], "pyffish": importlib.metadata.version("pyffish"),
                "pillow": importlib.metadata.version("Pillow"), "font": font(16).path,
                "codex": shutil.which("codex"), "game": plugin.game_id,
                "games": registered_games()}
        for side, player_config in player_configs.items():
            if player_config["kind"] == "dsh":
                data.setdefault("dsh_commands", {})[side] = dsh_command(player_config)[0]
        print(json.dumps(data, ensure_ascii=False, indent=2))
    elif args.command == "serve":
        directory = args.directory.resolve()
        if not (directory / "match.json").is_file():
            parser.error("directory must contain match.json")
        handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
        print(f"http://127.0.0.1:{args.port}/replay.html", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
