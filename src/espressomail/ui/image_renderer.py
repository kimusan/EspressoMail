"""Terminal image rendering engine supporting Chafa, Sixel, Kitty, Half-Block, and Braille."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from enum import Enum
from pathlib import Path

from espresso.beans import (
    ImageViewer,
    TerminalGraphicsProtocol,
    parse_bmp,
    parse_ppm,
)
from espresso.crema import ROUNDED_BORDER, Align, Style

from espressomail.models.entities import Attachment
from espressomail.themes.engine import Theme


class ImageRenderBackend(str, Enum):
    """Selectable terminal image rendering backends."""

    AUTO = "auto"          # Chafa if available, otherwise Espresso ImageViewer (Sixel/Kitty/Half-block)
    CHAFA = "chafa"        # External `chafa` CLI tool
    SIXEL = "sixel"        # DEC Sixel graphics (`img2sixel` or Espresso native Sixel encoder)
    KITTY = "kitty"        # Kitty Graphics Protocol (APC)
    HALF_BLOCK = "half_block"  # 24-bit TrueColor Unicode half-blocks (▀)
    BRAILLE = "braille"    # 2x4 Unicode Braille dot matrix
    OFF = "off"            # Disable inline terminal image rendering


BACKEND_CYCLE_ORDER: list[ImageRenderBackend] = [
    ImageRenderBackend.AUTO,
    ImageRenderBackend.CHAFA,
    ImageRenderBackend.SIXEL,
    ImageRenderBackend.KITTY,
    ImageRenderBackend.HALF_BLOCK,
    ImageRenderBackend.BRAILLE,
    ImageRenderBackend.OFF,
]


class TerminalImageRenderer:
    """Renders email image attachments inside the terminal reading pane."""

    def __init__(self, backend: ImageRenderBackend | str = ImageRenderBackend.AUTO) -> None:
        if isinstance(backend, str):
            try:
                self.backend = ImageRenderBackend(backend)
            except ValueError:
                self.backend = ImageRenderBackend.AUTO
        else:
            self.backend = backend

        self.chafa_bin = shutil.which("chafa")
        self.img2sixel_bin = shutil.which("img2sixel")

    def cycle_backend(self) -> ImageRenderBackend:
        """Advance to the next image rendering mode and return it."""
        idx = BACKEND_CYCLE_ORDER.index(self.backend)
        self.backend = BACKEND_CYCLE_ORDER[(idx + 1) % len(BACKEND_CYCLE_ORDER)]
        return self.backend

    def set_backend(self, backend: ImageRenderBackend | str) -> ImageRenderBackend:
        """Set a specific image rendering backend."""
        if isinstance(backend, str):
            self.backend = ImageRenderBackend(backend)
        else:
            self.backend = backend
        return self.backend

    @property
    def label(self) -> str:
        """Human-readable label for the active image rendering backend."""
        labels = {
            ImageRenderBackend.AUTO: "Auto (Chafa/Half-Block)",
            ImageRenderBackend.CHAFA: "Chafa CLI",
            ImageRenderBackend.SIXEL: "DEC Sixel",
            ImageRenderBackend.KITTY: "Kitty Graphics",
            ImageRenderBackend.HALF_BLOCK: "24-bit Half-Block (▀)",
            ImageRenderBackend.BRAILLE: "Unicode Braille (⣿)",
            ImageRenderBackend.OFF: "Off",
        }
        return labels.get(self.backend, self.backend.value)

    def render_attachment(
        self,
        attachment: Attachment,
        theme: Theme,
        max_width: int = 44,
        max_height: int = 12,
    ) -> str:
        """Render an image attachment into a framed ANSI string for the reading pane."""
        if self.backend == ImageRenderBackend.OFF:
            return (
                Style()
                .foreground(theme.fg_muted)
                .italic(True)
                .render(f"  [Image preview disabled: {attachment.filename} ({attachment.human_size}) — press 'i' to enable]")
            )

        data = attachment.data
        if data is None and attachment.local_path and Path(attachment.local_path).exists():
            data = Path(attachment.local_path).read_bytes()

        w = max(16, min(56, max_width - 6))
        h = max(6, min(16, max_height))

        rendered_art = ""
        used_engine = self.backend.value

        # 1. Try Chafa CLI when backend is CHAFA or AUTO (if chafa is installed)
        if self.backend in (ImageRenderBackend.CHAFA, ImageRenderBackend.AUTO) and self.chafa_bin and data:
            chafa_out = self._run_chafa(data, attachment.filename, w, h)
            if chafa_out:
                rendered_art = chafa_out
                used_engine = "chafa"

        # 2. Try img2sixel CLI if SIXEL requested and img2sixel is installed
        if not rendered_art and self.backend == ImageRenderBackend.SIXEL and self.img2sixel_bin and data:
            sixel_out = self._run_img2sixel(data, attachment.filename, w * 8, h * 16)
            if sixel_out:
                rendered_art = sixel_out
                used_engine = "img2sixel"

        # 3. Use Espresso's native ImageViewer (supports PPM, BMP, Sixel, Kitty, Pillow, or procedural fallback)
        if not rendered_art:
            matrix = self._decode_to_rgb_matrix(data or b"", attachment.filename)
            proto_map = {
                ImageRenderBackend.AUTO: TerminalGraphicsProtocol.HALF_BLOCK,
                ImageRenderBackend.CHAFA: TerminalGraphicsProtocol.HALF_BLOCK,
                ImageRenderBackend.SIXEL: TerminalGraphicsProtocol.SIXEL,
                ImageRenderBackend.KITTY: TerminalGraphicsProtocol.KITTY,
                ImageRenderBackend.HALF_BLOCK: TerminalGraphicsProtocol.HALF_BLOCK,
                ImageRenderBackend.BRAILLE: TerminalGraphicsProtocol.BRAILLE,
            }
            proto = proto_map.get(self.backend, TerminalGraphicsProtocol.HALF_BLOCK)
            viewer = ImageViewer(
                pixels=matrix,
                width=w,
                height=h,
                protocol=proto,
                enable_native=(proto in (TerminalGraphicsProtocol.SIXEL, TerminalGraphicsProtocol.KITTY)),
                show_border=False,
            )
            rendered_art = viewer.view()
            if self.backend == ImageRenderBackend.CHAFA and not self.chafa_bin:
                used_engine = "half_block (chafa fallback)"
            else:
                used_engine = proto.value

        title = f" 🖼️ {attachment.filename} ({attachment.human_size}) [{used_engine}] "
        return (
            Style()
            .border(ROUNDED_BORDER)
            .border_foreground(theme.attachment_fg)
            .border_title(
                Style().bold(True).foreground(theme.attachment_fg).render(title),
                align=Align.LEFT,
            )
            .padding(0, 1)
            .render(rendered_art)
        )

    def _run_chafa(self, data: bytes, filename: str, width: int, height: int) -> str:
        """Invoke the `chafa` CLI tool to rasterize an image to ANSI/Sixel symbols."""
        if not self.chafa_bin:
            return ""
        suffix = Path(filename).suffix or ".ppm"
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
                tmp.write(data)
                tmp.flush()
                proc = subprocess.run(
                    [
                        self.chafa_bin,
                        f"--size={width}x{height}",
                        "--format=symbols",
                        tmp.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.rstrip("\n")
        except Exception:
            pass
        return ""

    def _run_img2sixel(self, data: bytes, filename: str, px_width: int, px_height: int) -> str:
        """Invoke `img2sixel` CLI if available on the host system."""
        if not self.img2sixel_bin:
            return ""
        suffix = Path(filename).suffix or ".ppm"
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
                tmp.write(data)
                tmp.flush()
                proc = subprocess.run(
                    [
                        self.img2sixel_bin,
                        "-w",
                        str(px_width),
                        "-h",
                        str(px_height),
                        tmp.name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3.0,
                    check=False,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout
        except Exception:
            pass
        return ""

    @staticmethod
    def _decode_to_rgb_matrix(
        data: bytes, filename: str
    ) -> list[list[tuple[int, int, int]]]:
        """Decode image bytes into a 2D RGB matrix using stdlib PPM/BMP, Pillow, or hash art."""
        ext = filename.lower()
        if data.startswith((b"P6", b"P3")) or ext.endswith((".ppm", ".pgm")):
            try:
                return parse_ppm(data)
            except Exception:
                pass
        if data.startswith(b"BM") or ext.endswith(".bmp"):
            try:
                return parse_bmp(data)
            except Exception:
                pass
        if data:
            try:
                import io
                from PIL import Image as PILImage

                img = PILImage.open(io.BytesIO(data)).convert("RGB")
                img.thumbnail((64, 36))
                w, h = img.size
                raw = img.tobytes()
                matrix: list[list[tuple[int, int, int]]] = []
                idx = 0
                for _ in range(h):
                    row: list[tuple[int, int, int]] = []
                    for _ in range(w):
                        row.append((raw[idx], raw[idx + 1], raw[idx + 2]))
                        idx += 3
                    matrix.append(row)
                return matrix
            except Exception:
                pass

        # Deterministic procedural gradient preview if binary format has no decoder installed
        digest = hashlib.sha256(data or filename.encode("utf-8")).digest()
        r0, g0, b0 = digest[0], digest[1], digest[2]
        r1, g1, b1 = digest[3], digest[4], digest[5]
        w, h = 28, 14
        fallback_matrix: list[list[tuple[int, int, int]]] = []
        for y in range(h):
            row = []
            for x in range(w):
                tx = x / max(1, w - 1)
                ty = y / max(1, h - 1)
                r = int(r0 * (1 - tx) + r1 * tx)
                g = int(g0 * (1 - ty) + g1 * ty)
                b = int(b0 * (1 - (tx + ty) / 2) + b1 * ((tx + ty) / 2))
                row.append((r, g, b))
            fallback_matrix.append(row)
        return fallback_matrix
