"""Optional agreement endgame, referenced to Chinese 2002 articles 8/9/21.4.

This is an Arena profile, not a complete tournament rules implementation.
The original area-positional-v1 remains unchanged.
"""
from copy import deepcopy
import json

from .go import GoGame, RULESET
from .go_prompt import go_instructions

AGREEMENT_RULESET = "area-positional-agreement-v1"
CONTROL_MOVES = {"propose_dead", "accept_dead", "resume_play"}


class AgreementGoGame(GoGame):
    native_tools = True

    def __init__(self, board_size=19, komi=7.5, rules=AGREEMENT_RULESET):
        if rules != AGREEMENT_RULESET:
            raise ValueError("Unsupported agreement rules")
        super().__init__(board_size, komi, RULESET)
        self.rules = rules
        self.phase = "play"
        self.proposal = None
        self.revision = 0
        self.resume_player = None
        self.adjudicated_dead = []

    def snapshot(self):
        return {**super().snapshot(), "phase": self.phase,
                "play_ply": sum(m not in CONTROL_MOVES for m in self.moves),
                "negotiation": {"revision": self.revision, "proposal": deepcopy(self.proposal),
                                "resume_player": self.resume_player},
                "adjudicated_dead": self.adjudicated_dead.copy(),
                "rules_reference": "Chinese 2002 endgame reference; Arena profile"}

    def legal_actions(self):
        if self.phase == "agreement" and self._result is None:
            return ["resign"]
        return super().legal_actions()

    def action_schema(self):
        schema = super().action_schema()
        if self.phase == "agreement" and self._result is None:
            schema["properties"]["move"]["enum"] = ["tool", "resign"]
        return schema

    def prompt(self):
        return go_instructions(self) + "\n" + json.dumps(self.observation(), ensure_ascii=False)

    def dead_points(self, stones):
        if not isinstance(stones, list) or any(not isinstance(s, str) for s in stones):
            raise ValueError("stones must be an array of coordinates")
        points = set()
        for coord in stones:
            point = self._point(coord)
            if self._board[point] == ".":
                raise ValueError(f"Dead-stone proposal includes empty intersection {coord}")
            points.update(self._group(self._board, point)[0])
        return sorted(points)

    def preview_score(self, stones):
        clone = deepcopy(self)
        points = self.dead_points(stones)
        board = list(clone._board)
        for point in points:
            board[point] = "."
        clone._board = tuple(board)
        return {"dead": [self._coordinate(p) for p in points], "score": clone.score(),
                "hypothetical": True, "life_death_verified": False}

    def step(self, action):
        if self._result is not None:
            raise ValueError("Go game is already finished")
        move = action.get("move")
        if self.phase == "play" or move == "resign":
            super().step(action)
            if move == "resign":
                self.phase = "finished"
            elif self.consecutive_passes == 2:
                self._result = None
                self.phase = "agreement"
                self.revision += 1
                self.resume_player = self.current_player
                self.proposal = None
            return
        player = self.current_player
        opponent = "white" if player == "black" else "black"
        if action.get("revision") != self.revision:
            raise ValueError("Stale agreement revision")
        if move == "propose_dead":
            if self.proposal is not None:
                raise ValueError("Accept the existing proposal or resume play")
            dead = [self._coordinate(p) for p in self.dead_points(action.get("stones"))]
            self.proposal = {"id": f"{self.revision}:{len(self.moves)}", "by": player, "dead": dead}
            self._current_player = opponent
        elif move == "accept_dead":
            if not self.proposal or self.proposal["by"] == player or action.get("proposal_id") != self.proposal["id"]:
                raise ValueError("Only the opponent may accept the current proposal")
            preview = self.preview_score(self.proposal["dead"])
            score = preview["score"]
            difference = score["black"] - score["white"]
            # Keep the physical board unchanged; the agreed removals and score
            # are separate, so no artificial position enters the superko history.
            self.adjudicated_dead = preview["dead"]
            self._result = {"winner": "black" if difference > 0 else "white" if difference < 0 else None,
                            "reason": "agreement", "score": score, "margin": abs(difference),
                            "rules": self.rules, "komi": self.komi, "dead": self.adjudicated_dead.copy(),
                            "proposal_id": self.proposal["id"], "confirmed_by": [self.proposal["by"], player]}
            self.phase = "finished"
            self._current_player = opponent
        elif move == "resume_play":
            disputed = action.get("disputed_stone")
            first = self.resume_player
            if self.proposal:
                if not isinstance(disputed, str) or self._board[self._point(disputed)] == ".":
                    raise ValueError("Name an occupied disputed_stone from either color")
                first = self.proposal["by"] if disputed in self.proposal["dead"] else player
            elif disputed is not None:
                raise ValueError("Without a proposal use disputed_stone=null")
            self._current_player = first
            self.proposal = None
            self.consecutive_passes = 0
            self.phase = "play"
        else:
            raise ValueError("Agreement phase requires a native decision tool")
        self.last_captures = []
        self.moves.append(move)


def create_go(**options):
    cls = AgreementGoGame if options.get("rules") == AGREEMENT_RULESET else GoGame
    return cls(**options)
