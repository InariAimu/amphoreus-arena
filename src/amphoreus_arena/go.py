"""Deterministic Go rules: area scoring, positional superko, no suicide.

This is an explicit computer-play ruleset, not a claim to implement all Chinese
or Japanese tournament adjudication. Players must capture dead stones before
passing; remaining stones are counted as they stand. Passes are superko-exempt.
"""

import json
import math


RULESET = "area-positional-v1"
COLUMNS = "ABCDEFGHJKLMNOPQRST"  # GTP-style coordinates skip I.
COLORS = {"black": "B", "white": "W"}


class GoGame:
    game_id = "go"

    def __init__(self, board_size: int = 9, komi: float = 7.5, rules: str = RULESET):
        if type(board_size) is not int or board_size not in (9, 13, 19):
            raise ValueError("Go board_size must be 9, 13 or 19")
        if (isinstance(komi, bool) or not isinstance(komi, (int, float))
                or not math.isfinite(komi) or komi * 2 != int(komi * 2)):
            raise ValueError("Go komi must be a finite integer or half-integer")
        if rules != RULESET:
            raise ValueError(f"Unsupported Go rules: {rules!r}; expected {RULESET}")
        self.board_size, self.komi, self.rules = board_size, float(komi), rules
        self._board = tuple("." for _ in range(board_size * board_size))
        self._seen = {self._board}
        self._current_player = "black"
        self.moves: list[str] = []
        self.captures = {"black": 0, "white": 0}
        self.consecutive_passes = 0
        self.last_captures: list[str] = []
        self._result = None

    @property
    def current_player(self) -> str:
        return self._current_player

    def _neighbors(self, point: int):
        row, col = divmod(point, self.board_size)
        if row:
            yield point - self.board_size
        if row + 1 < self.board_size:
            yield point + self.board_size
        if col:
            yield point - 1
        if col + 1 < self.board_size:
            yield point + 1

    def _group(self, board, point):
        color, group, liberties, pending = board[point], {point}, set(), [point]
        while pending:
            for neighbor in self._neighbors(pending.pop()):
                if board[neighbor] == ".":
                    liberties.add(neighbor)
                elif board[neighbor] == color and neighbor not in group:
                    group.add(neighbor)
                    pending.append(neighbor)
        return group, liberties

    def _coordinate(self, point):
        row, col = divmod(point, self.board_size)
        return f"{COLUMNS[col]}{self.board_size - row}"

    def _point(self, move):
        # Strict canonical spelling avoids ambiguous or silently repaired actions.
        if (len(move) < 2 or move[0] not in COLUMNS[:self.board_size]
                or not move[1:].isascii() or not move[1:].isdigit()):
            raise ValueError(f"Invalid Go coordinate: {move!r}")
        rank = int(move[1:])
        if not 1 <= rank <= self.board_size or str(rank) != move[1:]:
            raise ValueError(f"Invalid Go coordinate: {move!r}")
        return (self.board_size - rank) * self.board_size + COLUMNS.index(move[0])

    def _play(self, point):
        if self._board[point] != ".":
            raise ValueError("Go intersection is occupied")
        own = COLORS[self.current_player]
        other = "W" if own == "B" else "B"
        board, captured = list(self._board), set()
        board[point] = own
        for neighbor in self._neighbors(point):
            if board[neighbor] == other:
                group, liberties = self._group(board, neighbor)
                if not liberties:
                    captured.update(group)
                    for stone in group:
                        board[stone] = "."
        if not self._group(board, point)[1]:
            raise ValueError("Go suicide is forbidden")
        board = tuple(board)
        if board in self._seen:
            raise ValueError("Go positional superko: repeated board")
        return board, captured

    def legal_actions(self) -> list[str]:
        if self._result is not None:
            return []
        legal = []
        for point, stone in enumerate(self._board):
            if stone == ".":
                try:
                    self._play(point)
                except ValueError:
                    continue
                legal.append(self._coordinate(point))
        return legal + ["pass", "resign"]

    def snapshot(self) -> dict:
        size = self.board_size
        return {"board_size": size, "board": ["".join(self._board[r:r + size])
                                               for r in range(0, size * size, size)],
                "rules": self.rules, "komi": self.komi,
                "current_player": self.current_player, "moves": self.moves.copy(),
                "ply": len(self.moves), "captures": self.captures.copy(),
                "consecutive_passes": self.consecutive_passes,
                "last_captures": self.last_captures.copy(), "result": self.outcome()}

    def observation(self) -> dict:
        return {**self.snapshot(), "legal_moves": self.legal_actions()}

    def action_schema(self) -> dict:
        return {"type": "object", "properties": {
            "move": {"type": "string", "enum": self.legal_actions()},
            "explanation": {"type": "string", "description": "Brief public move rationale in Chinese"}},
                "required": ["move", "explanation"], "additionalProperties": False}

    def prompt(self) -> str:
        return (
            f"Play one move in a {self.board_size}x{self.board_size} Go match. "
            f"You control {self.current_player}. Play to win using only the supplied position. "
            "Do not use tools, files, engines, web search or other agents. "
            f"Coordinates: columns {COLUMNS[:self.board_size]} (I is skipped), "
            f"ranks 1-{self.board_size} from bottom to top. Board rows are top to bottom; "
            "B=black, W=white, .=empty. Black plays first. "
            "Rules: no suicide; positional superko forbids repeating ANY earlier board; "
            "pass is exempt. Two consecutive passes end the game. "
            "Area score counts stones plus empty regions bordered only by your color; "
            f"White receives {self.komi:g} komi. Capture dead stones and finish boundaries "
            "before passing: remaining stones count as they stand, without dead-stone adjudication. "
            "Captured stones do not add separate points. Resign concedes the game. "
            "The legal_moves list is authoritative: select exactly one member. "
            'Return ONLY JSON, e.g. {"move":"D4","explanation":"占据角部，准备向边上展开。"}. '
            "Use move=pass to pass or move=resign to concede. Explanation is a public "
            "1-2 sentence Chinese summary of purpose and risk, not internal reasoning.\n"
            + json.dumps(self.observation(), ensure_ascii=False)
        )

    def step(self, action: dict) -> None:
        if self._result is not None:
            raise ValueError("Go game is already finished")
        move = action.get("move")
        if not isinstance(move, str):
            raise ValueError("Go move must be a string")
        player = self.current_player
        opponent = "white" if player == "black" else "black"
        if move == "resign":
            self._result = {"winner": opponent, "reason": "resignation", "resigned": player}
            self.last_captures = []
        elif move == "pass":
            self.consecutive_passes += 1
            self.last_captures = []
            if self.consecutive_passes == 2:
                score = self.score()
                difference = score["black"] - score["white"]
                self._result = {"winner": "black" if difference > 0 else "white" if difference < 0 else None,
                                "reason": "two_passes", "score": score, "margin": abs(difference),
                                "rules": self.rules, "komi": self.komi}
        else:
            board, captured = self._play(self._point(move))
            self._board = board
            self._seen.add(board)
            self.captures[player] += len(captured)
            self.last_captures = [self._coordinate(p) for p in sorted(captured)]
            self.consecutive_passes = 0
        self.moves.append(move)
        self._current_player = opponent

    def score(self) -> dict:
        """Score the board as it stands; no life/death guesses or prisoner bonus."""
        stones = {side: self._board.count(color) for side, color in COLORS.items()}
        territory, neutral, visited = {"black": 0, "white": 0}, 0, set()
        for point, color in enumerate(self._board):
            if color != "." or point in visited:
                continue
            region, borders, pending = {point}, set(), [point]
            visited.add(point)
            while pending:
                for neighbor in self._neighbors(pending.pop()):
                    if self._board[neighbor] == ".":
                        if neighbor not in visited:
                            region.add(neighbor)
                            visited.add(neighbor)
                            pending.append(neighbor)
                    else:
                        borders.add(self._board[neighbor])
            if borders == {"B"}:
                territory["black"] += len(region)
            elif borders == {"W"}:
                territory["white"] += len(region)
            else:
                neutral += len(region)
        return {"black": stones["black"] + territory["black"],
                "white": stones["white"] + territory["white"] + self.komi,
                "stones": stones, "territory": territory, "neutral": neutral, "komi": self.komi}

    def outcome(self) -> dict | None:
        # A caller must not be able to mutate the rules state via a returned snapshot.
        from copy import deepcopy
        return deepcopy(self._result)
