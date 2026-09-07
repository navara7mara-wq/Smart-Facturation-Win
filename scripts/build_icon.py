from pathlib import Path

from PIL import Image, ImageDraw


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT_DIR / "installer" / "phoenix-bpu.ico"


def build_icon():
    size = 256
    image = Image.new("RGBA", (size, size), "#06294b")
    draw = ImageDraw.Draw(image)
    layers = (
        ((52, 70), (128, 32), (204, 70), (128, 108)),
        ((52, 112), (128, 74), (204, 112), (128, 150)),
        ((52, 154), (128, 116), (204, 154), (128, 192)),
    )
    for polygon in layers:
        draw.polygon(polygon, fill="#ffffff")
    draw.rounded_rectangle((82, 205, 174, 222), radius=8, fill="#19a257")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(
        OUTPUT_PATH,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(OUTPUT_PATH)


if __name__ == "__main__":
    build_icon()

