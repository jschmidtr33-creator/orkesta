# ruff: noqa: E501  (operadores PDF en una línea)
"""Generador de PDF propio (sin librerías ni servicios de terceros): texto en Helvetica con
tildes (WinAnsi), líneas, rectángulos de color y una imagen JPEG (la firma)."""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field

A4 = (595.28, 841.89)


_SUBST = str.maketrans(
    {
        "\u2212": "-",
        "\u2010": "-",
        "\u2011": "-",
        "\u00a0": " ",
        "\u202f": " ",
        "\u2009": " ",
        "\u2192": "->",
        "\u2713": "v",
    }
)


def _esc(text: str) -> bytes:
    raw = text.translate(_SUBST).encode("cp1252", errors="replace")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def _jpeg_size(data: bytes) -> tuple[int, int]:
    i = 2
    while i < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return int.from_bytes(data[i + 7 : i + 9], "big"), int.from_bytes(
                data[i + 5 : i + 7], "big"
            )
        i += 2 + int.from_bytes(data[i + 2 : i + 4], "big")
    raise ValueError("JPEG sin dimensiones")


@dataclass
class Page:
    ops: list[bytes] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


class PDF:
    def __init__(self) -> None:
        self.pages: list[Page] = [Page()]
        self.images: dict[str, tuple[bytes, int, int]] = {}
        self.y = A4[1] - 60
        self.margin = 56

    @property
    def page(self) -> Page:
        return self.pages[-1]

    def new_page(self) -> None:
        self.pages.append(Page())
        self.y = A4[1] - 60

    def _need(self, height: float) -> None:
        if self.y - height < 50:
            self.new_page()

    def text(self, value: str, *, size: float = 10, bold: bool = False, color: str = "#111827",
             x: float | None = None, width: float | None = None, gap: float = 1.35) -> None:  # fmt: skip
        """Párrafo con ajuste de línea."""
        font = "F2" if bold else "F1"
        avail = width or (A4[0] - 2 * self.margin)
        per_line = max(10, int(avail / (size * 0.5)))
        lines: list[str] = []
        for paragraph in str(value).split("\n"):
            words, current = paragraph.split(), ""
            for word in words:
                if len(current) + len(word) + 1 > per_line and current:
                    lines.append(current)
                    current = word
                else:
                    current = f"{current} {word}".strip()
            lines.append(current)
        r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
        for line in lines:
            self._need(size * gap)
            self.y -= size * gap
            self.page.ops.append(
                f"BT {r:.3f} {g:.3f} {b:.3f} rg /{font} {size} Tf {x or self.margin:.2f} "
                f"{self.y:.2f} Td (".encode()
                + _esc(line)
                + b") Tj ET"
            )

    def space(self, height: float) -> None:
        self.y -= height

    def rule(self, color: str = "#E5E7EB") -> None:
        r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
        self.y -= 6
        self.page.ops.append(f"{r:.3f} {g:.3f} {b:.3f} RG 0.8 w {self.margin} {self.y:.2f} m "
                             f"{A4[0] - self.margin:.2f} {self.y:.2f} l S".encode())  # fmt: skip
        self.y -= 6

    def band(self, color: str, height: float) -> None:
        """Franja de color de lado a lado en la parte superior de la página."""
        r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
        self.page.ops.append(f"{r:.3f} {g:.3f} {b:.3f} rg 0 {A4[1] - height:.2f} {A4[0]:.2f} "
                             f"{height:.2f} re f".encode())  # fmt: skip

    def image(self, jpeg: bytes, width: float) -> None:
        w, h = _jpeg_size(jpeg)
        name = f"Im{len(self.images) + 1}"
        self.images[name] = (jpeg, w, h)
        height = width * h / w
        self._need(height + 4)
        self.y -= height
        self.page.images.append(name)
        self.page.ops.append(f"q {width:.2f} 0 0 {height:.2f} {self.margin:.2f} {self.y:.2f} cm "
                             f"/{name} Do Q".encode())  # fmt: skip

    def build(self) -> bytes:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")  # se completa al final
        pages_id = add(b"")
        font1 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
        )
        font2 = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
        )
        image_ids = {}
        for name, (data, w, h) in self.images.items():
            image_ids[name] = add(
                f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB "
                f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode()
                + data
                + b"\nendstream"
            )
        kids = []
        for page in self.pages:
            content = zlib.compress(b"\n".join(page.ops))
            stream = add(f"<< /Length {len(content)} /Filter /FlateDecode >>\nstream\n".encode()
                         + content + b"\nendstream")  # fmt: skip
            xobjects = " ".join(f"/{n} {image_ids[n]} 0 R" for n in page.images)
            kids.append(add(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {A4[0]} {A4[1]}] "
                f"/Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> "
                f"/XObject << {xobjects} >> >> /Contents {stream} 0 R >>".encode()
            ))  # fmt: skip
        objects[catalog - 1] = f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode()
        objects[pages_id - 1] = (f"<< /Type /Pages /Count {len(kids)} /Kids ["
                                 + " ".join(f"{k} 0 R" for k in kids) + "] >>").encode()  # fmt: skip
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
        xref = len(out)
        out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
        out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n"
                f"{xref}\n%%EOF\n").encode()  # fmt: skip
        return bytes(out)


def strip_html(html: str) -> str:
    text = re.sub(r"<(br|/p|/li|/h[1-6])\s*/?>", "\n", html or "", flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    from html import unescape

    return re.sub(r"\n{3,}", "\n\n", unescape(text)).strip()
