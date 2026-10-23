"""Read images first, then replay identical fixed positions with/without an image."""
import argparse
import asyncio
import hashlib
import html
import json
import statistics
import shutil
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pyffish

from amphoreus_arena.harnesses import make_harness, write_json
from amphoreus_arena.runner import parse_action
from amphoreus_arena.vision import pieces_from_fen, render_observation
from amphoreus_arena.xiangqi import XiangqiGame

POSITIONS = (12, 35, 43, 58)  # Original turn numbers: input is the position BEFORE that move.
READ_POSITIONS = (35, 58)
READ_PROMPT = (
    "Read only the attached Xiangqi board image. Do not play a move. "
    "Do not use tools, files, web search, a chess engine or other agents. "
    "List EVERY occupied intersection exactly once, with its coordinate and piece code. "
    "Omit empty intersections. Files a-i run left to right; rank 1 is at the bottom, rank 10 at the top. "
    "Red pieces use uppercase codes; black pieces use lowercase. "
    "R/r=车, N/n=马, B=相, b=象, A=仕, a=士, K=帅, k=将, C/c=炮, P=兵, p=卒. "
    "Also read the side to move shown above the board. Return ONLY JSON matching "
    '{"to_move":"red or black","pieces":[{"square":"coordinate","piece":"code"}]}. '
    "There is no supplied FEN, piece list or move history; use the image itself."
)
READ_SCHEMA = {"type": "object", "properties": {
    "to_move": {"type": "string", "enum": ["red", "black"]},
    "pieces": {"type": "array", "items": {"type": "object", "properties": {
        "square": {"type": "string"}, "piece": {"type": "string", "enum": list("RNBAKCP rnbakcp".replace(" ", ""))}},
        "required": ["square", "piece"], "additionalProperties": False}}},
    "required": ["to_move", "pieces"], "additionalProperties": False}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_fixture(record, source, ply):
    game = XiangqiGame(record["snapshots"][0]["state"]["fen"])
    game.moves = [turn["action"]["move"] for turn in record["turns"][:ply - 1]]
    if game.snapshot() != record["snapshots"][ply - 1]["state"]:
        raise ValueError(f"Stored snapshot {ply - 1} differs from independently replayed history")
    prompt = (source / game.current_player / f"{ply:04d}.prompt.txt").read_text(encoding="utf-8")
    if json.loads(prompt.split("\n", 1)[1]) != game.observation():
        raise ValueError(f"Original input for turn {ply} differs from replayed observation")
    return {"turn": ply, "initial_fen": game.initial_fen, "state": game.snapshot(),
            "legal_moves": game.legal_actions(), "prompt": prompt,
            "original_move": record["turns"][ply - 1]["action"]["move"]}


def prepare(args):
    config = read_json(args.config)
    for player in config["players"].values():
        if "patches" in player:
            player["patches"] = [str(Path(p).resolve()) for p in player["patches"]]
    source = args.match.resolve()
    record = read_json(source / "match.json")
    manifest = {"source": str(source), "source_sha256": hashlib.sha256((source / "match.json").read_bytes()).hexdigest(),
                "config": config, "positions": list(POSITIONS), "read_positions": list(READ_POSITIONS),
                "repeats": args.repeats, "fresh_session_per_request": True,
                "text_prompt": "Original stored prompt, byte-identical across text and text_image arms (UTF-8 normalized newlines)",
                "image": {"size": [900, 1000], "orientation": "red_bottom", "annotations": "coordinates and current side only"}}
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "manifest.json"
    if path.exists() and read_json(path) != manifest:
        raise ValueError("Output belongs to a different evaluation; use a new directory")
    write_json(path, manifest)
    fixtures = {}
    for ply in POSITIONS:
        fixture = build_fixture(record, source, ply)
        fixtures[ply] = fixture
        image = args.output / "boards" / f"before-{ply:02d}.png"
        render_observation(fixture["state"], image)
        fixture["image"] = str(image)
        fixture["image_sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
    write_json(args.output / "fixtures.json", fixtures)
    return config, fixtures


def score_read(answer, fixture):
    expected = pieces_from_fen(fixture["state"]["fen"])
    entries = answer["pieces"]
    if not isinstance(entries, list):
        raise ValueError("pieces must be an array")
    actual = {entry["square"]: entry["piece"] for entry in entries}
    squares = [f"{file}{rank}" for file in "abcdefghi" for rank in range(1, 11)]
    correct = sum(actual.get(square) == expected.get(square) for square in squares)
    true_pieces = sum(actual.get(square) == piece for square, piece in expected.items())
    discrepancies = [{"square": square, "expected": expected.get(square), "reported": actual.get(square)}
                     for square in sorted(set(expected) | set(actual)) if expected.get(square) != actual.get(square)]
    duplicates = len(entries) - len(actual)
    return {"exact_board": not discrepancies and not duplicates,
            "correct_squares": correct, "total_squares": 90,
            "correct_pieces": true_pieces, "expected_pieces": len(expected), "reported_pieces": len(entries),
            "duplicate_squares": duplicates, "side_correct": answer["to_move"] == fixture["state"]["current_player"],
            "discrepancies": discrepancies}


def score_move(answer, fixture):
    initial, prefix = fixture["initial_fen"], fixture["state"]["moves"]
    move = answer.get("move")
    score = {"legal": isinstance(move, str) and move in fixture["legal_moves"],
             "same_as_original": move == fixture["original_move"]}
    if not score["legal"]:
        return score
    after = prefix + [move]
    replies = pyffish.legal_moves("xiangqi", initial, after)
    mates = [reply for reply in replies if pyffish.gives_check("xiangqi", initial, after + [reply])
             and not pyffish.legal_moves("xiangqi", initial, after + [reply])]
    score.update(opponent_mate_in_one=mates, allows_mate_in_one=bool(mates),
                 gives_check=pyffish.gives_check("xiangqi", initial, after),
                 is_capture=pyffish.is_capture("xiangqi", initial, prefix, move))
    # Availability of a known reply is a narrow fact, not a general engine blunder score.
    known_reply = {12: "b3b10", 35: "g10e8", 43: "g3c3", 58: "i7i10"}[fixture["turn"]]
    score["known_reply"] = {"move": known_reply, "legal": known_reply in replies}
    return score


def tool_events(directory, player):
    """Public native events only: do not infer tool use from private reasoning text."""
    found = []
    if player == "codex":
        for path in directory.glob("*.events.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                item = event.get("item", {})
                if item.get("type") in ("command_execution", "mcp_tool_call", "web_search", "file_change", "collab_tool_call"):
                    found.append({"type": item["type"], "id": item.get("id")})
    else:
        path = directory / "protocol.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                frame = json.loads(line)["frame"]
                update = frame.get("params", {}).get("update", {})
                if update.get("sessionUpdate") == "tool_call":
                    found.append({"type": update.get("kind"), "id": update.get("toolCallId"), "title": update.get("title")})
    return found


async def run_case(output, player, config, fixture, mode, repeat, timeout):
    case_id = f"{mode}-{player}-p{fixture['turn']:02d}-r{repeat}"
    directory = output / "cases" / case_id
    result_path = directory / "result.json"
    if result_path.exists():
        print(f"KEEP {case_id}", flush=True)
        return read_json(result_path)
    if directory.exists():
        raise ValueError(f"Incomplete case {case_id}; inspect it and use a new output directory to retry")
    harness = make_harness(config, directory)
    result = {"id": case_id, "player": player, "mode": mode, "turn": fixture["turn"], "repeat": repeat,
              "started_at": datetime.now(timezone.utc).isoformat(), "status": "running"}
    write_json(directory / "started.json", result)
    prompt = READ_PROMPT if mode == "read" else fixture["prompt"]
    schema = READ_SCHEMA if mode == "read" else XiangqiGame().action_schema()
    images = [Path(fixture["image"])] if mode in ("read", "text_image") else None
    result["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    result["image_sha256"] = fixture["image_sha256"] if images else None
    start = time.monotonic()
    print(f"START {case_id}", flush=True)
    try:
        await asyncio.wait_for(harness.start(), 60)
        action_start = time.monotonic()
        raw = await asyncio.wait_for(harness.act(prompt, schema, 1, images=images), timeout)
        result["action_seconds"] = round(time.monotonic() - action_start, 3)
        answer = parse_action(raw)
        result.update(status="completed", answer=answer,
                      score=score_read(answer, fixture) if mode == "read" else score_move(answer, fixture))
    except Exception as exc:
        result.update(status="failed", error={"type": type(exc).__name__, "message": str(exc)})
        (directory / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - start, 3)
        result["metadata"] = harness.metadata
        await harness.close()
    result["tool_events"] = tool_events(directory, player)
    write_json(result_path, result)
    print(f"DONE {case_id}: {result['status']} {json.dumps(result.get('score', result.get('error')), ensure_ascii=False)}", flush=True)
    return result


async def run_phase(output, config, fixtures, phase, repeats, parallel_cases=False):
    if phase == "replay" and parallel_cases:
        semaphore = asyncio.Semaphore(2)
        async def bounded_case(name, player, fixture, mode, repeat):
            async with semaphore:
                return await run_case(output, name, player, fixture, mode, repeat, config["timeout"])
        jobs = []
        for name, player in config["players"].items():
            probes = [read_json(output / "cases" / f"read-{name}-p{p:02d}-r1" / "result.json") for p in READ_POSITIONS]
            if not any(p["status"] == "completed" and p["score"]["correct_pieces"] > 0 for p in probes):
                print(f"BLOCKED {name}: no successful image-reading probe", flush=True)
                continue
            for repeat in range(1, repeats + 1):
                for ply in POSITIONS:
                    for mode in (("text", "text_image") if repeat % 2 else ("text_image", "text")):
                        jobs.append(bounded_case(name, player, fixtures[ply], mode, repeat))
        await asyncio.gather(*jobs)
        return
    async def run_player(name, player):
        if phase == "read":
            for ply in READ_POSITIONS:
                await run_case(output, name, player, fixtures[ply], "read", 1, config["timeout"])
        else:
            # Verify actual image delivery before comparing it against text.
            probes = [read_json(output / "cases" / f"read-{name}-p{p:02d}-r1" / "result.json") for p in READ_POSITIONS]
            if not any(p["status"] == "completed" and p["score"]["correct_pieces"] > 0 for p in probes):
                print(f"BLOCKED {name}: no successful image-reading probe", flush=True)
                return
            for repeat in range(1, repeats + 1):
                for ply in POSITIONS:
                    # Counterbalance order across repetitions; each request gets a new session.
                    modes = ("text", "text_image") if repeat % 2 else ("text_image", "text")
                    for mode in modes:
                        await run_case(output, name, player, fixtures[ply], mode, repeat, config["timeout"])
    await asyncio.gather(*(run_player(name, player) for name, player in config["players"].items()))


def report(output):
    results = [read_json(path) for path in sorted((output / "cases").glob("*/result.json"))]
    manifest = read_json(output / "manifest.json")
    expected = len(manifest["config"]["players"]) * (len(manifest["read_positions"]) + 2 * len(manifest["positions"]) * manifest["repeats"])
    summary = {"expected_requests": expected, "total_requests": len(results), "completed": sum(r["status"] == "completed" for r in results),
               "failed": sum(r["status"] == "failed" for r in results),
               "interrupted": sum(r["status"] == "interrupted" for r in results), "groups": [], "results": results}
    summary["reused_successes"] = sum(bool(r.get("reused_from")) for r in results)
    summary["new_attempts"] = len(results) - summary["reused_successes"]
    continuation_path = output / "continuation.json"
    if continuation_path.exists():
        source = Path(read_json(continuation_path)["source"])
        previous = [read_json(path) for path in (source / "cases").glob("*/result.json")]
        summary["previous_batch_attempts"] = len(previous)
        summary["total_attempts_including_previous_batch"] = len(previous) + summary["new_attempts"]
    for player in sorted({r["player"] for r in results}):
        for mode in ("read", "text", "text_image"):
            group = [r for r in results if r["player"] == player and r["mode"] == mode]
            if not group:
                continue
            completed = [r for r in group if r["status"] == "completed"]
            entry = {"player": player, "mode": mode, "requests": len(group), "completed": len(completed),
                     "failed": sum(r["status"] == "failed" for r in group),
                     "interrupted": sum(r["status"] == "interrupted" for r in group),
                     "timeouts": sum(r.get("error", {}).get("type") == "TimeoutError" for r in group),
                     "mean_action_seconds": round(statistics.mean(r["action_seconds"] for r in completed), 2) if completed else None,
                     "median_action_seconds": round(statistics.median(r["action_seconds"] for r in completed), 2) if completed else None,
                     "max_action_seconds": round(max(r["action_seconds"] for r in completed), 2) if completed else None,
                     "tool_event_count": sum(len(r["tool_events"]) for r in group)}
            keys = ("exact_board", "correct_squares", "total_squares", "correct_pieces", "expected_pieces", "side_correct") if mode == "read" else ("legal", "same_as_original", "allows_mate_in_one")
            entry.update({key: sum(r["score"].get(key, 0) for r in completed) for key in keys})
            summary["groups"].append(entry)
    write_json(output / "summary.json", summary)
    def esc(value):
        return html.escape(str(value))
    labels = {"read": "只看图识别", "text": "原文本", "text_image": "原文本 + 棋盘图"}
    groups = []
    for group in summary["groups"]:
        detail = (f'完整棋盘 {group["exact_board"]}/{group["completed"]}；棋子 {group["correct_pieces"]}/{group["expected_pieces"]}'
                  if group["mode"] == "read" else f'合法 {group["legal"]}/{group["completed"]}；允许对手一步将死 {group["allows_mate_in_one"]}')
        groups.append(f'<tr><td>{esc(group["player"])}</td><td>{labels[group["mode"]]}</td>'
                      f'<td>{group["completed"]}/{group["requests"]}</td><td>{detail}</td><td>{group["mean_action_seconds"]} s</td></tr>')
    overview = '<table><thead><tr><th>Harness</th><th>输入</th><th>完成</th><th>规则核验 / 读图核验</th><th>成功请求平均耗时</th></tr></thead><tbody>' + ''.join(groups) + '</tbody></table>'
    move_rows = []
    for player in sorted(manifest["config"]["players"]):
        for ply in manifest["positions"]:
            cells = []
            for mode in ("text", "text_image"):
                cases = sorted((r for r in results if r["player"] == player and r["turn"] == ply and r["mode"] == mode), key=lambda r: r["repeat"])
                cells.append(' / '.join(esc(r.get("answer", {}).get("move", r["status"])) for r in cases) or '待完成')
            move_rows.append(f'<tr><td>{esc(player)}</td><td>{ply}</td><td>{cells[0]}</td><td>{cells[1]}</td></tr>')
    moves_table = '<h2>固定局面落子对照</h2><p>每格按第一次 / 第二次显示；着法不同不自动代表棋力优劣。</p><table><thead><tr><th>Harness</th><th>原局第几手之前</th><th>文本</th><th>图文</th></tr></thead><tbody>' + ''.join(move_rows) + '</tbody></table>'
    cards = []
    for result in results:
        metadata = result.get("metadata", {})
        move = result.get("answer", {}).get("move", "识别棋盘")
        cards.append(f'<details><summary>{esc(result["id"])} · {esc(move)} · {esc(result["status"])}</summary>'
                     f'<p>{esc(metadata.get("model"))} / {esc(metadata.get("reasoning_effort"))}</p>'
                     f'<pre>{esc(json.dumps({k: result.get(k) for k in ("answer", "score", "action_seconds", "error", "tool_events")}, ensure_ascii=False, indent=2))}</pre></details>')
    boards = ''.join(f'<figure><img src="boards/before-{p:02d}.png"><figcaption>原局第 {p} 手之前</figcaption></figure>' for p in POSITIONS)
    page = '<!doctype html><meta charset="utf-8"><title>棋盘读图与固定局面测试</title><style>body{max-width:1280px;margin:32px auto;font:16px system-ui;background:#f7f6f2;color:#26363d}h1{font-size:28px}.boards{display:flex;gap:12px}figure{margin:0;flex:1}img{width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px}details{margin:8px 0;border:1px solid #ccd2d0;padding:12px}summary{cursor:pointer}figcaption{padding:8px}a{color:#275f86}table{width:100%;border-collapse:collapse;background:white}th,td{padding:12px;text-align:left;border-bottom:1px solid #dde1dd}th{background:#e7ece8}</style>'
    page += '<h1>棋盘读图与固定局面测试</h1><p>只重放原局第 12、35、43、58 手之前的固定局面。每个请求为新会话；文本组使用原提示词，图文组只多一张棋盘图。无新对局、无视频。小样本冒烟，不代表总体棋力。</p>'
    page += f'<p>已记录 {len(results)}/{expected} 次请求 · 成功 {summary["completed"]} · 失败 {summary["failed"]} · 中断 {summary["interrupted"]}（失败样本保留，不自动重试）</p>'
    if (output / "continuation.json").exists():
        continuation = read_json(output / "continuation.json")
        page += '<p>本批为用户要求取消单步时限后的补跑；沿用先前成功样本，仅重新请求被时限截断、中断或未完成的项目。先前失败记录完整保留。补跑最多两个独立请求并行，耗时不能视为严格控制并发的速度基准。</p>'
        page += '<p>前一批产物：' + esc(continuation["source"]) + '</p>'
        page += f'<p>当前收集了 {summary["reused_successes"]} 个既有成功样本与 {summary["new_attempts"]} 个新结果。连同前批截断和中断，共记录 {summary["total_attempts_including_previous_batch"]} 次实际尝试；表格展示每项的最终保留结果。</p>'
    page += '<div class="boards">' + boards + '</div><h2>汇总</h2>' + overview
    models = sorted({f'{r["player"]}: {r.get("metadata", {}).get("model")} / {r.get("metadata", {}).get("reasoning_effort")}' for r in results})
    page += '<p>实测配置：' + esc('；'.join(models)) + '。读图核验只提供图片和坐标/棋子编码说明。单步对照使用原提示词与完整历史，但不带原生会话旧上下文。平均耗时不含 Harness 启动及关闭。DSH 默认实验模型与原长局不同，仅在本轮同模型组间比较图片效果。</p>'
    page += '<p>known_reply 表示原局关键后继着法在本次走子后是否仍合法。它不自动等于漏子或最佳应对，棋力未用引擎评分。一次避免原失误也不足以证明稳定改善。</p>'
    page += moves_table
    evidence_path = output / "explanation-evidence.json"
    if evidence_path.exists():
        evidence = read_json(evidence_path)
        page += '<h2>关键案例的追加核验</h2><p>以下为具体规则事实，不是对全部说明进行全面评分。</p>'
        for entry in evidence:
            page += '<details><summary>' + esc(entry["case"]) + '</summary><pre>' + esc(json.dumps(entry, ensure_ascii=False, indent=2)) + '</pre></details>'
    page += '<h2>逐次公开输出与规则核验</h2>' + ''.join(cards)
    page += '<p><a href="summary.json">完整 JSON</a> · <a href="manifest.json">测试配置</a> · <a href="fixtures.json">固定局面</a></p>'
    (output / "report.html").write_text(page, encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("total_requests", "completed", "failed", "groups")}, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match", type=Path, default=Path("output/matches/dsh-codex-long-01"))
    parser.add_argument("--config", type=Path, default=Path("configs/vision-eval.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepare", "read", "replay", "report", "all"), default="all")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--reuse-successes", type=Path, help="Explicit continuation from an earlier batch; retains old failures there")
    parser.add_argument("--parallel-cases", action="store_true", help="Allow two independent requests, including from the same Harness")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.output = args.output.resolve()
    if args.phase == "report":
        report(args.output)
        return
    config, fixtures = prepare(args)
    if args.reuse_successes:
        source = args.reuse_successes.resolve()
        old = read_json(source / "manifest.json")
        new = read_json(args.output / "manifest.json")
        for key in ("source_sha256", "positions", "read_positions", "repeats"):
            if old[key] != new[key]:
                raise ValueError(f"Cannot reuse a different experiment: {key}")
        if old["config"]["players"] != new["config"]["players"]:
            raise ValueError("Cannot reuse results from different Harness/model/effort settings")
        reused = []
        for path in sorted((source / "cases").glob("*/result.json")):
            row = read_json(path)
            if row["status"] != "completed":
                continue
            destination = args.output / "cases" / path.parent.name
            if not destination.exists():
                shutil.copytree(path.parent, destination)
                row["reused_from"] = str(path)
                row["original_timeout_seconds"] = old["config"]["timeout"]
                write_json(destination / "result.json", row)
            reused.append(row["id"])
        write_json(args.output / "continuation.json", {"source": str(source), "reused": reused,
                   "reason": "User requested no per-move timeout; preserve earlier censored results and rerun remaining cases",
                   "parallel_cases": args.parallel_cases, "new_timeout_seconds": config["timeout"]})
        if (source / "explanation-evidence.json").exists() and not (args.output / "explanation-evidence.json").exists():
            shutil.copy2(source / "explanation-evidence.json", args.output / "explanation-evidence.json")
    if args.phase in ("read", "all"):
        asyncio.run(run_phase(args.output, config, fixtures, "read", args.repeats, args.parallel_cases))
    if args.phase in ("replay", "all"):
        asyncio.run(run_phase(args.output, config, fixtures, "replay", args.repeats, args.parallel_cases))
    report(args.output)


if __name__ == "__main__":
    main()
