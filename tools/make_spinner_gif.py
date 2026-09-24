#!/usr/bin/env python3
"""生成卡片用的 spinner 动图（本仓唯一真源脚本）。

为什么要有它（2026-09-22 用户口径 D1′）：工具行「运行中」图标与正文前加载指示都要用
**我们自己**的动图 —— 现在引用的那张是 CLS / FRY / aiduPOP 源码里硬编码的**共享资产**
（我们的 app 只能引用、不能下载：`234008 The app is not the resource sender`）。
上传一次拿 `img_key` 之后，卡片只引用 key（**不产生每回合的额外 API 调用**）。

⚠️ GIF 格式的两条血泪纪律（v1/v2 实测踩过，别再犯）：
  1. GIF 只有 **1-bit 透明 + 调色板**。半透明像素会被量化成调色板里的杂色 ⇒ 放大 6× 能看到
     青/蓝/灰彩点（LANCZOS 直接缩放 RGBA 也一样出彩点）。
  2. 正确做法 = 形状用**纯色实心**画（"渐隐"用多段实色表达，不用 alpha）→ 超采样渲染 →
     预乘空间缩放 → **阈值二值化** alpha → **自建调色板**写 `transparency=0`。

用法::

    PY="${HERMES_HOME:-$HOME/.hermes}/hermes-agent/venv/bin/python3"
    $PY tools/make_spinner_gif.py --variant swirl --color grey --out assets/spinner-tool.gif
    $PY tools/make_spinner_gif.py --all --outdir /tmp/spinner-candidates   # 出四张候选（挑图用）

变体：``bars``（8 根短条转圈）/ ``swirl``（弧线转圈）/ ``dots``（**三个圆点**，打字指示器 ——
用户 2026-09-22 指定「仿 aiduPOP 那张」，aiduPOP 官方截图里就是三个点）。
``dots`` 单独一档：**48×48 画布**（客户端按 16px 渲染 ⇒ 3× 降采样，边缘更干净）、
**10 帧 × 50ms**、点径随亮度波起伏（头部最大 4.3px、尾部最小 2.7px）——
三条都是用户 2026-09-22 的反馈（「清晰度不够 / 速度比较慢 / 每个点一样大，不够灵动」）。

尺寸 24×24（卡片按 16px 渲染，留余量）、8/12 帧、70ms/帧、透明底、无限循环。
"""
from __future__ import annotations

import argparse
import math
import pathlib
import sys

from PIL import Image, ImageDraw

SIZE = 24
SS = 8                      # 超采样倍率
DUR_MS = 70

#: 灰 / 蓝各自的「头 → 尾」四段实色（深浅交替，取代 alpha 渐隐）
RAMP = {
    "grey": [(110, 115, 125), (138, 143, 153), (168, 173, 182), (200, 204, 210)],
    # 「深灰」档：头部更接近 aiduPOP 参考图里那种接近黑的点，尾部仍留浅灰做拖尾。
    "grey_dark": [(64, 66, 74), (118, 122, 131), (170, 174, 183), (211, 214, 220)],
    "blue": [(35, 90, 220), (51, 112, 255), (120, 165, 255), (185, 210, 255)],
}


def _capsule(d: ImageDraw.ImageDraw, p0, p1, w: int) -> None:
    """带圆头的粗线（PIL 的 line 没有 round cap，用两端小圆补）。"""
    d.line([p0, p1], fill=255, width=w)
    r = w / 2
    for (x, y) in (p0, p1):
        d.ellipse([x - r, y - r, x + r, y + r], fill=255)


def _mask(img: Image.Image, size: int = SIZE) -> Image.Image:
    """超采样灰度图 → 缩放 → 阈值二值化（255 = 实心，0 = 透明）。"""
    return img.resize((size, size), Image.LANCZOS).point(lambda v: 255 if v >= 128 else 0)


def _compose(masks: list[Image.Image], ramp: list[tuple],
                 size: int = SIZE) -> Image.Image:
    """masks[i] 用 ramp[i] 上色；调色板 index 0 = 透明。"""
    pal: list[int] = [0, 0, 0]
    for c in ramp:
        pal += list(c)
    pal += [0, 0, 0] * (256 - len(ramp) - 1)
    out = Image.new("P", (size, size), 0)
    out.putpalette(pal)
    for idx, mask in enumerate(masks, start=1):
        out.paste(idx, (0, 0), mask)
    out.info["transparency"] = 0
    return out


def bars(ramp: list[tuple]) -> list[Image.Image]:
    """8 根圆头短条，按位置套 4 段实色（两根一档），每帧转 45°。"""
    frames = []
    c = SIZE * SS / 2
    r_in, r_out, w = 4.8 * SS, 10.0 * SS, 2.0 * SS
    for f in range(8):
        groups = [Image.new("L", (SIZE * SS, SIZE * SS), 0) for _ in ramp]
        draws = [ImageDraw.Draw(g) for g in groups]
        for i in range(8):
            ang = math.radians((f + i) * 45)
            p0 = (c + r_in * math.cos(ang), c + r_in * math.sin(ang))
            p1 = (c + r_out * math.cos(ang), c + r_out * math.sin(ang))
            _capsule(draws[min(i // 2, len(ramp) - 1)], p0, p1, int(w))
        frames.append(_compose([_mask(g) for g in groups], ramp))
    return frames


def swirl(ramp: list[tuple]) -> list[Image.Image]:
    """头/中/尾三段弧 + 缺口，每帧转 30°（转动方向清楚）。"""
    frames = []
    box = (3.0 * SS, 3.0 * SS, (SIZE - 3.0) * SS, (SIZE - 3.0) * SS)
    w = int(2.8 * SS)
    spans = [(305, 395), (255, 340), (200, 300)]        # 头 / 中 / 尾（角度）
    for f in range(12):
        groups = [Image.new("L", (SIZE * SS, SIZE * SS), 0) for _ in ramp]
        draws = [ImageDraw.Draw(g) for g in groups]
        base = f * 30
        for idx, (a0, a1) in enumerate(spans):
            draws[idx].arc(box, base + a0, base + a1, fill=255, width=w)
        frames.append(_compose([_mask(g) for g in groups], ramp))
    return frames


def dots(ramp: list[tuple], count: int = 3, n_frames: int = 10,
         canvas: int = 48) -> list[Image.Image]:
    """三个圆点「打字指示器」：亮的部分从左往右跑、后面的点渐暗（仿 aiduPOP 那张资产）。

    为什么是它（2026-09-22）：用户要「仿照之前用的 aiduPOP 资产」。本地 aiduPOP 检出
    `assets/screenshots/01-instant-response.png` 里那行 loading 元素就是**三个圆点**
    （左深、中灰、右浅）—— 也就是 `typing…` 那种指示器；条状/弧线不是那个味道。

    动画：第 i 个点的相位比第 i+1 个早 `1/count` 个循环 ⇒ 每一帧都呈
    「头(最深) → 中 → 尾(最浅)」的拖尾，且拖尾整体左→右移动。色阶用 `ramp` 的
    **实色**分段（GIF 没有 alpha 渐隐，见模块 docstring 的纪律）。
    """
    levels = len(ramp)
    unit = canvas / 16.0             # 画布是「16px 渲染尺寸」的多少倍
    d_base = 3.1 * unit * SS         # 基准点径（渲染后约 3.1px）
    # 2026-09-22 用户复批：「大小起伏再明显一点，小的点让它更小一点」⇒ 拉开三档：
    # 头 4.8px（+55%）、基准 3.1px、尾 1.9px（-39%）。起伏靠**点径**表达，间距（节距）不变。
    d_max = 4.8 * unit * SS
    d_min = 1.9 * unit * SS
    gap = 1.0 * unit * SS
    x0 = (canvas * SS - (count * d_max + (count - 1) * gap)) / 2
    yc = canvas * SS / 2
    frames = []
    for f in range(n_frames):
        groups = [Image.new("L", (canvas * SS, canvas * SS), 0) for _ in ramp]
        draws = [ImageDraw.Draw(g) for g in groups]
        for i in range(count):
            phase = ((f / n_frames) - i / count) % 1.0
            lvl = min(int(phase * levels), levels - 1)   # phase≈0 ⇒ ramp[0]（最深）
            # 大小起伏与亮度波同相：phase≈0（最亮最深）时最大，越靠尾越小
            wave = math.cos(2 * math.pi * phase)          # 1 → -1
            d = d_base + (d_max - d_base) * ((wave + 1) / 2)
            if lvl == levels - 1:
                d = d_min        # 最浅那颗 = **最小那颗**（用户 2026-09-22：「小的点让它更小」）
            cx = x0 + i * (d_max + gap) + d_max / 2
            draws[lvl].ellipse([cx - d / 2, yc - d / 2, cx + d / 2, yc + d / 2], fill=255)
        frames.append(_compose([_mask(g, canvas) for g in groups], ramp, canvas))
    return frames


def save(frames: list[Image.Image], path: pathlib.Path,
         duration_ms: int = DUR_MS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=duration_ms, loop=0, disposal=2, transparency=0)
    print(f"{path}: {path.stat().st_size} bytes, {len(frames)} frames")


def build(variant: str, color: str) -> list[Image.Image]:
    ramp = RAMP[color]
    if variant == "bars":
        return bars(ramp)
    if variant == "dots":
        return dots(ramp)      # 10 帧 × 50ms = 500ms/轮（见 save 的 duration）
    return swirl(ramp)


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 spinner 动图（纯色实心 + 二值透明）")
    ap.add_argument("--variant", choices=("bars", "swirl", "dots"), default="swirl")
    ap.add_argument("--color", choices=("grey", "blue", "grey_dark"), default="grey")
    ap.add_argument("--out", default="", help="输出 .gif 路径")
    ap.add_argument("--all", action="store_true", help="输出四种组合到 --outdir（挑图用）")
    ap.add_argument("--outdir", default="", help="配合 --all 的输出目录")
    args = ap.parse_args()
    if args.all:
        outdir = pathlib.Path(args.outdir or ".")
        for variant in ("bars", "swirl", "dots"):
            for color in ("grey", "blue", "grey_dark"):
                save(build(variant, color), outdir / f"cand-{variant}-{color}.gif")
        return 0
    if not args.out:
        ap.error("需要 --out（或 --all --outdir）")
    save(build(args.variant, args.color), pathlib.Path(args.out),
         duration_ms=50 if args.variant == "dots" else DUR_MS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
