"""A deterministic observation image, separate from the audience broadcast renderer."""
from pathlib import Path

from PIL import Image, ImageDraw

from .rendering import PIECES, font


def pieces_from_fen(fen: str) -> dict[str, str]:
    pieces = {}
    for row, rank in enumerate(fen.split()[0].split("/")):
        column = 0
        for piece in rank:
            if piece.isdigit():
                column += int(piece)
            else:
                pieces[f"{chr(97 + column)}{10 - row}"] = piece
                column += 1
    return pieces


def render_observation(snapshot: dict, destination: Path) -> None:
    """Fixed red-bottom orientation, coordinates on all edges, no move/answer annotations."""
    image = Image.new("RGB", (900, 1000), "#faf8f2")
    draw = ImageDraw.Draw(image)
    ink = "#584e3d"

    def label(xy, text, size=25, fill=ink):
        draw.text(xy, text, font=font(size), fill=fill, anchor="mm")

    def point(square):
        return 90 + (ord(square[0]) - 97) * 90, 115 + (10 - int(square[1:])) * 86

    side = snapshot["current_player"]
    label((450, 28), "轮到红方 / RED to move" if side == "red" else "轮到黑方 / BLACK to move", 27)
    draw.rectangle((58, 83, 842, 921), fill="#f1e3c7")
    for rank in range(1, 11):
        y = point(f"a{rank}")[1]
        draw.line((90, y, 810, y), fill=ink, width=2)
        label((30, y), str(rank))
        label((870, y), str(rank))
    for column in range(9):
        x = 90 + column * 90
        if column in (0, 8):
            draw.line((x, 115, x, 889), fill=ink, width=2)
        else:
            draw.line((x, 115, x, 459), fill=ink, width=2)
            draw.line((x, 545, x, 889), fill=ink, width=2)
        for y in (65, 944):
            label((x, y), chr(97 + column))
    for y in (115, 717):
        draw.line((360, y, 540, y + 172), fill=ink, width=2)
        draw.line((540, y, 360, y + 172), fill=ink, width=2)
    label((270, 502), "楚 河", 28)
    label((630, 502), "汉 界", 28)
    for square, piece in pieces_from_fen(snapshot["fen"]).items():
        x, y = point(square)
        color = "#bb2828" if piece.isupper() else "#18252b"
        draw.ellipse((x - 33, y - 33, x + 33, y + 33), fill="#fffaf0", outline=color, width=3)
        draw.ellipse((x - 28, y - 28, x + 28, y + 28), outline=color, width=1)
        label((x, y - 1), PIECES[piece], 40, color)
    label((450, 980), "红方在下 / RED at bottom · a1 = 左下角", 19)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination)
