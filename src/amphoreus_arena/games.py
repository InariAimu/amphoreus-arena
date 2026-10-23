"""Explicit game plugin registry and common match configuration.

Plugins are registered in code; no arbitrary module paths are loaded from JSON.
Game options and player identities belong to the plugin, not the runner.
"""

from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from .contracts import Game, ObservationRenderer, Renderer
from .go import GoGame
from .go_agreement import create_go
from .go_rendering import GoRenderer, render_go_observation
from .rendering import XiangqiRenderer
from .vision import render_observation
from .xiangqi import XiangqiGame


@dataclass(frozen=True)
class GamePlugin:
    game_id: str
    player_ids: tuple[str, str]
    side_names: tuple[str, str]
    create_game: Callable[..., Game]
    create_renderer: Callable[[], Renderer]
    observation_renderer: ObservationRenderer | None
    browser_scripts: tuple[str, ...]
    default_options: dict


_GAMES: dict[str, GamePlugin] = {}


def register_game(plugin: GamePlugin) -> None:
    if not plugin.game_id or plugin.game_id in _GAMES:
        raise ValueError(f"Game already registered or empty id: {plugin.game_id!r}")
    if len(plugin.player_ids) != 2 or len(set(plugin.player_ids)) != 2 or len(plugin.side_names) != 2:
        raise ValueError("A game plugin needs two distinct player IDs and two side names")
    import re
    if any(not re.fullmatch(r"[a-z][a-z0-9_-]*", side) for side in plugin.player_ids):
        raise ValueError("Player IDs must be safe directory names")
    if any(not re.fullmatch(r"web/[a-zA-Z0-9_-]+\.js", script) for script in plugin.browser_scripts):
        raise ValueError("Browser scripts must be packaged web/*.js assets")
    _GAMES[plugin.game_id] = plugin


def get_game_plugin(game_id: str) -> GamePlugin:
    try:
        return _GAMES[game_id]
    except (KeyError, TypeError):
        raise ValueError(f"Unknown game {game_id!r}; available: {', '.join(_GAMES)}") from None


def registered_games() -> tuple[str, ...]:
    return tuple(_GAMES)


def configure_game(config: dict, *, random_players: bool = False):
    """Normalize new config and legacy Xiangqi config without mutating either."""
    plugin = get_game_plugin(config.get("game", "xiangqi"))
    options = config.get("game_options", {})
    if not isinstance(options, dict):
        raise ValueError("game_options must be an object")
    options = {**deepcopy(plugin.default_options), **deepcopy(options)}
    if "initial_fen" in config:
        if plugin.game_id != "xiangqi":
            raise ValueError("initial_fen only applies to xiangqi")
        if "initial_fen" in config.get("game_options", {}) and config["initial_fen"] != options["initial_fen"]:
            raise ValueError("Conflicting initial_fen values")
        options["initial_fen"] = config["initial_fen"]
    unknown = options.keys() - plugin.default_options.keys()
    if unknown:
        raise ValueError(f"Unknown {plugin.game_id} options: {', '.join(sorted(unknown))}")
    game = plugin.create_game(**options)
    if random_players:
        players = {side: {"kind": "random", "seed": 42 + i} for i, side in enumerate(plugin.player_ids)}
    else:
        players = config.get("players")
        if players is None and plugin.game_id == "xiangqi":
            players = {side: config.get(side) for side in plugin.player_ids}
        if not isinstance(players, dict) or set(players) != set(plugin.player_ids):
            raise ValueError(f"players must contain exactly: {', '.join(plugin.player_ids)}")
        if any(not isinstance(p, dict) or p.get("kind") not in ("dsh", "codex", "random") for p in players.values()):
            raise ValueError("Each player must configure kind: dsh, codex or random")
        if "players" in config and any(side in config for side in plugin.player_ids):
            raise ValueError("Use players or legacy top-level players, not both")
        players = {side: deepcopy(players[side]) for side in plugin.player_ids}
    presentation = deepcopy(config.get("presentation", {}))
    if not isinstance(presentation, dict) or not isinstance(presentation.get("players", {}), dict):
        raise ValueError("presentation and presentation.players must be objects")
    display_players = presentation.setdefault("players", {})
    for side, label in zip(plugin.player_ids, plugin.side_names):
        if not isinstance(display_players.get(side, {}), dict):
            raise ValueError(f"presentation.players.{side} must be an object")
        display_players.setdefault(side, {}).setdefault("side_name", label)
    return plugin, game, options, players, presentation


register_game(GamePlugin("xiangqi", ("red", "black"), ("红方", "黑方"), XiangqiGame,
                         XiangqiRenderer, render_observation, ("web/xiangqi.js",), {"initial_fen": None}))
register_game(GamePlugin("go", ("black", "white"), ("黑方", "白方"), create_go,
                         GoRenderer, render_go_observation, ("web/go.js",),
                         {"board_size": 9, "komi": 7.5, "rules": "area-positional-v1"}))
