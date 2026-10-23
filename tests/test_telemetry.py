import json
import tempfile
import unittest
from pathlib import Path

from amphoreus_arena.telemetry import enrich_usage


class UsageRecoveryTests(unittest.TestCase):
    def test_codex_cumulative_counts_are_not_added_across_turns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "black").mkdir()
            for ply, total in ((2, 1000), (4, 2500)):
                event = {"type": "turn.completed", "usage": {"input_tokens": total, "output_tokens": 100}}
                (root / "black" / f"{ply:04d}.events.jsonl").write_text(json.dumps(event), encoding="utf-8")
            record = {"players": {"black": {"kind": "codex"}},
                      "turns": [{"ply": p, "player": "black"} for p in (2, 4, 6)]}
            enrich_usage(root, record)
            self.assertEqual([t.get("usage", {}).get("input_tokens") for t in record["turns"]], [1000, 2500, None])
            self.assertEqual(record["turns"][1]["usage"]["kind"], "session")

    def test_acp_usage_is_bound_to_its_prompt_and_missing_is_not_previous_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "red").mkdir()
            events = []

            def usage(value):
                events.append({"direction": "in", "frame": {"method": "session/update", "params": {
                    "update": {"sessionUpdate": "usage_update", "used": value, "size": 1000000}}}})

            usage(999)  # Startup metadata must not be assigned to the opening move.
            for request_id in (10, 11, 12):
                events.append({"direction": "out", "frame": {"method": "session/prompt", "id": request_id}})
                if request_id != 11:
                    usage(request_id * 100)
                events.append({"direction": "in", "frame": {"id": request_id, "result": {"stopReason": "end_turn"}}})
            (root / "red/protocol.jsonl").write_text("\n".join(map(json.dumps, events)), encoding="utf-8")
            record = {"players": {"red": {"kind": "dsh"}},
                      "turns": [{"ply": p, "player": "red", "usage": None} for p in (1, 3, 5)]}
            enrich_usage(root, record)
            self.assertEqual(record["turns"][0]["usage"]["context_tokens"], 1000)
            self.assertIsNone(record["turns"][1]["usage"])
            self.assertEqual(record["turns"][2]["usage"]["context_tokens"], 1200)
            before = json.dumps(record)
            enrich_usage(root, record)
            self.assertEqual(before, json.dumps(record), "Recovery must be idempotent")

    def test_missing_logs_leave_legacy_matches_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            record = {"players": {"red": {"kind": "dsh"}}, "turns": [{"ply": 1, "player": "red"}]}
            self.assertEqual(enrich_usage(Path(temp), record), record)
