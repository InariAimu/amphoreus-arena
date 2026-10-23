"""Go broadcast PNGs and separate, coordinate-labelled player observations."""

from pathlib import Path

from PIL import Image, ImageDraw

from .go import COLUMNS
from .rendering import font


def draw_board(image, state, box):
    draw = ImageDraw.Draw(image)
    left, top, width = box
    size = state["board_size"]
    gap = width / (size - 1)
    radius = gap * 0.43
    draw.rounded_rectangle((left - 34, top - 34, left + width + 34, top + width + 34),
                           radius=12, fill="#e6c58f", outline="#b49361", width=2)
    for index in range(size):
        pos = index * gap
        draw.line((left, top + pos, left + width, top + pos), fill="#806842", width=1)
        draw.line((left + pos, top, left + pos, top + width), fill="#806842", width=1)
        draw.text((left + pos, top + width + 20), COLUMNS[index], font=font(15), fill="#59492f", anchor="mm")
        draw.text((left - 20, top + pos), str(size - index), font=font(15), fill="#59492f", anchor="mm")
    stars = (2, size // 2, size - 3) if size == 9 else (3, size // 2, size - 4)
    for row in stars:
        for col in stars:
            if size == 9 and (row == size // 2) != (col == size // 2):
                continue
            x, y = left + col * gap, top + row * gap
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#806842")
    last = state["moves"][-1] if state["moves"] else None
    dead = set(state.get("adjudicated_dead") or (state.get("negotiation", {}).get("proposal") or {}).get("dead", []))
    for row, line in enumerate(state["board"]):
        for col, stone in enumerate(line):
            if stone == ".":
                continue
            x, y = left + col * gap, top + row * gap
            draw.ellipse((x - radius + 2, y - radius + 3, x + radius + 2, y + radius + 3), fill="#ae915e")
            draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                         fill="#232833" if stone == "B" else "#fcfbf5", outline="#575a61", width=2)
            if f"{COLUMNS[col]}{size - row}" == last:
                r = max(4, gap * .1)
                draw.ellipse((x - r, y - r, x + r, y + r), outline="#e96e55", width=3)
            if f"{COLUMNS[col]}{size - row}" in dead:
                r = radius * .65
                draw.line((x-r, y-r, x+r, y+r), fill="#e96e55", width=3)
                draw.line((x-r, y+r, x+r, y-r), fill="#e96e55", width=3)


def render_go_observation(state: dict, destination: Path):
    image = Image.new("RGB", (900, 1000), "#f5f3ec")
    draw = ImageDraw.Draw(image)
    side = "黑方" if state["current_player"] == "black" else "白方"
    phase = "确认死子" if state.get("phase") == "agreement" else "行棋"
    draw.text((45, 25), f"围棋 {state['board_size']} 路 · {side}{phase} · 已下 {state['ply']} 手", font=font(25), fill="#273a40")
    draw_board(image, state, (90, 135, 720))
    draw.text((45, 925), f"白贴 {state['komi']:g} · 坐标跳过 I · B 黑 / W 白", font=font(20), fill="#53666c")
    image.save(destination)


class GoRenderer:
    def render(self, snapshot: dict, metadata: dict, destination: Path):
        image = Image.new("RGB", (1120, 850), "#f5f3ec")
        draw = ImageDraw.Draw(image)
        draw.text((40, 25), f"AMPHOREUS ARENA · 围棋 {snapshot['board_size']} 路", font=font(25), fill="#273a40")
        draw_board(image, snapshot, (75, 140, 620))
        x = 760
        for offset, side in enumerate(("black", "white")):
            title = "黑方" if side == "black" else "白方"
            kind = metadata["players"][side]["kind"]
            draw.text((x, 140 + offset * 90), f"{title} / {kind}", font=font(23), fill="#273a40")
            draw.text((x, 178 + offset * 90), f"提子 {snapshot['captures'][side]}", font=font(18), fill="#63747a")
        side = "黑方" if snapshot["current_player"] == "black" else "白方"
        last = snapshot["moves"][-1] if snapshot["moves"] else "—"
        last = {"pass": "停一手", "resign": "认输", "propose_dead": "提议死子",
                "accept_dead": "确认数子", "resume_play": "争议续弈"}.get(last, last)
        lines = [f"第 {snapshot['ply']} 手 · {side}行棋", f"上一手：{last}",
                 f"白贴 {snapshot['komi']:g}", "面积计分 · 禁止自杀", "全局同形禁止 · 双停终局"]
        if snapshot.get("phase"):
            lines[-1] = "协商规则 · 参照中规2002"
            if snapshot["phase"] == "agreement":
                lines[0] = f"第 {snapshot['ply']} 步 · {side}确认死子"
        for i, line in enumerate(lines):
            draw.text((x, 355 + i * 36), line, font=font(18), fill="#63747a")
        result = metadata.get("result")
        if result:
            label = {"black": "黑方获胜", "white": "白方获胜", None: "和棋"}[result["winner"]]
            draw.text((x, 590), label, font=font(27), fill="#258777")
            detail = "认输" if result["reason"] == "resignation" else f"黑 {result['score']['black']:g} / 白 {result['score']['white']:g}"
            draw.text((x, 638), detail, font=font(20), fill="#273a40")
        elif metadata.get("status") == "stopped":
            draw.text((x, 590), "达到步数上限 · 未分胜负", font=font(18), fill="#63747a")
        image.save(destination)
