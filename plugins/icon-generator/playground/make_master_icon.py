from __future__ import annotations

from pathlib import Path
import sys

from PIL import Image, ImageDraw


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python3 playground/make_master_icon.py <output.png>", file=sys.stderr)
        return 2

    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)

    image = Image.new("RGBA", (1024, 1024), (18, 25, 36, 255))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((164, 164, 860, 860), radius=180, fill=(29, 120, 210, 255))
    draw.ellipse((292, 246, 732, 686), fill=(255, 255, 255, 245))
    draw.rounded_rectangle((404, 596, 620, 744), radius=48, fill=(255, 192, 64, 255))
    draw.polygon([(512, 330), (644, 604), (380, 604)], fill=(18, 25, 36, 255))

    image.save(output, format="PNG", optimize=True)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
