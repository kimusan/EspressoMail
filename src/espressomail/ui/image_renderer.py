"""Terminal image rendering engine supporting Chafa, Sixel, Kitty, Half-Block, and Braille."""

from __future__ import annotations

import base64
import hashlib
import io
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from enum import Enum
from pathlib import Path

from espresso.beans import (
    ImageViewer,
    TerminalGraphicsProtocol,
    parse_bmp,
    parse_ppm,
)
from espresso.crema import ROUNDED_BORDER, Align, Style

from espressomail.models.entities import Attachment, detect_image_mime_type
from espressomail.themes.engine import Theme

# In-memory cache of fetched remote HTTP(S) image payloads (url -> bytes | None)
_REMOTE_IMAGE_CACHE: dict[str, bytes | None] = {}

# Classic 16-color DEC VT340 Sixel palette for Sixel mode inside split-pane viewports
_VT340_PALETTE: list[tuple[int, int, int]] = [
    (20, 20, 24),
    (51, 51, 204),
    (204, 33, 33),
    (51, 204, 51),
    (204, 51, 204),
    (51, 204, 204),
    (204, 204, 51),
    (155, 155, 155),
    (76, 76, 82),
    (96, 96, 175),
    (175, 76, 76),
    (96, 175, 96),
    (175, 96, 175),
    (96, 175, 175),
    (175, 175, 96),
    (230, 230, 230),
]


class ImageRenderBackend(str, Enum):
    """Selectable terminal image rendering backends."""

    AUTO = "auto"              # Chafa if available, otherwise 24-bit TrueColor Half-Block
    CHAFA = "chafa"            # External `chafa` CLI tool (with TrueColor Half-Block fallback)
    SIXEL = "sixel"            # DEC Sixel palette mode
    KITTY = "kitty"            # Kitty TrueColor HD block mode
    HALF_BLOCK = "half_block"  # 24-bit TrueColor Unicode half-blocks (▀)
    BRAILLE = "braille"        # 2x4 Unicode Braille dot matrix (⣿)
    OFF = "off"                # Disable inline terminal image rendering


BACKEND_CYCLE_ORDER: list[ImageRenderBackend] = [
    ImageRenderBackend.AUTO,
    ImageRenderBackend.CHAFA,
    ImageRenderBackend.SIXEL,
    ImageRenderBackend.KITTY,
    ImageRenderBackend.HALF_BLOCK,
    ImageRenderBackend.BRAILLE,
    ImageRenderBackend.OFF,
]


def is_likely_tracking_pixel_url(url: str, width_attr: str = "", height_attr: str = "") -> bool:
    """Return True if an HTML <img> tag attributes or URL indicate a 1x1 tracking pixel or spacer."""
    for dim in (width_attr, height_attr):
        clean_dim = re.sub(r"[^0-9]", "", str(dim or ""))
        if clean_dim and int(clean_dim) <= 3:
            return True
    lower = url.lower()
    tracking_markers = (
        "1x1",
        "spacer.gif",
        "pixel.gif",
        "transparent.gif",
        "blank.gif",
        "clear.gif",
        "/track/",
        "/tracker/",
        "/beacon",
        "open.aspx",
        "wf/open",
        "list-manage.com/track/open",
        "sendgrid.net/wf/open",
        "mailchimp.com/track",
    )
    return any(m in lower for m in tracking_markers)


def resolve_html_image_src(
    src: str,
    alt: str,
    attachments: list[Attachment],
    message_id: str = "",
    idx: int = 1,
    allow_remote_fetch: bool = True,
) -> Attachment | None:
    """Resolve an HTML `<img src="...">` attribute (`cid:`, `data:`, or `http(s)://`) to an Attachment."""
    raw_src = (src or "").strip()
    if not raw_src:
        return None

    # 1. Inline MIME Content-ID reference: cid:...
    if raw_src.lower().startswith("cid:"):
        cid_target = urllib.parse.unquote(raw_src[4:].strip().strip("<>"))
        cid_short = cid_target.split("@")[0].lower()
        for att in attachments:
            att_cid = (att.content_id or "").strip().strip("<>")
            if att_cid and (
                att_cid.lower() == cid_target.lower()
                or att_cid.split("@")[0].lower() == cid_short
            ):
                return att
            if att.filename and (
                att.filename.lower() == cid_target.lower()
                or att.filename.lower().startswith(cid_short)
            ):
                return att
        return None

    # 2. Inline base64 / data URI: data:image/png;base64,...
    if raw_src.lower().startswith("data:image/"):
        try:
            header, b64_payload = raw_src.split(",", 1)
            mime_part = header.split(";")[0][5:].lower()  # e.g. image/png
            ext = mime_part.split("/")[-1].split("+")[0] or "png"
            if ";base64" in header.lower():
                raw_bytes = base64.b64decode(b64_payload.strip())
            else:
                raw_bytes = urllib.parse.unquote_to_bytes(b64_payload)
            if len(raw_bytes) < 24:
                return None
            clean_alt = re.sub(r"[^a-zA-Z0-9._-]+", "_", (alt or "").strip()).strip("_")
            fname = f"{clean_alt[:28]}.{ext}" if clean_alt else f"inline_image_{idx}.{ext}"
            return Attachment(
                id=f"{message_id}:data_img:{idx}",
                message_id=message_id,
                filename=fname,
                content_type=mime_part,
                size_bytes=len(raw_bytes),
                is_inline=True,
                data=raw_bytes,
            )
        except Exception:
            return None

    # 3. Remote HTTP/HTTPS image URL
    if raw_src.lower().startswith(("https://", "http://")):
        parsed = urllib.parse.urlparse(raw_src)
        basename = Path(urllib.parse.unquote(parsed.path)).name
        clean_alt = re.sub(r"[^a-zA-Z0-9._-]+", "_", (alt or "").strip()).strip("_")
        fname = basename if (basename and "." in basename and len(basename) <= 42) else (
            f"{clean_alt[:28]}.png" if clean_alt else f"web_image_{idx}.png"
        )

        raw_bytes: bytes | None = None
        if raw_src in _REMOTE_IMAGE_CACHE:
            raw_bytes = _REMOTE_IMAGE_CACHE[raw_src]
        elif allow_remote_fetch:
            raw_bytes = _fetch_remote_image_bytes(raw_src)
            _REMOTE_IMAGE_CACHE[raw_src] = raw_bytes

        if not raw_bytes:
            return None

        sniffed_mime = detect_image_mime_type(raw_bytes) or "image/png"
        if "." not in fname:
            fname = f"{fname}.{sniffed_mime.split('/')[-1]}"

        return Attachment(
            id=f"{message_id}:web_img:{idx}",
            message_id=message_id,
            filename=fname,
            content_type=sniffed_mime,
            size_bytes=len(raw_bytes),
            is_inline=True,
            data=raw_bytes,
        )

    return None


def _fetch_remote_image_bytes(url: str, timeout: float = 2.5, max_bytes: int = 4 * 1024 * 1024) -> bytes | None:
    """Download a remote image URL with a short timeout and size cap."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) EspressoMail/0.1",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes)
            if data and len(data) >= 32:
                return data
    except Exception:
        return None
    return None


def _quantize_to_sixel_palette(
    matrix: list[list[tuple[int, int, int]]]
) -> list[list[tuple[int, int, int]]]:
    """Quantize an RGB matrix to the 16-color DEC VT340 Sixel palette."""
    out: list[list[tuple[int, int, int]]] = []
    for row in matrix:
        q_row: list[tuple[int, int, int]] = []
        for r, g, b in row:
            best = min(
                _VT340_PALETTE,
                key=lambda p: (r - p[0]) ** 2 + (g - p[1]) ** 2 + (b - p[2]) ** 2,
            )
            q_row.append(best)
        out.append(q_row)
    return out


class TerminalImageRenderer:
    """Renders email image attachments and inline HTML images inside the terminal reading pane."""

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
        max_width: int = 48,
        max_height: int = 12,
        skip_tiny_trackers: bool = False,
    ) -> str:
        """Render an image attachment into a framed ANSI string for the reading pane."""
        if self.backend == ImageRenderBackend.OFF:
            return (
                Style()
                .foreground(theme.fg_muted)
                .italic(True)
                .render(
                    f"  [Image preview disabled: {attachment.filename} ({attachment.human_size}) — press 'i' to enable]"
                )
            )

        data = attachment.data
        if data is None and attachment.local_path and Path(attachment.local_path).exists():
            try:
                data = Path(attachment.local_path).read_bytes()
            except Exception:
                data = None

        matrix, orig_dims = self._decode_to_rgb_matrix(data or b"", attachment.filename)
        if skip_tiny_trackers and orig_dims is not None:
            ow, oh = orig_dims
            if ow <= 4 or oh <= 4:
                return ""

        w = max(18, min(60, max_width - 6))
        h = max(6, min(16, max_height))
        if orig_dims is not None and orig_dims[0] > 0 and orig_dims[1] > 0:
            # Preserve approximate aspect ratio in terminal cells (cells are ~1:2 aspect ratio)
            aspect = orig_dims[0] / max(1, orig_dims[1])
            ideal_h = int(round((w / max(0.25, aspect)) * 0.5))
            h = max(5, min(max_height, ideal_h))

        rendered_art = ""
        used_engine = self.backend.value

        # 1. Try Chafa CLI when backend is CHAFA or AUTO (if chafa is installed)
        if self.backend in (ImageRenderBackend.CHAFA, ImageRenderBackend.AUTO) and self.chafa_bin and data:
            chafa_out = self._run_chafa(data, attachment.filename, w, h)
            if chafa_out:
                rendered_art = chafa_out
                used_engine = "chafa"

        # 2. Render using Espresso's cell-safe ImageViewer inside the 3-pane Viewport
        # Note: Raw multi-line Sixel/Kitty escape sequences inside a side-by-side split-pane
        # Viewport get overwritten by adjacent pane redraws or blank out on non-Sixel/Kitty
        # terminals, so we render cell-accurate representations for every mode.
        if not rendered_art:
            active_matrix = matrix
            if self.backend == ImageRenderBackend.BRAILLE:
                proto = TerminalGraphicsProtocol.BRAILLE
                used_engine = "braille"
            elif self.backend == ImageRenderBackend.SIXEL:
                active_matrix = _quantize_to_sixel_palette(matrix)
                proto = TerminalGraphicsProtocol.HALF_BLOCK
                used_engine = "sixel (16c)"
            elif self.backend == ImageRenderBackend.KITTY:
                proto = TerminalGraphicsProtocol.HALF_BLOCK
                used_engine = "kitty (24b)"
            elif self.backend == ImageRenderBackend.CHAFA:
                proto = TerminalGraphicsProtocol.HALF_BLOCK
                used_engine = "half_block (chafa fallback)"
            else:
                proto = TerminalGraphicsProtocol.HALF_BLOCK
                used_engine = "half_block"

            viewer = ImageViewer(
                pixels=active_matrix,
                width=w,
                height=h,
                protocol=proto,
                enable_native=False,
                show_border=False,
            )
            rendered_art = viewer.view()

        dim_str = f" {orig_dims[0]}×{orig_dims[1]}px," if orig_dims else ""
        title = f" 🖼️ {attachment.filename} ({dim_str.strip()} {attachment.human_size}) [{used_engine}] "
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
        """Invoke the `chafa` CLI tool to rasterize an image to ANSI symbols."""
        if not self.chafa_bin:
            return ""
        suffix = Path(filename).suffix or ".png"
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

    @staticmethod
    def _decode_to_rgb_matrix(
        data: bytes, filename: str
    ) -> tuple[list[list[tuple[int, int, int]]], tuple[int, int] | None]:
        """Decode image bytes into a 2D RGB matrix and original (width, height) dimensions."""
        ext = filename.lower()
        if data.startswith((b"P6", b"P3")) or ext.endswith((".ppm", ".pgm")):
            try:
                mat = parse_ppm(data)
                if mat and mat[0]:
                    return mat, (len(mat[0]), len(mat))
            except Exception:
                pass
        if data.startswith(b"BM") or ext.endswith(".bmp"):
            try:
                mat = parse_bmp(data)
                if mat and mat[0]:
                    return mat, (len(mat[0]), len(mat))
            except Exception:
                pass
        if data:
            try:
                from PIL import Image as PILImage

                img = PILImage.open(io.BytesIO(data))
                orig_w, orig_h = img.size

                # Handle transparency properly so dark logos on transparent PNGs remain visible
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    rgba = img.convert("RGBA")
                    # Sample average luminance of non-transparent pixels
                    raw_rgba = rgba.tobytes()
                    lum_sum = 0.0
                    opaque_cnt = 0
                    for i in range(0, len(raw_rgba), 16):
                        a = raw_rgba[i + 3]
                        if a > 64:
                            r_p, g_p, b_p = raw_rgba[i], raw_rgba[i + 1], raw_rgba[i + 2]
                            lum_sum += 0.299 * r_p + 0.587 * g_p + 0.114 * b_p
                            opaque_cnt += 1
                    avg_lum = (lum_sum / opaque_cnt) if opaque_cnt else 128.0
                    bg_color = (232, 228, 220, 255) if avg_lum < 75.0 else (34, 36, 44, 255)
                    canvas = PILImage.new("RGBA", rgba.size, bg_color)
                    img = PILImage.alpha_composite(canvas, rgba).convert("RGB")
                else:
                    img = img.convert("RGB")

                resample = getattr(getattr(PILImage, "Resampling", PILImage), "LANCZOS", PILImage.BICUBIC)
                img.thumbnail((112, 64), resample)
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
                if matrix and matrix[0]:
                    return matrix, (orig_w, orig_h)
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
        return fallback_matrix, None
