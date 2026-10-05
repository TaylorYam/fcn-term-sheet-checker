"""由 SVG 原稿重新產生 PANEL 圖示 panel.ico（維護者改圖示時才需要執行）。

每個尺寸都用 headless Chrome／Edge 直接從向量繪製，不從大圖縮小；再用 Pillow 組成多尺寸 .ico。
36px 以上用 panel.svg，32px 以下用簡化版 panel-small.svg。尺寸涵蓋 100%～250% 螢幕縮放下的
小圖示（16 × 縮放）與大圖示（32 × 縮放），缺尺寸時工作列會把小圖放大而變糊（#111）。

需要：Pillow（`pip install pillow`，不是套件依賴）與 Chrome 或 Edge。

    python scripts/build_panel_icon.py [--assets 資料夾] [--browser 瀏覽器路徑]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "fcn_checker" / "assets"
LARGE = (256, 128, 96, 80, 72, 64, 56, 48, 40, 36)  # panel.svg
SMALL = (32, 28, 24, 20, 16)  # panel-small.svg
BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def find_browser() -> str:
    for candidate in BROWSERS:
        if Path(candidate).is_file():
            return candidate
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        if found := shutil.which(name):
            return found
    raise SystemExit("找不到 Chrome 或 Edge，請用 --browser 指定瀏覽器路徑。")


def render(assets: Path, browser: str, work: Path) -> dict[int, Image.Image]:
    """把所有尺寸排在同一頁、透明背景截一次圖，再逐一裁切。"""
    plan = [("panel.svg", s) for s in LARGE] + [("panel-small.svg", s) for s in SMALL]
    tags, boxes, left = [], [], 0
    for name, size in plan:
        uri = (assets / name).as_uri()
        tags.append(f'<img src="{uri}" width="{size}" height="{size}" style="position:absolute;left:{left}px;top:0">')
        boxes.append((size, left))
        left += size + 8
    page = work / "sheet.html"
    page.write_text(f'<html><body style="margin:0;background:transparent">{"".join(tags)}</body></html>', "utf-8")
    shot = work / "sheet.png"
    subprocess.run(
        [
            browser,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--default-background-color=00000000",
            f"--window-size={left},{max(LARGE)}",
            f"--screenshot={shot}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    sheet = Image.open(shot).convert("RGBA")
    return {size: sheet.crop((x, 0, x + size, size)) for size, x in boxes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--assets", type=Path, default=ASSETS, help="SVG 原稿與 panel.ico 所在資料夾")
    parser.add_argument("--browser", help="Chrome 或 Edge 執行檔路徑（預設自動尋找）")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as work:
        frames = render(args.assets, args.browser or find_browser(), Path(work))
    largest = max(frames)
    ico = args.assets / "panel.ico"
    frames[largest].save(
        ico,
        format="ICO",
        sizes=[(s, s) for s in frames],
        append_images=[frames[s] for s in frames if s != largest],
    )
    print(f"已產生 {ico}（{len(frames)} 個尺寸：{', '.join(map(str, sorted(frames)))}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
