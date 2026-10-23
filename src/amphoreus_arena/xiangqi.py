import json

import pyffish


class XiangqiGame:
    game_id = "xiangqi"

    def __init__(self, initial_fen: str | None = None):
        self.initial_fen = initial_fen or pyffish.start_fen(self.game_id)
        if pyffish.validate_fen(self.initial_fen, self.game_id) != 1:
            raise ValueError("Invalid Xiangqi initial FEN")
        self.moves: list[str] = []

    @property
    def fen(self) -> str:
        return pyffish.get_fen(self.game_id, self.initial_fen, self.moves)

    @property
    def current_player(self) -> str:
        return "red" if self.fen.split()[1] == "w" else "black"

    def legal_actions(self) -> list[str]:
        return pyffish.legal_moves(self.game_id, self.initial_fen, self.moves)

    def snapshot(self) -> dict:
        return {"fen": self.fen, "moves": self.moves.copy(),
                "current_player": self.current_player, "ply": len(self.moves)}

    def observation(self) -> dict:
        return {**self.snapshot(), "legal_moves": self.legal_actions()}

    def action_schema(self) -> dict:
        return {"type": "object", "properties": {
            "move": {"type": "string"},
            "explanation": {"type": "string", "description": "Brief public move rationale in Chinese"}},
                "required": ["move", "explanation"], "additionalProperties": False}

    def prompt(self) -> str:
        return (
            "Play one move in an actual Xiangqi (Chinese chess) match. "
            f"You control {self.current_player}. Choose a good move using only the supplied position. "
            "Do not use tools, files, a chess engine, web search or other agents. "
            "Play to win and consider immediate captures, threats and king safety. "
            "Coordinates are files a-i, ranks 1-10, red starts at the bottom. "
            "FEN uppercase=red, lowercase=black; N=horse, B=elephant, A=advisor, "
            "K=general, C=cannon, P=soldier, R=chariot. "
            "The legal_moves list is authoritative: select exactly one member. "
            'Return ONLY one JSON object with move and explanation, e.g. '
            '{"move":"h3e3","explanation":"架中炮争取中路主动，接下来准备出马。"}. '
            "The explanation is a concise public summary of the move's purpose and key risk, "
            "in 1-2 Chinese sentences; do not provide a detailed internal reasoning transcript. "
            "Do not put anything outside the JSON object.\n"
            + json.dumps(self.observation(), ensure_ascii=False)
        )

    def step(self, action: dict) -> None:
        move = action.get("move")
        if not isinstance(move, str) or move not in self.legal_actions():
            raise ValueError(f"Illegal Xiangqi move: {move!r}")
        self.moves.append(move)

    def outcome(self) -> dict | None:
        # Keep the complete move history: repetition checks need more than a FEN.
        args = (self.game_id, self.initial_fen, self.moves)
        immediate, score = pyffish.is_immediate_game_end(*args)
        if immediate:
            return self._result(score, "rules_immediate")
        if not self.legal_actions():
            return self._result(pyffish.game_result(*args), "no_legal_moves")
        optional, score = pyffish.is_optional_game_end(*args)
        if optional:
            return self._result(score, "rules_optional_auto_claim")
        return None

    def _result(self, score: int, reason: str) -> dict:
        side = self.current_player
        opponent = "black" if side == "red" else "red"
        return {"winner": None if score == 0 else side if score > 0 else opponent,
                "reason": reason}
