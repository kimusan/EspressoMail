"""Terminal image rendering engine supporting Chafa, Sixel, Kitty, Half-Block, and Braille."""

from __future__ import annotations

import base64
import hashlib
import io
import re
import shutil
import subprocess
import sys
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

# In-memory cache of rasterized ANSI image boxes (cache_key -> str)
_RENDERED_BOX_CACHE: dict[tuple[str, int, int, int, str, str], str] = {}


def clear_image_caches() -> None:
    """Clear in-memory image download cache and rasterized ANSI box cache."""
    _REMOTE_IMAGE_CACHE.clear()
    _RENDERED_BOX_CACHE.clear()


def _import_pil_image():
    """Import PIL.Image, falling back to system dist-packages if running in an isolated venv."""
    try:
        from PIL import Image as PILImage
        return PILImage
    except ImportError:
        for sys_pkg in (
            "/usr/lib/python3/dist-packages",
            f"/usr/local/lib/python{sys.version_info.major}.{sys.version_info.minor}/dist-packages",
            f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}/dist-packages",
        ):
            if Path(sys_pkg).exists() and sys_pkg not in sys.path:
                sys.path.append(sys_pkg)
        try:
            from PIL import Image as PILImage
            return PILImage
        except ImportError:
            return None


class ImageRenderBackend(str, Enum):
    """Selectable terminal image rendering backends."""

    AUTO = "auto"              # Chafa if available, otherwise 24-bit TrueColor Half-Block
    CHAFA = "chafa"            # External `chafa` CLI tool (with TrueColor Half-Block fallback)
    SIXEL = "sixel"            # DEC Sixel graphics (`img2sixel` or Espresso native Sixel encoder)
    KITTY = "kitty"            # Kitty Graphics Protocol (APC)
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
    allow_blocking_fetch: bool = False,
) -> Attachment | None:
    """Resolve an HTML `<img src="...">` attribute (`cid:`, `data:`, or `http(s)://`) to an Attachment."""
    raw_src = re.sub(r"[\r\n\t]+", "", (src or "").strip())
    if not raw_src:
        return None
    if raw_src.startswith("//"):
        raw_src = "https:" + raw_src

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

        if allow_remote_fetch and allow_blocking_fetch:
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

        # Return a non-blocking pending placeholder Attachment (loads asynchronously via TEA Cmd)
        if allow_remote_fetch:
            return Attachment(
                id=f"{message_id}:web_img_pending:{idx}",
                message_id=message_id,
                filename=fname,
                content_type="image/pending",
                size_bytes=0,
                is_inline=True,
                data=None,
                content_id=raw_src,
            )

        return None


def _fetch_remote_image_bytes(url: str, timeout: float = 2.5, max_bytes: int = 4 * 1024 * 1024) -> bytes | None:
    """Download a remote image URL with a short timeout and verify it is not an HTML error page."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) EspressoMail/0.1",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = str(resp.headers.get("Content-Type", "") or "").lower()
            if "text/html" in ctype or "text/plain" in ctype:
                return None
            data = resp.read(max_bytes)
            if not data or len(data) < 24:
                return None
            head_strip = data[:64].lstrip().lower()
            if head_strip.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
                return None
            return data
    except Exception:
        return None


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

        if attachment.content_type == "image/pending" or (attachment.data is None and not attachment.local_path):
            title = f" 🖼️ {attachment.filename} "
            return (
                Style()
                .border(ROUNDED_BORDER)
                .border_foreground(theme.border_unfocused)
                .border_title(
                    Style().foreground(theme.fg_muted).render(title),
                    align=Align.LEFT,
                )
                .padding(0, 1)
                .render(
                    Style()
                    .italic(True)
                    .foreground(theme.fg_muted)
                    .render("  ⏳ Loading remote image in background...")
                )
            )

        data = attachment.data
        if data is None and attachment.local_path and Path(attachment.local_path).exists():
            try:
                data = Path(attachment.local_path).read_bytes()
            except Exception:
                data = None

        cache_id = attachment.id or (hashlib.sha256(data).hexdigest() if data else "")
        cache_key = (
            cache_id,
            len(data) if data else 0,
            max_width,
            max_height,
            self.backend.value,
            theme.id,
        )
        if cache_id and cache_key in _RENDERED_BOX_CACHE:
            return _RENDERED_BOX_CACHE[cache_key]

        matrix, orig_dims = self._decode_to_rgb_matrix(data or b"", attachment.filename)
        if skip_tiny_trackers and orig_dims is not None:
            ow, oh = orig_dims
            if ow <= 4 or oh <= 4:
                return ""

        w = max(18, min(60, max_width - 6))
        h = max(6, min(16, max_height))
        if orig_dims is not None and orig_dims[0] > 0 and orig_dims[1] > 0:
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

        # 2. Try img2sixel CLI if SIXEL requested and img2sixel is installed
        if not rendered_art and self.backend == ImageRenderBackend.SIXEL and self.img2sixel_bin and data:
            sixel_out = self._run_img2sixel(data, attachment.filename, w * 8, h * 16)
            if sixel_out:
                rendered_art = sixel_out
                used_engine = "img2sixel"

        # 3. Use Espresso's ImageViewer with the decoded RGB pixel matrix
        if not rendered_art:
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

        dim_str = f" {orig_dims[0]}×{orig_dims[1]}px," if orig_dims else ""
        title = f" 🖼️ {attachment.filename} ({dim_str.strip()} {attachment.human_size}) [{used_engine}] "
        res_box = (
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
        if cache_id:
            _RENDERED_BOX_CACHE[cache_key] = res_box
        return res_box

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

    def _run_img2sixel(self, data: bytes, filename: str, px_width: int, px_height: int) -> str:
        """Invoke `img2sixel` CLI if available on the host system."""
        if not self.img2sixel_bin:
            return ""
        suffix = Path(filename).suffix or ".png"
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
    ) -> tuple[list[list[tuple[int, int, int]]], tuple[int, int] | None]:
        """Decode image bytes into a 2D RGB matrix using PPM/BMP, Pillow (venv or system), or ImageMagick/ffmpeg."""
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

        # 1. Try Pillow (including automatic discovery of system dist-packages when inside an isolated venv)
        if data:
            PILImage = _import_pil_image()
            if PILImage is not None:
                try:
                    img = PILImage.open(io.BytesIO(data))
                    orig_w, orig_h = img.size

                    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                        rgba = img.convert("RGBA")
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

            # 2. Fallback to ImageMagick `convert` -> PPM (handles PNG/JPEG/GIF/WebP/SVG even without Pillow in venv)
            convert_bin = shutil.which("convert")
            if convert_bin:
                try:
                    proc = subprocess.run(
                        [
                            convert_bin,
                            "-",
                            "-background",
                            "#22242c",
                            "-flatten",
                            "-thumbnail",
                            "112x64",
                            "ppm:-",
                        ],
                        input=data,
                        capture_output=True,
                        timeout=3.0,
                        check=False,
                    )
                    if proc.returncode == 0 and proc.stdout.startswith(b"P6"):
                        mat = parse_ppm(proc.stdout)
                        if mat and mat[0]:
                            return mat, (len(mat[0]), len(mat))
                except Exception:
                    pass

            # 3. Fallback to `ffmpeg` -> PPM
            ffmpeg_bin = shutil.which("ffmpeg")
            if ffmpeg_bin:
                try:
                    proc = subprocess.run(
                        [
                            ffmpeg_bin,
                            "-loglevel",
                            "quiet",
                            "-i",
                            "pipe:0",
                            "-vf",
                            "scale='min(112,iw)':-1",
                            "-frames:v",
                            "1",
                            "-f",
                            "image2pipe",
                            "-vcodec",
                            "ppm",
                            "pipe:1",
                        ],
                        input=data,
                        capture_output=True,
                        timeout=3.0,
                        check=False,
                    )
                    if proc.returncode == 0 and proc.stdout.startswith(b"P6"):
                        mat = parse_ppm(proc.stdout)
                        if mat and mat[0]:
                            return mat, (len(mat[0]), len(mat))
                except Exception:
                    pass

        # Fallback gradient only if no binary image decoder succeeded
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
