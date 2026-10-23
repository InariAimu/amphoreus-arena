"""Offline rule/input audit and exact one-move mate checks. No model or search engine."""
import argparse
import json
from pathlib import Path

import pyffish


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve()
    record = json.loads((directory / "match.json").read_text(encoding="utf-8"))
    variant = record["game_id"]
    initial = record["snapshots"][0]["state"]["fen"]
    history = []
    rows, mate_opportunities, prompt_sizes = [], [], []

    def legal(moves):
        return pyffish.legal_moves(variant, initial, moves)

    def mates(moves):
        return [move for move in legal(moves)
                if pyffish.gives_check(variant, initial, moves + [move])
                and not legal(moves + [move])]

    for turn in record["turns"]:
        ply, side, move = turn["ply"], turn["player"], turn["action"]["move"]
        prompt = (directory / side / f"{ply:04d}.prompt.txt").read_text(encoding="utf-8")
        supplied = json.loads(prompt.split("\n", 1)[1])
        fen = pyffish.get_fen(variant, initial, history)
        options = legal(history)
        expected = {"fen": fen, "moves": history.copy(), "current_player": side,
                    "ply": len(history), "legal_moves": options}
        row = {"ply": ply, "player": side, "move": move,
               "observation_exact": supplied == expected,
               "in_supplied_legal_moves": move in supplied["legal_moves"],
               "in_recomputed_legal_moves": move in options,
               "before_in_check": pyffish.gives_check(variant, initial, history)}
        prompt_sizes.append(len(prompt))
        if winning := mates(history):
            mate_opportunities.append({"ply": ply, "player": side, "mate_in_one": winning,
                                       "selected_move": move, "converted": move in winning})
        # Do not feed invalid moves into native move application APIs.
        if move not in options:
            raise ValueError(f"Illegal recorded move at ply {ply}: {move}")
        history.append(move)
        row["stored_fen_exact"] = pyffish.get_fen(variant, initial, history) == record["snapshots"][ply]["state"]["fen"]
        row["after_in_check"] = pyffish.gives_check(variant, initial, history)
        rows.append(row)

    mate_blunders = []
    for opportunity in mate_opportunities:
        if opportunity["ply"] <= 1:
            continue
        previous_ply = opportunity["ply"] - 1
        prefix = [t["action"]["move"] for t in record["turns"][:previous_ply - 1]]
        alternatives = [{"move": candidate, "opponent_mate_in_one": mates(prefix + [candidate])}
                        for candidate in legal(prefix)]
        mate_blunders.append({"ply": previous_ply, "selected_move": history[previous_ply - 1],
                              "legal_alternatives": alternatives,
                              "alternatives_without_immediate_mate": sum(not a["opponent_mate_in_one"] for a in alternatives)})

    evidence = []
    # Explain specific claims only if this is the exact recorded sequence under review.
    if len(history) == 59 and history[33:35] == ["f6g4", "a8g8"] and history[42] == "c1e3":
        after35 = legal(history[:35])
        evidence.append({"source_explanation_ply": 34, "probe_after_ply": 35,
                         "claim": "horse g4 can next reach f2/h2 without another board change",
                         "candidates": {move: move in after35 for move in ("g4f2", "g4h2")},
                         "blocking_square": "g3", "blocking_piece": "red horse",
                         "qualification": "The explanation says prepares to, so this is an omitted prerequisite, not an attempted illegal action."})
        evidence.append({"source_explanation_ply": 35, "probe_after_ply": 35,
                         "candidates": {move: move in after35 for move in ("g10e8", "g10i8")},
                         "reason": "e8 would be the only screen between the red cannon e7 and black general e10; the elephant cannot expose its own general."})
        for ply, reply in ((6, "h1h8"), (12, "b3b10"), (16, "b1b10"), (43, "g3c3")):
            prefix = history[:ply]
            evidence.append({"ply": ply, "selected_move": history[ply - 1], "next_capture": reply,
                             "reply_is_legal": reply in legal(prefix),
                             "reply_is_capture": pyffish.is_capture(variant, initial, prefix, reply)})

    report = {
        "scope": "Exact rule legality, prompt consistency, and mate-in-one only; no positional engine score or causal prompt experiment.",
        "match": directory.name, "pyffish": pyffish.version(), "turns": len(rows),
        "counts": {key: sum(row[key] for row in rows) for key in (
            "observation_exact", "in_supplied_legal_moves", "in_recomputed_legal_moves", "stored_fen_exact")},
        "prompt_char_range": [min(prompt_sizes), max(prompt_sizes)],
        "mate_in_one_opportunities": mate_opportunities,
        "previous_moves_allowing_mate_in_one": mate_blunders,
        "explanation_probes": evidence, "per_turn": rows,
        "real_model_calls": 0,
    }
    destination = directory / "quality-audit.json"
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("turns", "counts", "mate_in_one_opportunities")}, ensure_ascii=False))
    print(destination)


if __name__ == "__main__":
    main()
