#!/usr/bin/env python3
"""生成临时应用图标 icon.icns（占位样式；正式图标属后续工作）。

macOS 上运行：Pillow 生成 iconset PNG → iconutil 转 .icns。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

PACKAGING_DIR = Path(__file__).resolve().parent
SIZES = (16, 32, 64, 128, 256, 512, 1024)


def draw_base(size: int) -> Image.Image:
    image = Image.new("RGB", (size, size), color=(30, 41, 59))
    draw = ImageDraw.Draw(image)
    margin = size // 8
    # 占位样式：圆角底 + 播放三角
    draw.rounded_rectangle(
        (margin, margin, size - margin, size - margin),
        radius=size // 6, fill=(56, 132, 255),
    )
    third = size // 3
    draw.polygon(
        [(third + size // 20, third), (third + size // 20, size - third),
         (size - third, size // 2)],
        fill=(255, 255, 255),
    )
    return image


def main() -> int:
    if sys.platform != "darwin":
        print("需要 macOS（iconutil）", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in SIZES:
            base = draw_base(size)
            base.save(iconset / f"icon_{size}x{size}.png")
            if size <= 512:
                draw_base(size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
        target = PACKAGING_DIR / "icon.icns"
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
            check=True,
        )
        print(f"已生成：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
