import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PIECES = dict(zip("rnbakcpRNBAKCP", "车马象士将炮卒车马相仕帅炮兵"))


@lru_cache(maxsize=24)
def font(size: int):
    candidates = [os.environ.get("ARENA_FONT", ""), "C:/Windows/Fonts/msyh.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "/System/Library/Fonts/PingFang.ttc"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    raise FileNotFoundError("A Chinese font is required; set ARENA_FONT to a TTF/TTC font file")


def point(square):
    return 105 + (ord(square[0]) - ord("a")) * 70, 170 + (10 - int(square[1:])) * 66


class XiangqiRenderer:
    def render(self, snapshot: dict, metadata: dict, destination: Path):
        fen, played = snapshot["fen"], snapshot["moves"]
        image = Image.new("RGB", (1120, 850), "#f5f3ec")
        d = ImageDraw.Draw(image)

        def text(xy, value, size=18, fill="#263c42", anchor=None):
            d.text(xy, str(value), font=font(size), fill=fill, anchor=anchor)

        text((40, 24), "Amphoreus Arena", 30)
        players = metadata["players"]
        text((42, 72), f"中国象棋 · 红 {players['red']['kind']}  /  黑 {players['black']['kind']}", 18, "#63747a")
        d.rounded_rectangle((40, 120, 730, 816), 20, fill="#ead6ad")
        for row in range(10):
            y = 170 + row * 66
            d.line((105, y, 665, y), fill="#6e6049", width=2)
            text((72, y), 10-row, 14, anchor="mm")
        for col in range(9):
            x = 105 + col * 70
            if col in (0, 8):
                d.line((x, 170, x, 764), fill="#6e6049", width=2)
            else:
                d.line((x, 170, x, 434), fill="#6e6049", width=2)
                d.line((x, 500, x, 764), fill="#6e6049", width=2)
            text((x, 795), chr(ord("a")+col), 14, anchor="mm")
        for top in (170, 632):
            d.line((315, top, 455, top+132), fill="#6e6049", width=2)
            d.line((455, top, 315, top+132), fill="#6e6049", width=2)
        text((245, 467), "楚 河", 25, "#78654a", "mm")
        text((525, 467), "汉 界", 25, "#78654a", "mm")
        last_to = None
        if played:
            start, last_to = re.fullmatch(r"([a-i]\d+)([a-i]\d+)", played[-1]).groups()
            x, y = point(start)
            d.rounded_rectangle((x-29, y-29, x+29, y+29), 8, outline="#d28b35", width=4)
            d.line((*point(start), *point(last_to)), fill="#d28b35", width=4)
        for row, rank in enumerate(fen.split()[0].split("/")):
            col = 0
            for piece in rank:
                if piece.isdigit():
                    col += int(piece)
                    continue
                x, y = 105+col*70, 170+row*66
                square = chr(ord("a")+col) + str(10-row)
                if square == last_to:
                    d.ellipse((x-31, y-31, x+31, y+31), fill="#d28b35")
                color = "#b84032" if piece.isupper() else "#273a40"
                d.ellipse((x-26, y-25, x+27, y+28), fill="#bda17b")
                d.ellipse((x-27, y-27, x+27, y+27), fill="#fff1d5", outline=color, width=2)
                d.ellipse((x-23, y-23, x+23, y+23), outline=color, width=1)
                text((x, y-1), PIECES[piece], 32, color, "mm")
                col += 1
        d.rounded_rectangle((760, 120, 1080, 816), 20, fill="#ffffff")
        text((786, 146), "中国象棋 / Xiangqi", 20)
        text((786, 190), f"第 {len(played)} 半回合", 24)
        text((786, 238), "行棋方：" + ("红方" if snapshot["current_player"] == "red" else "黑方"))
        status = metadata.get("status", "running")
        labels = {"running": "对局进行中", "starting": "正在连接 Harness", "stopped": "达到冒烟步数上限",
                  "finished": "对局结束", "failed": "对局中断，见日志", "cancelled": "已取消"}
        text((786, 276), labels.get(status, status), 17, "#63747a")
        result = metadata.get("result")
        if result:
            winner = {"red": "红方胜", "black": "黑方胜", None: "和棋"}[result["winner"]]
            text((786, 309), winner, 19)
        text((786, 356), "最近着法", 18)
        text((786, 390), "红方                 黑方", 15, "#63747a")
        start = max(0, (len(played)+1)//2 - 7) * 2
        for row, i in enumerate(range(start, len(played), 2)):
            text((786, 427+row*32), f"{i//2+1:02}. {played[i]}", 16)
            text((935, 427+row*32), played[i+1] if i+1 < len(played) else "…", 16)
        text((786, 695), "坐标：a–i / 1–10", 16, "#63747a")
        elapsed = metadata.get("last_elapsed_seconds")
        if elapsed is not None:
            text((786, 728), f"上一步耗时：{elapsed:.1f} 秒", 16, "#63747a")
        text((786, 762), "规则：pyffish / Fairy-Stockfish", 14, "#63747a")
        image.save(destination)


def write_replay(directory: Path, record: dict):
    # Resolve only code-registered assets, never script URLs from a match file.
    from .games import get_game_plugin
    try:
        scripts = get_game_plugin(record["game_id"]).browser_scripts
    except ValueError:
        scripts = ()  # Unknown games retain the saved-frame fallback.
    game_scripts = "".join(f'<script src="{script}"></script>' for script in scripts)
    root = Path(__file__).parent
    web = root / "web"
    # Static assets live beside the match so any ordinary HTTP server can host it.
    # Copy only when the packaged asset changed, not every time a clock updates.
    for source in web.rglob("*"):
        if not source.is_file() or source.name == "broadcast.html":
            continue
        target = directory / "web" / source.relative_to(web)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or (target.stat().st_size, target.stat().st_mtime_ns) != (source.stat().st_size, source.stat().st_mtime_ns):
            shutil.copy2(source, target)
    # Escape '<' so even an error containing HTML cannot end the JSON script element.
    data = json.dumps(record, ensure_ascii=False).replace("<", "\\u003c")
    for name, source in (("replay.html", root / "replay.html"), ("broadcast.html", web / "broadcast.html")):
        temp = directory / f"{name}.tmp"
        page = source.read_text(encoding="utf-8").replace("__GAME_SCRIPTS__", game_scripts)
        temp.write_text(page.replace("__MATCH_DATA__", data), encoding="utf-8")
        temp.replace(directory / name)


def export(directory: Path, format: str, seconds: float = 0.8) -> Path:
    if seconds <= 0:
        raise ValueError("Frame duration must be positive")
    record = json.loads((directory / "match.json").read_text(encoding="utf-8"))
    paths = [directory / snapshot["frame"] for snapshot in record["snapshots"]]
    target = directory / f"replay.{format}"
    if format == "gif":
        images = []
        try:
            for path in paths:
                with Image.open(path) as source:
                    images.append(source.convert("P", palette=Image.Palette.ADAPTIVE))
            images[0].save(target, save_all=True, append_images=images[1:],
                           duration=[int(seconds*1000)]*(len(images)-1)+[int(seconds*2000)], loop=0)
        finally:
            for image in images:
                image.close()
    elif format == "mp4":
        executable = shutil.which("ffmpeg")
        if not executable:
            try:
                from imageio_ffmpeg import get_ffmpeg_exe
                executable = get_ffmpeg_exe()
            except ImportError as exc:
                raise RuntimeError('Install video support: pip install -e ".[video]"') from exc
        command = [executable, "-y", "-v", "error", "-framerate", str(1/seconds),
                   "-i", str(directory / "frames/%04d.png"), "-c:v", "libx264",
                   "-r", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
        subprocess.run(command, check=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    else:
        raise ValueError(f"Unknown export format: {format}")
    return target
