"""Draws The Boss sprite into boss.ico (PNG-in-ICO, no extra libraries)."""
import os
import struct
import zlib

BASE = os.path.dirname(os.path.abspath(__file__))

PALETTE = {
    "H": (0x2b, 0x2d, 0x42), "h": (0xef, 0x23, 0x3c), "F": (0xf4, 0xa2, 0x61),
    "S": (0x14, 0x14, 0x1a), "s": (0xff, 0xff, 0xff), "C": (0xc8, 0xa1, 0x65),
    "c": (0x8d, 0x6e, 0x3f), "L": (0x3a, 0x3a, 0x3a), "M": (0x7a, 0x2e, 0x1f),
}
ROWS = [
    "....HHHHHH....",
    "....HHHHHH....",
    "...hhhhhhhh...",
    ".HHHHHHHHHHHH.",
    "..FFFFFFFFFF..",
    ".FSSSsFFSSSsF.",
    ".FSSSSFFSSSSF.",
    ".FFFFFFFFFFFF.",
    ".FFFFMFFMFFFF.",
    "..FFFFMMFFFF..",
    "..CCCccccCCC..",
    ".CCCCCcCCCCCC.",
    "FCCCCCcCCCCCCF",
    ".CccccccccccC.",
    ".CCCCCcCCCCCC.",
    "..LL......LL..",
]


def render(size):
    """RGBA rows: sprite (14x16) scaled to fit, centred on a transparent square."""
    scale = size // 16
    w, h = 14 * scale, 16 * scale
    ox, oy = (size - w) // 2, (size - h) // 2
    img = [[(0, 0, 0, 0)] * size for _ in range(size)]
    for r, row in enumerate(ROWS):
        for c, ch in enumerate(row):
            if ch == ".":
                continue
            rgb = PALETTE[ch]
            for y in range(oy + r * scale, oy + (r + 1) * scale):
                for x in range(ox + c * scale, ox + (c + 1) * scale):
                    img[y][x] = rgb + (255,)
    return img


def png(img):
    size = len(img)
    raw = b"".join(b"\x00" + b"".join(bytes(px) for px in row) for row in img)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main():
    sizes = [16, 32, 48, 64, 128, 256]
    images = [png(render(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for s, im in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(im), offset + len(data))
        data += im
    path = os.path.join(BASE, "boss.ico")
    with open(path, "wb") as f:
        f.write(header + entries + data)
    print("wrote", path)


if __name__ == "__main__":
    main()
