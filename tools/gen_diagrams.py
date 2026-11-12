#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Harishankar Narayanan
# SPDX-License-Identifier: Apache-2.0
"""Generate the Tri-Queue architecture block diagram (SVG and high-DPI PNG).

The layout mirrors classic decoupled accelerator block diagrams (top memory bar,
left/right stream & drain controllers, central instruction decode feeding three
segmented green command queues, and a purple compute engine housing the QA/QB
operand FIFOs, 8x8 outer-product multiplier array, variable-arity accumulate
cells, and double-banked int32 output tile SC).
"""

from __future__ import annotations

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "figures"

# Canvas dimensions in pixels (matching 1408 x 768 reference aspect ratio)
W, H = 1408, 768

# Palette matched to reference diagram
C_BG = "#ffffff"
C_MEM_FILL = "#dfe6ee"
C_DEC_FILL = "#eef2f7"
C_SLATE_BORDER = "#2c3744"
C_SLATE_TEXT = "#18222c"
C_SLATE_SUB = "#3d4d5e"

C_GREEN_FILL = "#cbf5da"
C_GREEN_ALT = "#ddf9e7"
C_GREEN_BORDER = "#188f4e"

C_PURPLE_BG = "#f4effc"
C_PURPLE_UNIT = "#e3d8fa"
C_PURPLE_CELL = "#f8f5ff"
C_PURPLE_BORDER = "#6c38b8"
C_PURPLE_TEXT = "#2c1a4d"

C_GOLD_FILL = "#ffe096"
C_GOLD_ALT = "#ffedb8"
C_GOLD_BORDER = "#b57612"
C_GOLD_ARROW = "#b86b00"
C_GOLD_TEXT = "#3d2604"

C_BADGE_FILL = "#f7f4cf"
C_SHADOW_HEX = "#e4e9f0"


class DualCanvas:
    """Simultaneously records crisp SVG elements and renders a 3x supersampled PNG."""

    def __init__(self, width: int, height: int, scale: int = 3) -> None:
        self.w = width
        self.h = height
        self.s = scale
        self.svg_body: list[str] = []

        try:
            from PIL import Image, ImageDraw, ImageFont

            self._has_pil = True
            self._Image = Image
            self._ImageFont = ImageFont
            self.img = Image.new("RGB", (width * scale, height * scale), (255, 255, 255))
            self.draw = ImageDraw.Draw(self.img)
        except ImportError:
            self._has_pil = False

    def _font(self, size: float, bold: bool = False):
        if not self._has_pil:
            return None
        px = int(round(size * self.s))
        candidates = (
            [
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/System/Library/Fonts/HelveticaNeue.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ]
            if bold
            else [
                "/System/Library/Fonts/Supplemental/Arial.ttf",
                "/System/Library/Fonts/Helvetica.ttc",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            ]
        )
        for p in candidates:
            if Path(p).exists():
                try:
                    return self._ImageFont.truetype(p, px)
                except OSError:
                    continue
        return self._ImageFont.load_default()

    def rounded_box(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        r: float = 10.0,
        fc: str = "#ffffff",
        ec: str = C_SLATE_BORDER,
        lw: float = 2.2,
        shadow: bool = True,
    ) -> None:
        filt = ' filter="url(#drop-shadow)"' if shadow else ""
        self.svg_body.append(
            f'  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{r:.1f}" fill="{fc}" stroke="{ec}" stroke-width="{lw:.1f}"{filt}/>'
        )
        if self._has_pil:
            s = self.s
            if shadow:
                self.draw.rounded_rectangle(
                    [(x + 3) * s, (y + 4) * s, (x + w + 3) * s, (y + h + 4) * s],
                    radius=r * s,
                    fill=C_SHADOW_HEX,
                )
            self.draw.rounded_rectangle(
                [x * s, y * s, (x + w) * s, (y + h) * s],
                radius=r * s,
                fill=None if fc == "none" else fc,
                outline=None if ec == "none" else ec,
                width=0 if ec == "none" else max(1, int(round(lw * s))),
            )

    def segmented_queue(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        slots: int = 6,
        *,
        fc1: str = C_GREEN_FILL,
        fc2: str = C_GREEN_ALT,
        ec: str = C_GREEN_BORDER,
        lw: float = 2.0,
    ) -> None:
        r = 5.0
        clip_id = f"clip-q-{int(x)}-{int(y)}"
        self.svg_body.append(
            f'  <defs><clipPath id="{clip_id}">'
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r:.1f}"/>'
            f"</clipPath></defs>"
        )
        self.svg_body.append(
            f'  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{r:.1f}" fill="{fc1}" filter="url(#drop-shadow)"/>'
        )
        sw = w / slots
        self.svg_body.append(f'  <g clip-path="url(#{clip_id})">')
        for i in range(slots):
            sx = x + i * sw
            fill = fc1 if i % 2 == 0 else fc2
            self.svg_body.append(
                f'    <rect x="{sx:.2f}" y="{y:.1f}" width="{sw:.2f}" height="{h:.1f}" fill="{fill}"/>'
            )
            if i > 0:
                self.svg_body.append(
                    f'    <line x1="{sx:.2f}" y1="{y:.1f}" x2="{sx:.2f}" y2="{y + h:.1f}" '
                    f'stroke="{ec}" stroke-width="{lw * 0.9:.1f}"/>'
                )
        self.svg_body.append("  </g>")
        self.svg_body.append(
            f'  <rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{r:.1f}" fill="none" stroke="{ec}" stroke-width="{lw:.1f}"/>'
        )

        if self._has_pil:
            s = self.s
            self.draw.rounded_rectangle(
                [(x + 2.5) * s, (y + 3.5) * s, (x + w + 2.5) * s, (y + h + 3.5) * s],
                radius=r * s,
                fill=C_SHADOW_HEX,
            )
            self.draw.rounded_rectangle(
                [x * s, y * s, (x + w) * s, (y + h) * s],
                radius=r * s,
                fill=fc1,
            )
            for i in range(slots):
                sx0 = (x + i * sw) * s
                sx1 = (x + (i + 1) * sw) * s
                if i % 2 == 1:
                    self.draw.rectangle([sx0, (y + 2) * s, sx1, (y + h - 2) * s], fill=fc2)
                if i > 0:
                    self.draw.line(
                        [(sx0, (y + 1) * s), (sx0, (y + h - 1) * s)],
                        fill=ec,
                        width=max(1, int(round(lw * 0.9 * s))),
                    )
            self.draw.rounded_rectangle(
                [x * s, y * s, (x + w) * s, (y + h) * s],
                radius=r * s,
                fill=None,
                outline=ec,
                width=max(1, int(round(lw * s))),
            )

    def polygon(self, pts: list[tuple[float, float]], fill: str) -> None:
        pts_str = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        self.svg_body.append(f'  <polygon points="{pts_str}" fill="{fill}"/>')
        if self._has_pil:
            s = self.s
            self.draw.polygon([(px * s, py * s) for px, py in pts], fill=fill)

    def thick_straight_arrow(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        *,
        shaft_w: float = 11.0,
        head_w: float = 24.0,
        head_l: float = 16.0,
        color: str = C_GOLD_ARROW,
    ) -> None:
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) > abs(dy):
            sgn = 1.0 if dx > 0 else -1.0
            xb = x1 - sgn * head_l
            pts = [
                (x0, y0 - shaft_w / 2),
                (xb, y0 - shaft_w / 2),
                (xb, y0 - head_w / 2),
                (x1, y1),
                (xb, y0 + head_w / 2),
                (xb, y0 + shaft_w / 2),
                (x0, y0 + shaft_w / 2),
            ]
        else:
            sgn = 1.0 if dy > 0 else -1.0
            yb = y1 - sgn * head_l
            pts = [
                (x0 - shaft_w / 2, y0),
                (x0 - shaft_w / 2, yb),
                (x0 - head_w / 2, yb),
                (x1, y1),
                (x0 + head_w / 2, yb),
                (x0 + shaft_w / 2, yb),
                (x0 + shaft_w / 2, y0),
            ]
        self.polygon(pts, color)

    def thick_l_arrow(
        self,
        pts_center: list[tuple[float, float]],
        *,
        shaft_w: float = 11.0,
        head_w: float = 24.0,
        head_l: float = 16.0,
        color: str = C_GOLD_ARROW,
    ) -> None:
        hw = shaft_w / 2.0
        for i in range(len(pts_center) - 1):
            x0, y0 = pts_center[i]
            x1, y1 = pts_center[i + 1]
            if i == len(pts_center) - 2:
                # Start final segment slightly back by hw so corner is seamless
                dx, dy = x1 - x0, y1 - y0
                if abs(dx) > abs(dy):
                    sgn = 1.0 if dx > 0 else -1.0
                    x0_adj = x0 - sgn * hw
                    y0_adj = y0
                else:
                    sgn = 1.0 if dy > 0 else -1.0
                    x0_adj = x0
                    y0_adj = y0 - sgn * hw
                self.thick_straight_arrow(
                    x0_adj,
                    y0_adj,
                    x1,
                    y1,
                    shaft_w=shaft_w,
                    head_w=head_w,
                    head_l=head_l,
                    color=color,
                )
            else:
                if abs(x1 - x0) > abs(y1 - y0):
                    xmin, xmax = min(x0, x1), max(x0, x1)
                    if i > 0:
                        xmin -= hw
                    pts = [
                        (xmin, y0 - hw),
                        (xmax + hw, y0 - hw),
                        (xmax + hw, y0 + hw),
                        (xmin, y0 + hw),
                    ]
                else:
                    ymin, ymax = min(y0, y1), max(y0, y1)
                    if i > 0:
                        ymin -= hw
                    pts = [
                        (x0 - hw, ymin),
                        (x0 + hw, ymin),
                        (x0 + hw, ymax + hw),
                        (x0 - hw, ymax + hw),
                    ]
                self.polygon(pts, color)

    def thin_ortho_arrow(
        self,
        pts: list[tuple[float, float]],
        *,
        color: str = C_GREEN_BORDER,
        lw: float = 2.6,
        head_w: float = 11.0,
        head_l: float = 11.0,
    ) -> None:
        adj_pts = list(pts)
        x0, y0 = pts[-2]
        x1, y1 = pts[-1]
        dx, dy = x1 - x0, y1 - y0
        if abs(dx) > abs(dy):
            sgn = 1.0 if dx > 0 else -1.0
            adj_pts[-1] = (x1 - sgn * (head_l * 0.65), y1)
            ah = [
                (x1, y1),
                (x1 - sgn * head_l, y1 - head_w / 2),
                (x1 - sgn * head_l, y1 + head_w / 2),
            ]
        else:
            sgn = 1.0 if dy > 0 else -1.0
            adj_pts[-1] = (x1, y1 - sgn * (head_l * 0.65))
            ah = [
                (x1, y1),
                (x1 - head_w / 2, y1 - sgn * head_l),
                (x1 + head_w / 2, y1 - sgn * head_l),
            ]

        d_str = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in adj_pts)
        self.svg_body.append(
            f'  <path d="{d_str}" fill="none" stroke="{color}" stroke-width="{lw:.1f}" '
            f'stroke-linecap="square" stroke-linejoin="miter"/>'
        )
        self.polygon(ah, color)

        if self._has_pil:
            s = self.s
            w_px = max(1, int(round(lw * s)))
            for i in range(len(adj_pts) - 1):
                p0 = (adj_pts[i][0] * s, adj_pts[i][1] * s)
                p1 = (adj_pts[i + 1][0] * s, adj_pts[i + 1][1] * s)
                self.draw.line([p0, p1], fill=color, width=w_px)
                r = w_px // 2
                if i > 0:
                    self.draw.rectangle(
                        [p0[0] - r, p0[1] - r, p0[0] + r, p0[1] + r], fill=color
                    )

    def text(
        self,
        x: float,
        y: float,
        label: str,
        *,
        size: float = 14.0,
        bold: bool = False,
        color: str = C_SLATE_TEXT,
        align: str = "center",
        line_gap: float = 1.28,
    ) -> None:
        lines = label.split("\n")
        n = len(lines)
        step = size * line_gap
        start_y = y - ((n - 1) * step) / 2.0

        anchor = {"center": "middle", "left": "start", "right": "end"}[align]
        weight = "700" if bold else "500"
        font_holder = self._font(size, bold=bold)

        for idx, ln in enumerate(lines):
            ly = start_y + idx * step
            self.svg_body.append(
                f'  <text x="{x:.1f}" y="{ly:.1f}" text-anchor="{anchor}" '
                f'dominant-baseline="central" font-family="Helvetica Neue, Helvetica, Arial, sans-serif" '
                f'font-size="{size:.1f}" font-weight="{weight}" fill="{color}">{escape(ln)}</text>'
            )
            if self._has_pil and font_holder is not None:
                s = self.s
                pil_anchor = {"center": "mm", "left": "lm", "right": "rm"}[align]
                self.draw.text(
                    (x * s, ly * s),
                    ln,
                    fill=color,
                    font=font_holder,
                    anchor=pil_anchor,
                )

    def save(self, svg_path: Path, png_path: Path) -> None:
        svg_header = f"""<?xml version="1.0" encoding="UTF-8"?>
<!-- SPDX-FileCopyrightText: 2026 Harishankar Narayanan -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}">
  <defs>
    <filter id="drop-shadow" x="-5%" y="-5%" width="112%" height="116%">
      <feDropShadow dx="2" dy="3" stdDeviation="2.5" flood-color="#0f172a" flood-opacity="0.09"/>
    </filter>
  </defs>
  <rect width="{self.w}" height="{self.h}" fill="{C_BG}"/>
"""
        svg_footer = "\n</svg>\n"
        svg_path.write_text(svg_header + "\n".join(self.svg_body) + svg_footer, encoding="utf-8")

        if self._has_pil:
            out_img = self.img.resize((self.w, self.h), self._Image.Resampling.LANCZOS)
            out_img.save(png_path, format="PNG", optimize=True)


def generate_architecture_diagram() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c = DualCanvas(W, H, scale=3)

    # =========================================================================
    # 1. TOP MEMORY BAR (Local SRAM / SoC Memory)
    # =========================================================================
    c.rounded_box(24, 18, 1360, 58, r=9, fc=C_MEM_FILL, ec=C_SLATE_BORDER, lw=2.3)
    c.text(W / 2, 37, "Local SRAM / SoC Memory", size=18.5, bold=True, color=C_SLATE_TEXT)
    c.text(
        W / 2,
        59,
        "Packed A Column Panels (int8)   ·   Packed B Row Panels (int8)   ·   C Output Tiles (int32 / Sat-Shift int8)   ·   Native OBI or AXI4 Burst (tq_axi_mem)",
        size=11.5,
        bold=False,
        color=C_SLATE_SUB,
    )

    # Thick dark-slate memory port arrows (Read on left, Write on right)
    c.thick_straight_arrow(
        96, 76, 96, 148, shaft_w=17, head_w=36, head_l=20, color=C_SLATE_BORDER
    )
    c.text(122, 103, "128-bit Read", size=11.5, bold=True, color=C_SLATE_TEXT, align="left")
    c.text(122, 121, "(16 B / cycle)", size=10.5, bold=False, color=C_SLATE_SUB, align="left")

    c.thick_straight_arrow(
        1312, 148, 1312, 76, shaft_w=17, head_w=36, head_l=20, color=C_SLATE_BORDER
    )
    c.text(1286, 103, "128-bit Write", size=11.5, bold=True, color=C_SLATE_TEXT, align="right")
    c.text(1286, 121, "(16 B / cycle)", size=10.5, bold=False, color=C_SLATE_SUB, align="right")

    # =========================================================================
    # 2. LEFT & RIGHT CONTROLLERS (Stream Engine & Drain Controller)
    # =========================================================================
    # Left: Stream Engine (tq_stream)
    c.rounded_box(24, 150, 148, 212, r=10, fc=C_MEM_FILL, ec=C_SLATE_BORDER, lw=2.3)
    c.text(98, 198, "Stream\nEngine", size=17.5, bold=True, color=C_SLATE_TEXT, line_gap=1.18)
    c.text(98, 238, "(tq_stream)", size=12.0, bold=True, color=C_SLATE_SUB)
    c.text(
        98,
        296,
        "A/B Panel Walkers\nRound-Robin Arb\nCredit-Based FIFOs\n(0 Read Stalls)",
        size=10.5,
        bold=False,
        color=C_SLATE_SUB,
        line_gap=1.35,
    )

    # Right: Drain Controller (tq_drain)
    c.rounded_box(1236, 150, 148, 212, r=10, fc=C_MEM_FILL, ec=C_SLATE_BORDER, lw=2.3)
    c.text(1310, 198, "Drain\nController", size=17.5, bold=True, color=C_SLATE_TEXT, line_gap=1.18)
    c.text(1310, 238, "(tq_drain)", size=12.0, bold=True, color=C_SLATE_SUB)
    c.text(
        1310,
        296,
        "Background Drain\n16 B/cyc Aligned\nint32 / Shift int8\nAuto-Clears Bank",
        size=10.5,
        bold=False,
        color=C_SLATE_SUB,
        line_gap=1.35,
    )

    # =========================================================================
    # 3. FRONT-END FETCH, DECODE & HARDWARE LOOPS (tq_frontend + tq_regs)
    # =========================================================================
    c.rounded_box(436, 106, 536, 62, r=10, fc=C_DEC_FILL, ec=C_SLATE_BORDER, lw=2.3)
    c.text(
        704,
        126,
        "Instruction Fetch, Decode & Hardware Loops (tq_frontend)",
        size=15.5,
        bold=True,
        color=C_SLATE_TEXT,
    )
    c.text(
        704,
        149,
        "64 B Program Buf (10-Byte Kernel)  ·  8×32b CFG Regs  ·  2-Level Loops (LOOP_T, LOOP_K)",
        size=10.8,
        bold=False,
        color=C_SLATE_SUB,
    )

    # =========================================================================
    # 4. SEGMENTED GREEN COMMAND QUEUES (Stream CmdQ, MAC CmdQ, Drain Cmd)
    # =========================================================================
    qy, qh = 216, 38

    # Left Queue: Stream A/B Cmd Queues
    q1_x, q1_w = 224, 240
    c.text(
        q1_x + q1_w / 2,
        qy - 14,
        "Stream A/B Cmd Queues (2×2)",
        size=13.0,
        bold=True,
        color=C_SLATE_TEXT,
    )
    c.segmented_queue(q1_x, qy, q1_w, qh, slots=6)

    # Center Queue: MAC Command Queue (u_macq)
    q2_x, q2_w = 584, 240
    c.text(
        q2_x + q2_w / 2,
        qy - 14,
        "MAC Cmd Queue (4 × 52b)",
        size=13.0,
        bold=True,
        color=C_SLATE_TEXT,
    )
    c.segmented_queue(q2_x, qy, q2_w, qh, slots=6)

    # Right Queue: Drain Tile Command Queue / Descriptor (SWAP_C)
    q3_x, q3_w = 944, 240
    c.text(
        q3_x + q3_w / 2,
        qy - 14,
        "Tile Drain Cmd (SWAP_C)",
        size=13.0,
        bold=True,
        color=C_SLATE_TEXT,
    )
    c.segmented_queue(q3_x, qy, q3_w, qh, slots=6)

    # Green Control Arrows from Decoder -> Queues -> Controllers / Compute Engine
    c.thin_ortho_arrow([(520, 168), (520, 235), (q1_x + q1_w, 235)])
    c.thin_ortho_arrow([(q1_x, 235), (172, 235)])

    # MAC Cmd Queue arrow into Compute Engine ( matching reference image: no arrow crossing top label )
    c.thin_ortho_arrow([(704, qy + qh), (704, 304)])

    c.thin_ortho_arrow([(888, 168), (888, 235), (q3_x, 235)])
    c.thin_ortho_arrow([(q3_x + q3_w, 235), (1236, 235)])

    # =========================================================================
    # 5. COMPUTE ENGINE CONTAINER (Purple Outer Box)
    # =========================================================================
    c.rounded_box(204, 304, 1000, 444, r=14, fc=C_PURPLE_BG, ec=C_PURPLE_BORDER, lw=2.5)
    c.text(
        228,
        332,
        "Compute Engine (tq_mac_array)",
        size=16.5,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )

    # Top-left callout box (matching the "Local Register File" position in reference)
    c.rounded_box(228, 356, 244, 52, r=6, fc=C_BADGE_FILL, ec=C_SLATE_BORDER, lw=2.0)
    c.text(350, 373, "No Vector RF · No Crossbar", size=12.5, bold=True, color=C_SLATE_TEXT)
    c.text(350, 393, "~6.5 Kbit Arch State (8,562 FFs)", size=10.8, bold=False, color=C_SLATE_SUB)

    # =========================================================================
    # 6. QB OPERAND FIFO (Top Gold Buffer feeding Columns)
    # =========================================================================
    qb_x, qb_y, qb_w, qb_h = 524, 332, 360, 54
    c.rounded_box(qb_x, qb_y, qb_w, qb_h, r=7, fc=C_GOLD_FILL, ec=C_GOLD_BORDER, lw=2.1)
    c.text(
        qb_x + qb_w / 2,
        qb_y + 18,
        "QB Operand FIFO (u_qb · 8 × 16 B)",
        size=14.0,
        bold=True,
        color=C_GOLD_TEXT,
    )
    c.text(
        qb_x + qb_w / 2,
        qb_y + 39,
        "B Row Slices  (8 cols × 2 k-steps / vec  →  8 B / beat)",
        size=10.8,
        bold=False,
        color=C_GOLD_TEXT,
    )

    # Thick golden-brown bus: Stream Engine -> QB Operand FIFO
    c.thick_l_arrow(
        [(172, 282), (674, 282), (674, qb_y)],
        shaft_w=10,
        head_w=22,
        head_l=14,
        color=C_GOLD_ARROW,
    )

    # =========================================================================
    # 7. QA OPERAND FIFO (Left Gold Buffer feeding Rows)
    # =========================================================================
    qa_x, qa_y, qa_w, qa_h = 228, 444, 224, 196
    c.rounded_box(qa_x, qa_y, qa_w, qa_h, r=7, fc=C_GOLD_FILL, ec=C_GOLD_BORDER, lw=2.1)
    c.text(
        qa_x + qa_w / 2,
        qa_y + 50,
        "QA Operand\nFIFO",
        size=16.5,
        bold=True,
        color=C_GOLD_TEXT,
        line_gap=1.18,
    )
    c.text(
        qa_x + qa_w / 2,
        qa_y + 96,
        "(u_qa · 8 × 16 B)",
        size=12.0,
        bold=True,
        color=C_GOLD_TEXT,
    )
    c.text(
        qa_x + qa_w / 2,
        qa_y + 146,
        "A Column Slices\n(8 rows × 2 k-steps / vec)\n8 B / beat → A[0..7][k]",
        size=10.8,
        bold=False,
        color=C_GOLD_TEXT,
        line_gap=1.32,
    )

    # Thick golden-brown bus: Stream Engine -> QA Operand FIFO
    c.thick_l_arrow(
        [(98, 362), (98, 542), (qa_x, 542)],
        shaft_w=12,
        head_w=26,
        head_l=16,
        color=C_GOLD_ARROW,
    )

    # =========================================================================
    # 8. 8x8 OUTER-PRODUCT MULTIPLIER ARRAY (Center White Box)
    # =========================================================================
    arr_x, arr_y, arr_w, arr_h = 524, 422, 360, 228
    c.rounded_box(arr_x, arr_y, arr_w, arr_h, r=8, fc="#ffffff", ec=C_PURPLE_BORDER, lw=2.2)
    c.text(
        arr_x + arr_w / 2,
        arr_y + 18,
        "8×8 Outer-Product Array (64 × 9×9 → 17b)",
        size=13.5,
        bold=True,
        color=C_SLATE_TEXT,
    )
    c.text(
        arr_x + arr_w / 2,
        arr_y + 37,
        "4 Quadrants (TL, TR, BL, BR)  ·  r × c × d = 64 MACs / beat",
        size=10.3,
        bold=False,
        color=C_PURPLE_TEXT,
    )

    col_xs = [552, 668, 784]
    row_ys = [472, 531, 590]
    cw, ch = 72, 36

    # Three golden arrows from QB FIFO down into top of the 3 MAC columns
    for cx in col_xs:
        mid_x = cx + cw / 2
        c.thick_straight_arrow(
            mid_x,
            qb_y + qb_h,
            mid_x,
            arr_y,
            shaft_w=8,
            head_w=18,
            head_l=12,
            color=C_GOLD_ARROW,
        )

    # Three golden arrows from QA FIFO right into left of the 3 MAC rows
    for ry in row_ys:
        mid_y = ry + ch / 2
        c.thick_straight_arrow(
            qa_x + qa_w,
            mid_y,
            arr_x,
            mid_y,
            shaft_w=8,
            head_w=18,
            head_l=12,
            color=C_GOLD_ARROW,
        )

    # Draw 3x3 MAC cells and internal broadcast arrows
    for r_idx, ry in enumerate(row_ys):
        for c_idx, cx in enumerate(col_xs):
            c.rounded_box(
                cx,
                ry,
                cw,
                ch,
                r=5,
                fc=C_PURPLE_CELL,
                ec=C_PURPLE_BORDER,
                lw=2.0,
                shadow=False,
            )
            c.text(cx + cw / 2, ry + ch / 2, "MAC", size=13.5, bold=True, color=C_SLATE_TEXT)
            if c_idx < 2:
                nx = col_xs[c_idx + 1]
                c.thin_ortho_arrow(
                    [(cx + cw, ry + ch / 2), (nx, ry + ch / 2)],
                    color=C_PURPLE_BORDER,
                    lw=2.2,
                    head_w=9,
                    head_l=9,
                )
            if r_idx < 2:
                ny = row_ys[r_idx + 1]
                c.thin_ortho_arrow(
                    [(cx + cw / 2, ry + ch), (cx + cw / 2, ny)],
                    color=C_PURPLE_BORDER,
                    lw=2.2,
                    head_w=9,
                    head_l=9,
                )

    # =========================================================================
    # 9. VARIABLE-ARITY ACCUMULATE CELLS (Bottom Purple Unit)
    # =========================================================================
    acc_x, acc_y, acc_w, acc_h = 524, 678, 360, 52
    c.rounded_box(acc_x, acc_y, acc_w, acc_h, r=7, fc=C_PURPLE_UNIT, ec=C_PURPLE_BORDER, lw=2.2)
    c.text(
        acc_x + acc_w / 2,
        acc_y + 18,
        "Variable-Arity Accumulate Cells (64 × int32)",
        size=13.5,
        bold=True,
        color=C_SLATE_TEXT,
    )
    c.text(
        acc_x + acc_w / 2,
        acc_y + 37,
        "Edge Quadrant Folding:  TL (+4)  ·  TR / BL (+2)  ·  BR (+1)",
        size=10.5,
        bold=False,
        color=C_PURPLE_TEXT,
    )

    # Three purple product arrows from bottom of MAC columns into Accumulate Unit
    for cx in col_xs:
        mid_x = cx + cw / 2
        c.thick_straight_arrow(
            mid_x,
            row_ys[-1] + ch,
            mid_x,
            acc_y,
            shaft_w=6,
            head_w=16,
            head_l=11,
            color=C_PURPLE_BORDER,
        )

    # =========================================================================
    # 10. DOUBLE-BANKED OUTPUT ACCUMULATOR TILE SC (Bottom-Right Gold Box)
    # =========================================================================
    sc_x, sc_y, sc_w, sc_h = 960, 622, 224, 108
    c.rounded_box(sc_x, sc_y, sc_w, sc_h, r=7, fc=C_GOLD_FILL, ec=C_GOLD_BORDER, lw=2.1)
    c.text(
        sc_x + sc_w / 2,
        sc_y + 21,
        "Output Tile SC (2 × 8×8)",
        size=13.5,
        bold=True,
        color=C_GOLD_TEXT,
    )
    c.text(
        sc_x + sc_w / 2,
        sc_y + 41,
        "Ping-Pong int32 Banks (4 Kbit)",
        size=10.5,
        bold=False,
        color=C_GOLD_TEXT,
    )

    # Two mini bank pills inside SC box: Bank X (Compute) <-> Bank Y (Drain)
    c.rounded_box(
        sc_x + 12,
        sc_y + 57,
        94,
        38,
        r=5,
        fc=C_GOLD_ALT,
        ec=C_GOLD_BORDER,
        lw=1.6,
        shadow=False,
    )
    c.text(
        sc_x + 59,
        sc_y + 76,
        "Bank X\n(Compute)",
        size=10.0,
        bold=True,
        color=C_GOLD_TEXT,
        line_gap=1.15,
    )

    c.rounded_box(
        sc_x + 118,
        sc_y + 57,
        94,
        38,
        r=5,
        fc=C_GOLD_ALT,
        ec=C_GOLD_BORDER,
        lw=1.6,
        shadow=False,
    )
    c.text(
        sc_x + 165,
        sc_y + 76,
        "Bank Y\n(Drain)",
        size=10.0,
        bold=True,
        color=C_GOLD_TEXT,
        line_gap=1.15,
    )

    # Thick golden arrow from Variable-Arity Accumulate Cells -> Output Tile SC
    c.thick_straight_arrow(
        acc_x + acc_w,
        acc_y + acc_h / 2,
        sc_x,
        acc_y + acc_h / 2,
        shaft_w=9,
        head_w=20,
        head_l=14,
        color=C_GOLD_ARROW,
    )

    # Thick golden L-arrow from Output Tile SC -> Drain Controller
    c.thick_l_arrow(
        [(sc_x + sc_w, 704), (1310, 704), (1310, 362)],
        shaft_w=12,
        head_w=26,
        head_l=16,
        color=C_GOLD_ARROW,
    )

    svg_path = OUT_DIR / "triqueue_architecture.svg"
    png_path = OUT_DIR / "triqueue_architecture.png"
    c.save(svg_path, png_path)
    print(f"Wrote {svg_path.relative_to(ROOT)} and {png_path.relative_to(ROOT)}")


def generate_arity_overlap_diagram() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h2 = 648
    c = DualCanvas(W, h2, scale=3)

    # =========================================================================
    # LEFT PANEL: VARIABLE-ARITY QUADRANT FOLDING (r * c * d = 64 MACs)
    # =========================================================================
    c.rounded_box(24, 18, 696, 612, r=14, fc=C_PURPLE_BG, ec=C_PURPLE_BORDER, lw=2.5)
    c.text(
        48,
        46,
        "Variable-Arity Quadrant Folding  (r × c × d = 64 MACs / beat)",
        size=16.5,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )
    c.text(
        48,
        69,
        "Idle 4×4 multiplier quadrants work on extra k-steps and fold into active SC cells via fixed wires",
        size=10.8,
        bold=False,
        color=C_SLATE_SUB,
        align="left",
    )

    # Top Sub-Card: Fixed Quadrant Routing & Accumulate Arity
    c.rounded_box(44, 88, 656, 162, r=9, fc="#ffffff", ec=C_PURPLE_BORDER, lw=1.9)

    # Left mini-grid: 8x8 Multiplier Quadrants
    c.text(176, 108, "8×8 Multiplier Quadrants (64 × 9×9)", size=12.0, bold=True, color=C_SLATE_TEXT)
    q_specs = [
        (68, 124, "TL (4×4)", "rows 0..3, cols 0..3", C_GREEN_FILL, C_GREEN_BORDER),
        (178, 124, "TR (4×4)", "rows 0..3, cols 4..7", "#dcebfb", "#2563eb"),
        (68, 184, "BL (4×4)", "rows 4..7, cols 0..3", C_GOLD_FILL, C_GOLD_BORDER),
        (178, 184, "BR (4×4)", "rows 4..7, cols 4..7", C_PURPLE_UNIT, C_PURPLE_BORDER),
    ]
    for qx, qy, qtitle, qsub, qfc, qec in q_specs:
        c.rounded_box(qx, qy, 102, 52, r=6, fc=qfc, ec=qec, lw=1.8, shadow=False)
        c.text(qx + 51, qy + 19, qtitle, size=11.5, bold=True, color=C_SLATE_TEXT)
        c.text(qx + 51, qy + 37, qsub, size=8.8, bold=False, color=C_SLATE_SUB)

    # Arrow between Multiplier Quadrants and Accumulator Arity
    c.thick_straight_arrow(
        294, 180, 358, 180, shaft_w=10, head_w=22, head_l=14, color=C_PURPLE_BORDER
    )
    c.text(326, 152, "Fixed Wire\nFolding", size=9.8, bold=True, color=C_PURPLE_TEXT, line_gap=1.15)
    c.text(326, 206, "(No Crossbar)", size=9.0, bold=False, color=C_SLATE_SUB)

    # Right mini-grid: Accumulate-Cell Arity (Max Products Added / Cycle)
    c.text(
        524, 108, "SC Accumulate-Cell Arity (Products / Cycle)", size=12.0, bold=True, color=C_SLATE_TEXT
    )
    a_specs = [
        (372, 124, "TL: Arity 4 (+4 prods)", "TL + TR + BL + BR", C_GREEN_FILL, C_GREEN_BORDER),
        (530, 124, "TR: Arity 2 (+2 prods)", "TR + BR", "#dcebfb", "#2563eb"),
        (372, 184, "BL: Arity 2 (+2 prods)", "BL + BR", C_GOLD_FILL, C_GOLD_BORDER),
        (530, 184, "BR: Arity 1 (+1 prod)", "BR only", C_PURPLE_UNIT, C_PURPLE_BORDER),
    ]
    for ax_x, ax_y, atitle, asub, afc, aec in a_specs:
        c.rounded_box(ax_x, ax_y, 150, 52, r=6, fc=afc, ec=aec, lw=1.8, shadow=False)
        c.text(ax_x + 75, ax_y + 19, atitle, size=11.0, bold=True, color=C_SLATE_TEXT)
        c.text(ax_x + 75, ax_y + 37, asub, size=9.2, bold=False, color=C_SLATE_SUB)

    # Bottom 2x2 Grid: The 4 VOUTER_MAC AUTO Shapes
    cards = [
        (
            44,
            266,
            "8 × 8 × 1   (Interior Tile)",
            "100% Util",
            C_GREEN_FILL,
            C_GREEN_BORDER,
            [
                ("TL: k", C_GREEN_FILL, C_GREEN_BORDER),
                ("TR: k", C_GREEN_FILL, C_GREEN_BORDER),
                ("BL: k", C_GREEN_FILL, C_GREEN_BORDER),
                ("BR: k", C_GREEN_FILL, C_GREEN_BORDER),
            ],
            [
                ("SC TL (+1)", C_GREEN_FILL, C_GREEN_BORDER),
                ("SC TR (+1)", C_GREEN_FILL, C_GREEN_BORDER),
                ("SC BL (+1)", C_GREEN_FILL, C_GREEN_BORDER),
                ("SC BR (+1)", C_GREEN_FILL, C_GREEN_BORDER),
            ],
            "All 4 quadrants active on step k  ·  1 prod/cell\n16 B/beat (8A + 8B)  ·  1.0 port cycle/beat",
        ),
        (
            380,
            266,
            "8 × 4 × 2   (Right-Edge, ≤4 cols)",
            "50% → 67%",
            "#dcebfb",
            "#2563eb",
            [
                ("TL: k", C_GREEN_FILL, C_GREEN_BORDER),
                ("TR: k+1", "#dcebfb", "#2563eb"),
                ("BL: k", C_GOLD_FILL, C_GOLD_BORDER),
                ("BR: k+1", "#dcebfb", "#2563eb"),
            ],
            [
                ("SC TL (+2)", "#dcebfb", "#2563eb"),
                ("(pad)", "#f1f5f9", "#cbd5e1"),
                ("SC BL (+2)", "#dcebfb", "#2563eb"),
                ("(pad)", "#f1f5f9", "#cbd5e1"),
            ],
            "TR & BR fold into left cols on k+1  ·  2 prods/cell\n24 B/beat (16A + 8B)  ·  1.5 port cycles/beat",
        ),
        (
            44,
            446,
            "4 × 8 × 2   (Bottom-Edge, ≤4 rows)",
            "50% → 67%",
            C_GOLD_FILL,
            C_GOLD_BORDER,
            [
                ("TL: k", C_GREEN_FILL, C_GREEN_BORDER),
                ("TR: k", "#dcebfb", "#2563eb"),
                ("BL: k+1", C_GOLD_FILL, C_GOLD_BORDER),
                ("BR: k+1", C_GOLD_FILL, C_GOLD_BORDER),
            ],
            [
                ("SC TL (+2)", C_GOLD_FILL, C_GOLD_BORDER),
                ("SC TR (+2)", C_GOLD_FILL, C_GOLD_BORDER),
                ("(pad)", "#f1f5f9", "#cbd5e1"),
                ("(pad)", "#f1f5f9", "#cbd5e1"),
            ],
            "BL & BR fold into top rows on k+1  ·  2 prods/cell\n24 B/beat (8A + 16B)  ·  1.5 port cycles/beat",
        ),
        (
            380,
            446,
            "4 × 4 × 4   (Corner Tile, ≤4×4)",
            "25% → 50%",
            C_PURPLE_UNIT,
            C_PURPLE_BORDER,
            [
                ("TL: k", C_GREEN_FILL, C_GREEN_BORDER),
                ("TR: k+1", "#dcebfb", "#2563eb"),
                ("BL: k+2", C_GOLD_FILL, C_GOLD_BORDER),
                ("BR: k+3", C_PURPLE_UNIT, C_PURPLE_BORDER),
            ],
            [
                ("SC TL (+4)", C_PURPLE_UNIT, C_PURPLE_BORDER),
                ("(pad)", "#f1f5f9", "#cbd5e1"),
                ("(pad)", "#f1f5f9", "#cbd5e1"),
                ("(pad)", "#f1f5f9", "#cbd5e1"),
            ],
            "All 4 quadrants fold into TL (k..k+3)  ·  4 prods/cell\n32 B/beat (16A + 16B)  ·  2.0 port cycles/beat",
        ),
    ]

    for cx, cy, ctitle, badge, bfc, bec, qmap, scmap, desc in cards:
        c.rounded_box(cx, cy, 320, 164, r=9, fc="#ffffff", ec=C_SLATE_BORDER, lw=1.9)
        c.text(cx + 12, cy + 20, ctitle, size=11.5, bold=True, color=C_SLATE_TEXT, align="left")
        # Badge pill
        c.rounded_box(cx + 232, cy + 8, 78, 24, r=5, fc=bfc, ec=bec, lw=1.5, shadow=False)
        c.text(cx + 271, cy + 20, badge, size=9.8, bold=True, color=C_SLATE_TEXT)

        # Left 2x2: Multiplier Quadrants (all 4 busy)
        c.text(cx + 71, cy + 40, "MUL Quadrants", size=8.6, bold=True, color=C_SLATE_SUB)
        for qi in range(2):
            for qj in range(2):
                qlabel, qfc, qec = qmap[qi * 2 + qj]
                bx = cx + 12 + qj * 60
                by = cy + 50 + qi * 31
                c.rounded_box(bx, by, 56, 27, r=4, fc=qfc, ec=qec, lw=1.4, shadow=False)
                c.text(bx + 28, by + 13.5, qlabel, size=8.4, bold=True, color=C_SLATE_TEXT)

        # Center fold arrow ->
        c.thick_straight_arrow(
            cx + 136, cy + 80, cx + 168, cy + 80, shaft_w=6, head_w=14, head_l=10, color=bec
        )

        # Right 2x2: Active Output Tile SC Quadrants
        c.text(cx + 235, cy + 40, "Active C Tile (SC)", size=8.6, bold=True, color=C_SLATE_SUB)
        for qi in range(2):
            for qj in range(2):
                slabel, sfc, sec = scmap[qi * 2 + qj]
                bx = cx + 176 + qj * 60
                by = cy + 50 + qi * 31
                is_pad = slabel == "(pad)"
                c.rounded_box(bx, by, 56, 27, r=4, fc=sfc, ec=sec, lw=1.3, shadow=False)
                c.text(
                    bx + 28,
                    by + 13.5,
                    slabel,
                    size=8.2,
                    bold=not is_pad,
                    color="#94a3b8" if is_pad else C_SLATE_TEXT,
                )

        # Bottom summary line
        c.text(
            cx + 160,
            cy + 136,
            desc,
            size=9.3,
            bold=False,
            color=C_SLATE_SUB,
            align="center",
            line_gap=1.26,
        )

    # =========================================================================
    # RIGHT PANEL: 3-WAY DECOUPLED OVERLAP & 10-BYTE KERNEL
    # =========================================================================
    c.rounded_box(740, 18, 644, 612, r=14, fc=C_DEC_FILL, ec=C_SLATE_BORDER, lw=2.5)
    c.text(
        764,
        46,
        "Three-Way Decoupled Overlap & Zero-Bubble Kernel",
        size=16.5,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )
    c.text(
        764,
        69,
        "Stream loads, outer-product compute, and background tile drain execute concurrently",
        size=10.8,
        bold=False,
        color=C_SLATE_SUB,
        align="left",
    )

    # Top Sub-Card: 5-Lane Cycle Timeline (K = 64 -> 64 cycles/tile)
    c.rounded_box(760, 88, 604, 276, r=9, fc="#ffffff", ec=C_SLATE_BORDER, lw=1.9)
    c.text(
        778,
        109,
        "Pipeline Overlap Timeline  (8×8 Tiles, K = 64  →  64 Cycles / Tile)",
        size=12.0,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )

    # Cycle markers: x0 = 904, each 64-cycle tile = 108 px wide (4 tiles = 432 px -> x = 1336)
    tx0 = 904
    tw = 108
    for ti in range(5):
        tx = tx0 + ti * tw
        c.text(tx, 130, f"Cyc {ti * 64}", size=9.5, bold=True, color=C_SLATE_SUB)
        if 0 < ti < 4:
            c.thin_ortho_arrow(
                [(tx, 140), (tx, 336)],
                color="#cbd5e1",
                lw=1.2,
                head_w=0.1,
                head_l=0.1,
            )

    lanes = [
        ("Read Port", "(16 B/cyc)", 146),
        ("MAC Array", "(64 MAC/c)", 184),
        ("SC Bank X", "(8×8 int32)", 222),
        ("SC Bank Y", "(8×8 int32)", 260),
        ("Write Port", "(16 B/cyc)", 298),
    ]
    for ltitle, lsub, ly in lanes:
        c.text(776, ly + 8, ltitle, size=10.5, bold=True, color=C_SLATE_TEXT, align="left")
        c.text(776, ly + 21, lsub, size=8.8, bold=False, color=C_SLATE_SUB, align="left")

    # Lane 1: Read Port (A_t, B_t prefetch)
    for ti in range(4):
        c.rounded_box(
            tx0 + ti * tw + 2,
            146,
            tw - 4,
            27,
            r=4,
            fc=C_GOLD_FILL,
            ec=C_GOLD_BORDER,
            lw=1.5,
            shadow=False,
        )
        c.text(
            tx0 + ti * tw + tw / 2,
            159.5,
            f"Stream A{ti}, B{ti}",
            size=9.5,
            bold=True,
            color=C_GOLD_TEXT,
        )

    # Lane 2: MAC Array (Tile 0..3 compute)
    for ti in range(4):
        c.rounded_box(
            tx0 + ti * tw + 2,
            184,
            tw - 4,
            27,
            r=4,
            fc=C_PURPLE_UNIT,
            ec=C_PURPLE_BORDER,
            lw=1.5,
            shadow=False,
        )
        c.text(
            tx0 + ti * tw + tw / 2,
            197.5,
            f"Compute Tile {ti}",
            size=9.5,
            bold=True,
            color=C_PURPLE_TEXT,
        )

    # Lane 3: SC Bank X (Compute 0, Drain 0, Compute 2, Drain 2)
    dw = 32  # 16 cycles drain
    for ti in [0, 2]:
        c.rounded_box(
            tx0 + ti * tw + 2,
            222,
            tw - 4,
            27,
            r=4,
            fc=C_GREEN_FILL,
            ec=C_GREEN_BORDER,
            lw=1.5,
            shadow=False,
        )
        c.text(
            tx0 + ti * tw + tw / 2,
            235.5,
            f"Accum Tile {ti}",
            size=9.5,
            bold=True,
            color=C_SLATE_TEXT,
        )
        # Drain block immediately after swap
        c.rounded_box(
            tx0 + (ti + 1) * tw + 2,
            222,
            dw,
            27,
            r=4,
            fc=C_GOLD_ALT,
            ec=C_GOLD_BORDER,
            lw=1.4,
            shadow=False,
        )
        c.text(
            tx0 + (ti + 1) * tw + 2 + dw / 2,
            235.5,
            f"D{ti}",
            size=9.0,
            bold=True,
            color=C_GOLD_TEXT,
        )

    # Lane 4: SC Bank Y (Compute 1, Drain 1, Compute 3)
    for ti in [1, 3]:
        c.rounded_box(
            tx0 + ti * tw + 2,
            260,
            tw - 4,
            27,
            r=4,
            fc=C_GREEN_FILL,
            ec=C_GREEN_BORDER,
            lw=1.5,
            shadow=False,
        )
        c.text(
            tx0 + ti * tw + tw / 2,
            273.5,
            f"Accum Tile {ti}",
            size=9.5,
            bold=True,
            color=C_SLATE_TEXT,
        )
        if ti == 1:
            c.rounded_box(
                tx0 + (ti + 1) * tw + 2,
                260,
                dw,
                27,
                r=4,
                fc=C_GOLD_ALT,
                ec=C_GOLD_BORDER,
                lw=1.4,
                shadow=False,
            )
            c.text(
                tx0 + (ti + 1) * tw + 2 + dw / 2,
                273.5,
                "D1",
                size=9.0,
                bold=True,
                color=C_GOLD_TEXT,
            )

    # Lane 5: Write Port (16 cycles per tile, hidden under next tile's 64-cycle compute!)
    for ti in range(3):
        wx = tx0 + (ti + 1) * tw + 2
        c.rounded_box(
            wx,
            298,
            dw,
            27,
            r=4,
            fc=C_MEM_FILL,
            ec=C_SLATE_BORDER,
            lw=1.5,
            shadow=False,
        )
        c.text(wx + dw / 2, 311.5, f"C{ti}", size=9.0, bold=True, color=C_SLATE_TEXT)

    c.text(
        1062,
        346,
        "SWAP_C swaps banks in 0 cycles; each 16-cycle C tile drain (D0, D1, D2) hides under the next tile's 64-cycle compute",
        size=9.3,
        bold=True,
        color=C_SLATE_SUB,
    )

    # Bottom Left Sub-Card: 10-Byte Hardware-Looped GEMM Kernel
    c.rounded_box(760, 376, 286, 234, r=9, fc="#ffffff", ec=C_SLATE_BORDER, lw=1.9)
    c.text(
        776,
        398,
        "10-Byte Reference Kernel (§6.2)",
        size=12.0,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )
    kernel_lines = [
        ("0x00", "ZERO_C", "Clear Bank X & Y"),
        ("0x01", "STREAM_A", "Prefetch tile 0 A"),
        ("0x02", "STREAM_B", "Prefetch tile 0 B"),
        ("0x03", "LOOP_T 5", "For each C tile:"),
        ("0x04", "  STREAM_A", "  Prefetch next A"),
        ("0x05", "  STREAM_B", "  Prefetch next B"),
        ("0x06", "  LOOP_K 1", "  Repeat Kp/2d:"),
        ("0x07", "    VOUTER_MAC", "    2 outer beats"),
        ("0x08", "  SWAP_C", "  0-cyc swap+drain"),
        ("0x09", "FENCE", "Wait drain done"),
    ]
    for idx, (pc, mnem, cmt) in enumerate(kernel_lines):
        ky = 420 + idx * 18
        is_loop = mnem.strip().startswith(("LOOP", "VOUTER", "SWAP"))
        if is_loop:
            c.rounded_box(
                772, ky - 8, 262, 16, r=3, fc=C_PURPLE_CELL, ec="none", lw=0, shadow=False
            )
        c.text(778, ky, pc, size=9.2, bold=False, color=C_SLATE_SUB, align="left")
        c.text(812, ky, mnem, size=9.5, bold=True, color=C_PURPLE_TEXT if is_loop else C_SLATE_TEXT, align="left")
        c.text(916, ky, cmt, size=9.0, bold=False, color=C_SLATE_SUB, align="left")

    # Bottom Right Sub-Card: Post-Route & Cycle-Exact Highlights
    c.rounded_box(1060, 376, 304, 234, r=9, fc="#ffffff", ec=C_SLATE_BORDER, lw=1.9)
    c.text(
        1076,
        398,
        "Measured Efficiency (T = 8)",
        size=12.0,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )

    kpis = [
        ("99.2% Peak Util on 64³ GEMM", "4,130 vs 4,096 ideal cycles (0-cycle SWAP_C)", C_GREEN_FILL, C_GREEN_BORDER),
        ("4 MACs / Byte Loaded", "64 MACs per 16 B vector (no operand RF)", C_GOLD_FILL, C_GOLD_BORDER),
        ("Drain Hidden for Kp ≥ 24", "16-cycle int32 write-back (8 cyc in int8 mode)", "#dcebfb", "#2563eb"),
        ("ASAP7 7nm: 2.53 TOPS/W", "992.5 MHz · 10,281 µm² (176 kGE) · 0.79 pJ/MAC", C_PURPLE_UNIT, C_PURPLE_BORDER),
    ]
    for idx, (ktitle, ksub, kfc, kec) in enumerate(kpis):
        ky = 418 + idx * 45
        c.rounded_box(1074, ky, 276, 39, r=6, fc=kfc, ec=kec, lw=1.5, shadow=False)
        c.text(1086, ky + 13, ktitle, size=10.5, bold=True, color=C_SLATE_TEXT, align="left")
        c.text(1086, ky + 28, ksub, size=8.8, bold=False, color=C_SLATE_SUB, align="left")

    svg_path = OUT_DIR / "triqueue_arity_overlap.svg"
    png_path = OUT_DIR / "triqueue_arity_overlap.png"
    c.save(svg_path, png_path)
    print(f"Wrote {svg_path.relative_to(ROOT)} and {png_path.relative_to(ROOT)}")


def _render_die_bitmap(chip_js_path: Path, box_px: int, scale: int = 3):
    """Render 1:1 DBU standard-cell placement from chip_*.js into a PIL Image."""
    import base64
    import io
    import json
    from PIL import Image, ImageDraw

    txt = chip_js_path.read_text(encoding="utf-8")
    data = json.loads(txt[txt.index("{") : txt.rindex("}") + 1])
    dbu = float(data["dbu"])
    die = data["die"]
    w_dbu = die["width_um"] * dbu
    h_dbu = die["height_um"] * dbu

    # High-contrast module palette on dark silicon substrate
    mod_colors = {
        0: (74, 222, 128),    # mac_bank0 (Bank X) - Emerald
        1: (248, 113, 113),   # mac_bank1 (Bank Y) - Coral
        2: (192, 132, 252),   # mac_prod (9x9 MUL Array) - Violet
        3: (56, 189, 248),    # qa_fifo (QA FIFO) - Sky Cyan
        4: (251, 191, 36),    # qb_fifo (QB FIFO) - Amber Gold
        5: (232, 121, 249),   # mac_cmd_q (MAC CmdQ) - Fuchsia
        6: (45, 212, 191),    # stream_eng (Stream Engine) - Teal
        7: (250, 204, 21),    # frontend (Front-End Decoder) - Yellow
        8: (251, 146, 60),    # drain_unit (Drain Controller) - Orange
        9: (163, 230, 53),    # regs_prog (Host Regs & Prog Buf) - Lime
        10: (129, 140, 248),  # core_ctrl (MAC Control & B1) - Indigo
        11: (30, 41, 59),     # filler / decap / tap
    }
    cts_color = (56, 189, 248)

    px_size = box_px * scale
    pad = int(round(8 * scale))
    inner = px_size - 2 * pad

    img = Image.new("RGB", (px_size, px_size), (15, 23, 42))
    drw = ImageDraw.Draw(img)

    # Core boundary outline
    cx0 = pad + (die["core_x0_um"] * dbu / w_dbu) * inner
    cy0 = pad + ((1.0 - (die["core_y1_um"] * dbu / h_dbu)) * inner)
    cx1 = pad + (die["core_x1_um"] * dbu / w_dbu) * inner
    cy1 = pad + ((1.0 - (die["core_y0_um"] * dbu / h_dbu)) * inner)
    drw.rectangle([cx0, cy0, cx1, cy1], fill=(21, 30, 52), outline=(51, 65, 85), width=2)

    # First pass: filler/decap cells (mod_id == 11, class_id == 4)
    # Second pass: active standard cells so logic/flops stand out crisply
    cells = data["cells"]
    mod_area_dbu2 = {m: 0 for m in range(12)}
    for cell in cells:
        x, y, cw, ch, mod_id, cls_id = cell[0], cell[1], cell[2], cell[3], cell[4], cell[5]
        if mod_id != 11:
            mod_area_dbu2[mod_id] = mod_area_dbu2.get(mod_id, 0) + (cw * ch)
        elif cls_id == 2:
            mod_area_dbu2[11] = mod_area_dbu2.get(11, 0) + (cw * ch)

    for pass_active in (False, True):
        for cell in cells:
            x, y, cw, ch, mod_id, cls_id = cell[0], cell[1], cell[2], cell[3], cell[4], cell[5]
            is_active = (mod_id != 11) or (cls_id == 2)
            if is_active != pass_active:
                continue
            x0 = pad + (x / w_dbu) * inner
            x1 = pad + ((x + cw) / w_dbu) * inner
            # Flip Y so North (+Y in DEF) is at top of image
            y1 = pad + (1.0 - (y / h_dbu)) * inner
            y0 = pad + (1.0 - ((y + ch) / h_dbu)) * inner
            if x1 - x0 < 1.0:
                x1 = x0 + 1.0
            if y1 - y0 < 1.0:
                y1 = y0 + 1.0
            col = cts_color if (mod_id == 11 and cls_id == 2) else mod_colors.get(mod_id, (30, 41, 59))
            drw.rectangle([x0, y0, x1, y1], fill=col)

    # Draw perimeter I/O pins
    for pin in data.get("pins", []):
        px_u, py_u = pin["x"], pin["y"]
        px_c = pad + (px_u * dbu / w_dbu) * inner
        py_c = pad + (1.0 - (py_u * dbu / h_dbu)) * inner
        r = 2.2 * scale
        drw.rectangle([px_c - r, py_c - r, px_c + r, py_c + r], fill=(226, 232, 240))

    # Downsample to 1x PNG bytes for SVG embedding
    small = img.resize((box_px, box_px), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return img, b64, data, mod_area_dbu2


def generate_silicon_ppa_diagram() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h3 = 678
    c = DualCanvas(W, h3, scale=3)

    dies = [
        (
            24,
            18,
            "ASAP7 7nm FinFET  ·  tq_top (T = 8)",
            "119.7 × 119.7 µm Die  ·  992.5 MHz Post-Route  ·  0 DRC",
            ROOT / "ui" / "data" / "chip_asap7.js",
            C_PURPLE_BG,
            C_PURPLE_BORDER,
            [
                ("Cell / Core Area", "10,281 / 13,820 µm²  (74.4% density)"),
                ("Gate Count & State", "176.3 kGE  ·  8,562 Flip-Flops (134 / MAC)"),
                ("Energy / MAC", "0.67–1.08 pJ  (0.79 pJ geomean)"),
                ("Efficiency & Peak", "2.53 TOPS/W geomean  ·  12.36 TOPS/mm²"),
            ],
        ),
        (
            496,
            18,
            "Sky130HD 130nm  ·  tq_top (T = 8)",
            "1,057 × 1,057 µm Die  ·  99.9 MHz Post-Route  ·  0 DRC",
            ROOT / "ui" / "data" / "chip_sky130hd.js",
            C_DEC_FILL,
            C_SLATE_BORDER,
            [
                ("Cell / Core Area", "0.693 / 1.110 mm²  (62.4% density)"),
                ("Gate Count & State", "184.6 kGE  ·  8,562 Flip-Flops (134 / MAC)"),
                ("Energy (Engine Only)", "16.4–25.3 pJ/MAC  (19.1 pJ geomean)"),
                ("Energy (+ OpenRAM)", "23.7 pJ/MAC geomean  (0.105 TOPS/W)"),
            ],
        ),
    ]

    asap_mod_areas = {}
    for dx, dy, dtitle, dsub, js_path, fc, ec, kpi_rows in dies:
        c.rounded_box(dx, dy, 452, 642, r=14, fc=fc, ec=ec, lw=2.5)
        c.text(dx + 20, dy + 28, dtitle, size=15.5, bold=True, color=C_SLATE_TEXT, align="left")
        c.text(dx + 20, dy + 50, dsub, size=10.5, bold=False, color=C_SLATE_SUB, align="left")

        die_px = 412
        die_x = dx + 20
        die_y = dy + 68
        c.rounded_box(die_x - 2, die_y - 2, die_px + 4, die_px + 4, r=8, fc="#0f172a", ec=ec, lw=2.0)

        die_img, b64, data, mod_area_dbu2 = _render_die_bitmap(js_path, die_px, scale=c.s)
        is_asap = "asap7" in js_path.name
        if is_asap:
            asap_mod_areas = mod_area_dbu2

        c.svg_body.append(
            f'  <image x="{die_x}" y="{die_y}" width="{die_px}" height="{die_px}" '
            f'href="data:image/png;base64,{b64}"/>'
        )
        if c._has_pil:
            c.img.paste(die_img, (int(die_x * c.s), int(die_y * c.s)))

        # Overlay key module callout pills at their true DEF centroids
        die_info = data["die"]
        w_um, h_um = die_info["width_um"], die_info["height_um"]
        pad_px = 8.0
        inner_px = die_px - 2 * pad_px
        bms = data["bookmarks"]
        callouts = [
            ("mac_prod", "9×9 MUL Array"),
            ("mac_bank0", "Bank X / Y"),
            ("qa_fifo", "QA / QB FIFOs"),
            ("regs_prog", "Regs & 64B Prog"),
            ("frontend", "Front-End"),
            ("stream_eng", "Stream"),
            ("drain_unit", "Drain"),
        ]
        for bkey, blabel in callouts:
            if bkey not in bms:
                continue
            bm = bms[bkey]
            cx_px = die_x + pad_px + (bm["cx"] / w_um) * inner_px
            cy_px = die_y + pad_px + (1.0 - (bm["cy"] / h_um)) * inner_px
            # Nudge overlapping labels slightly for readability
            if bkey == "qa_fifo":
                cx_px += 26
                cy_px += 6
            elif bkey == "regs_prog":
                cx_px += 34 if is_asap else 18
                if not is_asap:
                    cy_px -= 10
            elif bkey == "frontend" and not is_asap:
                cx_px += 18
                cy_px += 6
            elif bkey == "stream_eng":
                cx_px += 22 if is_asap else 10
                cy_px -= 8 if is_asap else 14
            elif bkey == "drain_unit":
                cx_px -= 18
            pw = max(62, len(blabel) * 6.4 + 14)
            ph = 20
            c.rounded_box(
                cx_px - pw / 2,
                cy_px - ph / 2,
                pw,
                ph,
                r=4,
                fc="#0f172a",
                ec=bm["color"],
                lw=1.4,
                shadow=False,
            )
            c.text(cx_px, cy_px, blabel, size=8.8, bold=True, color="#f8fafc")

        # Bottom KPI summary table inside die card
        ky0 = die_y + die_px + 12
        c.rounded_box(die_x, ky0, die_px, 134, r=8, fc="#ffffff", ec=C_SLATE_BORDER, lw=1.8)
        for idx, (klbl, kval) in enumerate(kpi_rows):
            ry = ky0 + 20 + idx * 31
            if idx > 0:
                c.thin_ortho_arrow(
                    [(die_x + 12, ry - 15), (die_x + die_px - 12, ry - 15)],
                    color="#e2e8f0",
                    lw=1.0,
                    head_w=0.1,
                    head_l=0.1,
                )
            c.text(die_x + 14, ry, klbl, size=10.2, bold=True, color=C_SLATE_TEXT, align="left")
            c.text(
                die_x + die_px - 14,
                ry,
                kval,
                size=10.2,
                bold=True,
                color=C_PURPLE_TEXT if idx >= 2 else C_SLATE_SUB,
                align="right",
            )

    # =========================================================================
    # RIGHT PANEL: MODULE AREA BREAKDOWN & POST-ROUTE ARCHITECTURE ABLATIONS
    # =========================================================================
    rx, ry, rw, rh = 968, 18, 416, 642
    c.rounded_box(rx, ry, rw, rh, r=14, fc=C_MEM_FILL, ec=C_SLATE_BORDER, lw=2.5)
    c.text(
        rx + 20,
        ry + 28,
        "Post-Route Area & Architecture Trade-offs",
        size=15.5,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )
    c.text(
        rx + 20,
        ry + 50,
        "Extracted from OpenROAD 6_final.def & activity power",
        size=10.5,
        bold=False,
        color=C_SLATE_SUB,
        align="left",
    )

    # Top Card: RTL Module Color Legend & Active Area Share (ASAP7)
    c.rounded_box(rx + 18, ry + 68, rw - 36, 318, r=9, fc="#ffffff", ec=C_SLATE_BORDER, lw=1.9)
    c.text(
        rx + 32,
        ry + 90,
        "RTL Module Floorplan Legend & Cell Area Share",
        size=11.8,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )

    tot_area = sum(asap_mod_areas.values()) or 1
    legend_rows = [
        ("9×9 Multipliers & Prod Reg (tq_mac_array)", [2], "#c084fc"),
        ("SC Bank X — 8×8 int32 Tile (u_array.v0)", [0], "#4ade80"),
        ("SC Bank Y — 8×8 int32 Tile (u_array.v1)", [1], "#f87171"),
        ("Beat-1 Latches & MAC Control (tq_core)", [10, 5], "#818cf8"),
        ("Host Regs & 64B Program Buf (tq_regs)", [9], "#a3e635"),
        ("Front-End Decoder & Loops (tq_frontend)", [7], "#facc15"),
        ("Stream Engine & Credits (tq_stream)", [6], "#2dd4bf"),
        ("QA & QB Operand FIFOs (u_qa, u_qb)", [3, 4], "#38bdf8"),
        ("Drain Controller & Staging (tq_drain)", [8], "#fb923c"),
    ]
    for idx, (mname, mids, mhex) in enumerate(legend_rows):
        ly = ry + 116 + idx * 29
        pct = 100.0 * sum(asap_mod_areas.get(m, 0) for m in mids) / tot_area
        c.rounded_box(rx + 32, ly - 8, 16, 16, r=3, fc=mhex, ec=C_SLATE_BORDER, lw=1.2, shadow=False)
        c.text(rx + 56, ly, mname, size=9.8, bold=True, color=C_SLATE_TEXT, align="left")
        c.text(rx + rw - 32, ly, f"{pct:.1f}%", size=9.8, bold=True, color=C_SLATE_SUB, align="right")

    # Bottom Card: Measured Architecture Ablations (flow/results.md)
    c.rounded_box(rx + 18, ry + 398, rw - 36, 228, r=9, fc="#ffffff", ec=C_SLATE_BORDER, lw=1.9)
    c.text(
        rx + 32,
        ry + 420,
        "Measured Post-Route Design Choices (T = 8)",
        size=11.8,
        bold=True,
        color=C_SLATE_TEXT,
        align="left",
    )

    ablations = [
        (
            "Clock Gating (tq_cg vs CLK_GATE = 0)",
            "−29.9% energy/MAC  ·  −8.5% cell area (sky130hd)",
            C_GREEN_FILL,
            C_GREEN_BORDER,
        ),
        (
            "Plain 32b Adder (CPA) vs Carry-Save (CSA)",
            "−21.6% energy/MAC  ·  −23.4% area  ·  −31.7% FFs",
            C_GOLD_FILL,
            C_GOLD_BORDER,
        ),
        (
            "Split Read/Write Ports vs Shared Port",
            "99.2% vs 79.8% util on 64³ GEMM (+0.2% area)",
            "#dcebfb",
            "#2563eb",
        ),
        (
            "Scale-Up Tile (T = 16, 256 MACs on ASAP7)",
            "762 MHz  ·  33,340 µm²  ·  0.448 MAC/cyc/kGE",
            C_PURPLE_UNIT,
            C_PURPLE_BORDER,
        ),
    ]
    for idx, (atitle, asub, afc, aec) in enumerate(ablations):
        ay = ry + 438 + idx * 43
        c.rounded_box(rx + 30, ay, rw - 60, 37, r=5, fc=afc, ec=aec, lw=1.4, shadow=False)
        c.text(rx + 42, ay + 12, atitle, size=9.8, bold=True, color=C_SLATE_TEXT, align="left")
        c.text(rx + 42, ay + 26, asub, size=8.8, bold=False, color=C_SLATE_SUB, align="left")

    svg_path = OUT_DIR / "triqueue_silicon_ppa.svg"
    png_path = OUT_DIR / "triqueue_silicon_ppa.png"
    c.save(svg_path, png_path)
    print(f"Wrote {svg_path.relative_to(ROOT)} and {png_path.relative_to(ROOT)}")


if __name__ == "__main__":
    generate_architecture_diagram()
    generate_arity_overlap_diagram()
    generate_silicon_ppa_diagram()
