"""
Draw Hestia's logo into assets/hestia.png: a white brush-stroke H on black,
in the manner of Japanese ink calligraphy, with a small red name seal.
Deterministic (fixed seed), so running it again gives the same picture.

    venv\\Scripts\\python scripts\\make_logo.py
"""

import math
import os
import random
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QImage, QPainter, QPainterPath, QPen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "hestia.png")
SIZE = 1024
INK = QColor(246, 243, 236)  # warm white, like rice paper


def bezier(p0, p1, p2, t):
    return QPointF((1 - t) ** 2 * p0.x() + 2 * (1 - t) * t * p1.x() + t ** 2 * p2.x(),
                   (1 - t) ** 2 * p0.y() + 2 * (1 - t) * t * p1.y() + t ** 2 * p2.y())


def stroke(painter, rng, p0, p1, p2, width, press=1.16, dry_from=0.74):
    """
    One brush stroke along a curve: a heavy press where the brush lands, a
    body that thins a little, and a dry-brush tail where the bristles run
    out of ink one by one.
    """
    steps = 500
    points = [bezier(p0, p1, p2, i / steps) for i in range(steps + 1)]

    def half_width(t):
        landing = 1 + (press - 1) * math.exp(-((t / 0.07) ** 2))  # the press at the start
        body = 1.0 - 0.15 * t                                      # thins as ink is used
        lift = 1.0 if t < 0.9 else max(0.6, 1 - (t - 0.9) / 0.25)  # a lift, not a point
        return width / 2 * body * landing * lift

    # The wet part: overlapping footprints of a brush held at an angle, so
    # strokes start and end on a slant (the "nail head" entry), not round.
    painter.setPen(Qt.NoPen)
    painter.setBrush(INK)
    for i, point in enumerate(points):
        t = i / steps
        if t > dry_from:
            break
        # The wet core narrows into the dry tail, so the bristles show at the edges first.
        fade = 1.0 if t < dry_from - 0.12 else 1 - 0.5 * (t - (dry_from - 0.12)) / 0.12
        r = half_width(t) * fade * (1 + rng.uniform(-0.015, 0.015))
        painter.save()
        painter.translate(point)
        painter.rotate(-38)
        painter.drawEllipse(QPointF(0, 0), r * 1.08, r * 0.86)
        painter.restore()

    # The dry tail: bristles, each running out of ink at its own point.
    for _ in range(90):
        offset = rng.uniform(-1, 1)
        # Each bristle runs dry at its own point; edges a little sooner.
        end = dry_from + (1 - dry_from) * rng.uniform(0.35, 1.0) * (1 - 0.35 * abs(offset))
        pen = QPen(INK, width * rng.uniform(0.03, 0.07), Qt.SolidLine, Qt.FlatCap)
        painter.setPen(pen)
        path = QPainterPath()
        started = False
        for i, point in enumerate(points):
            t = i / steps
            if t < dry_from - 0.14:
                continue
            if t > min(end, 1.0):
                break
            a, b = points[max(i - 1, 0)], points[min(i + 1, steps)]
            dx, dy = b.x() - a.x(), b.y() - a.y()
            length = math.hypot(dx, dy) or 1
            normal = QPointF(-dy / length, dx / length)
            p = point + normal * (offset * half_width(t) * 0.92)
            if not started:
                path.moveTo(p)
                started = True
            elif rng.random() < 0.985:  # tiny skips, where the paper catches
                path.lineTo(p)
            else:
                path.moveTo(p)
        painter.drawPath(path)


def splatter(painter, rng, centre, spread, count, radius):
    painter.setPen(Qt.NoPen)
    painter.setBrush(INK)
    for _ in range(count):
        angle, distance = rng.uniform(0, 2 * math.pi), abs(rng.gauss(0, spread))
        r = radius * rng.uniform(0.3, 1.0)
        painter.drawEllipse(centre + QPointF(math.cos(angle), math.sin(angle)) * distance, r, r)


def seal(painter, rect):
    """A red hanko-style seal with 火 (fire — Hestia kept the hearth)."""
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(196, 40, 36))
    painter.drawRoundedRect(rect, rect.width() * 0.12, rect.width() * 0.12)
    family = "SimSun"
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\STKAITI.TTF")
    if font_id >= 0:
        family = QFontDatabase.applicationFontFamilies(font_id)[0]
    font = QFont(family)
    font.setPixelSize(int(rect.height() * 0.78))
    painter.setFont(font)
    painter.setPen(QColor(250, 240, 230))
    painter.drawText(rect, Qt.AlignCenter, "火")


def draw():
    rng = random.Random(7)
    image = QImage(SIZE, SIZE, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    # Black tile with soft corners, like a lacquered card.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(14, 14, 16))
    painter.drawRoundedRect(QRectF(0, 0, SIZE, SIZE), SIZE * 0.2, SIZE * 0.2)

    s = SIZE / 1024
    P = lambda x, y: QPointF(x * s, y * s)  # noqa: E731
    w = 118 * s
    # Left post: lands at the top, leans in slightly, dries out at the foot.
    stroke(painter, rng, P(330, 205), P(312, 520), P(322, 835), w)
    # Right post: a touch taller, with its own lean.
    stroke(painter, rng, P(700, 180), P(722, 500), P(698, 840), w * 0.96)
    # Crossbar: drawn left to right, rising, as a brush would.
    stroke(painter, rng, P(255, 545), P(520, 520), P(800, 470), w * 0.82, press=1.1, dry_from=0.8)
    # A few drops thrown off where the brush landed hardest.
    splatter(painter, rng, P(330, 190), 30 * s, 9, 9 * s)
    splatter(painter, rng, P(805, 465), 26 * s, 7, 7 * s)

    seal(painter, QRectF(808 * s, 808 * s, 132 * s, 132 * s))
    painter.end()
    return image


if __name__ == "__main__":
    app = QGuiApplication(sys.argv)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    draw().save(OUT)
    print(f"Saved {OUT}")
