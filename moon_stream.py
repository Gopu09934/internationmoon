#!/usr/bin/env python3
"""
International Observe the Moon Night - interactive 24/7 YouTube live show.

Everything is generated in code - no video or audio files needed.

Scenes (rotating, ~8.5 minute cycle)
  LIVE       real-time Moon at its true phase, clock, countdowns, Sun-Earth-Moon diagram
  QUIZ       30s multiple-choice question, live chat voting (A/B/C), answer reveal
  CALENDAR   the next Moon phases with exact dates and countdowns
  TIMELAPSE  a whole lunar cycle in 30 seconds
  FACTS      animated "did you know" cards

Live chat commands (needs YOUTUBE_VIDEO_ID, see README)
  A / B / C   vote in the quiz
  !hello      your name appears on screen with a chime
  !fact       shows a random Moon fact on screen

Audio is synthesized in code too (no samples, nothing copied): an evolving pad and bells in a
random key each run, plus countdown ticks and reveal/welcome sounds. AUDIO_MODE=ambient|sfx|silent.

Usage
  YOUTUBE_STREAM_KEY=xxxx python3 moon_stream.py                 # go live
  python3 moon_stream.py --snapshot p.png --scene quiz --t 12    # preview a scene
  python3 moon_stream.py --snapshot p.png --at 2026-09-26T17:00  # preview a date (UTC)
Add --demo-chat to fake viewers (votes and shout-outs) for previews/tests.

Requires: ffmpeg, numpy, Pillow, DejaVu fonts (and pytchat for live chat).
"""
import argparse
import math
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 30
SR = 44100
SPF = SR // FPS  # audio samples per video frame
DURATION = int(os.environ.get("STREAM_SECONDS", 20400))  # 5h40m: under GitHub's 6h job limit
VIDEO_ID = os.environ.get("YOUTUBE_VIDEO_ID", "").strip()
START_OFFSET = float(os.environ.get("SHOW_START_OFFSET", 0))  # seconds into the cycle (testing)

SYNODIC = 29.530588853
J2000 = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)

# name, seconds
SCHEDULE = [("live", 120), ("quiz", 42), ("live", 90), ("calendar", 20),
            ("live", 90), ("timelapse", 30), ("live", 90), ("facts", 24)]
CYCLE = sum(d for _, d in SCHEDULE)
QUIZ_SECONDS = 30.0

TIPS = [
    "Look along the terminator, where light meets shadow, for the best detail.",
    "Even binoculars reveal craters, mountains and the dark lunar maria.",
    "The dark maria are ancient lava plains, not seas.",
    "The Moon always shows us the same face: it is tidally locked to Earth.",
    "Earthshine can faintly light the dark side of a crescent Moon.",
    "Cultures see a face, a rabbit or a toad in the Moon. What do you see?",
    "A half Moon shows sharper shadows than a full Moon.",
    "Give your eyes 15-20 minutes to adapt to the dark.",
    "Hold a phone to a telescope eyepiece to photograph the Moon.",
]

FACTS = [
    ("384,400 km", "average distance from Earth to the Moon"),
    ("1/6", "of Earth's gravity is what you feel on the Moon"),
    ("3.8 cm", "the Moon drifts away from Earth every year"),
    ("12", "people have walked on the Moon"),
    ("29.5 days", "from one New Moon to the next"),
    ("4.5 billion", "years old, likely born from a giant impact"),
    ("27%", "of Earth's width: the Moon is 3,474 km across"),
    ("-173 to 127 \u00b0C", "surface temperatures from lunar night to day"),
]

QUIZ = [
    {"q": "Why do we always see the same side of the Moon?",
     "o": ["It does not rotate at all", "It rotates once per orbit (tidal locking)", "Earth's gravity holds it still"],
     "a": 1, "why": "The Moon spins exactly once per orbit, so one face always points at Earth."},
    {"q": "What are the dark patches on the Moon called?",
     "o": ["Maria: ancient lava plains", "Oceans", "Shadow craters"],
     "a": 0, "why": "Maria (Latin for 'seas') are vast plains of solidified lava."},
    {"q": "About how far away is the Moon?",
     "o": ["38,400 km", "384,400 km", "3,844,000 km"],
     "a": 1, "why": "The average Earth-Moon distance is about 384,400 km."},
    {"q": "How many people have walked on the Moon?",
     "o": ["6", "12", "24"],
     "a": 1, "why": "Twelve astronauts walked on the Moon during Apollo 11 to 17."},
    {"q": "What is the line between the lit and dark part of the Moon called?",
     "o": ["The equator", "The horizon", "The terminator"],
     "a": 2, "why": "Craters look most dramatic along the terminator because of long shadows."},
    {"q": "About how long is one lunar cycle, New Moon to New Moon?",
     "o": ["7 days", "29.5 days", "365 days"],
     "a": 1, "why": "One full cycle of phases takes about 29.5 days."},
    {"q": "What is the faint glow on the dark part of a crescent Moon?",
     "o": ["Earthshine", "Moon lamps", "Aurora"],
     "a": 0, "why": "Earthshine is sunlight reflected off Earth onto the Moon's night side."},
    {"q": "Which phase comes right after First Quarter?",
     "o": ["Waning Crescent", "Waxing Gibbous", "Full Moon"],
     "a": 1, "why": "After First Quarter the lit part keeps growing: Waxing Gibbous."},
    {"q": "How strong is the Moon's gravity compared with Earth's?",
     "o": ["About 1/6", "About 1/2", "About 1/60"],
     "a": 0, "why": "You would weigh only about one sixth of your Earth weight."},
    {"q": "What causes most of Earth's ocean tides?",
     "o": ["The Sun alone", "The Moon's gravity (helped by the Sun)", "Ocean currents alone"],
     "a": 1, "why": "The Moon's gravity pulls on the oceans; the Sun adds a smaller pull."},
    {"q": "In the Northern Hemisphere, which side is lit on a waxing Moon?",
     "o": ["The right side", "The left side", "The top"],
     "a": 0, "why": "A waxing Moon grows from the right in the north (from the left in the south)."},
    {"q": "What is a 'Harvest Moon'?",
     "o": ["The full Moon nearest the autumn equinox", "A Moon that looks blue", "Any Moon seen in July"],
     "a": 0, "why": "It rises soon after sunset for several evenings in a row."},
]

FULL_MOON_NAMES = {1: "Wolf Moon", 2: "Snow Moon", 3: "Worm Moon", 4: "Pink Moon", 5: "Flower Moon",
                   6: "Strawberry Moon", 7: "Buck Moon", 8: "Sturgeon Moon", 9: "Harvest / Corn Moon",
                   10: "Hunter's Moon", 11: "Beaver Moon", 12: "Cold Moon"}

WHITE, SOFT, DIM = (240, 244, 255), (170, 185, 220), (120, 132, 165)
GOLD, GREEN, RED, ACCENT = (255, 214, 120), (110, 225, 150), (255, 125, 125), (130, 170, 255)


# ----------------------------------------------------------------------------
# Astronomy
# ----------------------------------------------------------------------------
def elongation(now):
    """Moon-Sun ecliptic longitude difference in degrees (0 = new, 180 = full).
    Low-precision Astronomical Almanac formulas: good to well under an hour of phase timing."""
    T = ((now - J2000).total_seconds() / 86400.0) / 36525.0
    sin, rad = math.sin, math.radians
    moon = (218.32 + 481267.881 * T
            + 6.29 * sin(rad(135.0 + 477198.87 * T)) - 1.27 * sin(rad(259.3 - 413335.36 * T))
            + 0.66 * sin(rad(235.7 + 890534.22 * T)) + 0.21 * sin(rad(269.9 + 954397.74 * T))
            - 0.19 * sin(rad(357.5 + 35999.05 * T)) - 0.11 * sin(rad(186.5 + 966404.03 * T)))
    g = 357.528 + 35999.050 * T
    sun = 280.460 + 36000.770 * T + 1.915 * sin(rad(g)) + 0.020 * sin(rad(2 * g))
    return (moon - sun) % 360.0


def moon_age(now):
    return elongation(now) / 360.0 * SYNODIC


def days_until(now, target_deg):
    """Days until the elongation next reaches target_deg (0 new, 90 first qtr, 180 full, 270 last qtr)."""
    d = ((target_deg - elongation(now)) % 360.0) / 12.19
    for _ in range(4):
        err = (target_deg - elongation(now + timedelta(days=d)) + 180.0) % 360.0 - 180.0
        d += err / 12.19
    return d


def phase_name(age):
    if age < 1.0 or age > SYNODIC - 1.0:
        return "New Moon"
    if age < 6.38:
        return "Waxing Crescent"
    if age < 8.38:
        return "First Quarter"
    if age < 13.77:
        return "Waxing Gibbous"
    if age < 15.77:
        return "Full Moon"
    if age < 21.15:
        return "Waning Gibbous"
    if age < 23.15:
        return "Last Quarter"
    return "Waning Crescent"


def fmt_days(d):
    hours = int(d * 24)
    return f"{hours // 24}d {hours % 24:02d}h"


def upcoming_phases(now, n=5):
    events = []
    for target, name in ((0, "New Moon"), (90, "First Quarter"), (180, "Full Moon"), (270, "Last Quarter")):
        d = days_until(now, target)
        events.append((d, target, name))
        events.append((d + 1 + days_until(now + timedelta(days=d + 1), target), target, name))
    events.sort()
    return events[:n]


# ----------------------------------------------------------------------------
# Drawing helpers
# ----------------------------------------------------------------------------
@lru_cache(maxsize=None)
def font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/" + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def T(d, xy, s, size, fill=WHITE, bold=False, anchor="lm", a=255):
    d.text(xy, s, font=font(size, bold), fill=tuple(fill) + (int(a),), anchor=anchor)


def rrect(d, box, radius, **kw):
    """Rounded rectangle that never raises: skips empty boxes, shrinks the radius for thin ones."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w < 1 or h < 1:
        return
    r = int(max(0, min(radius, w / 2 - 1, h / 2 - 1)))
    if r < 1:
        d.rectangle((x0, y0, x1, y1), **kw)
    else:
        d.rounded_rectangle((x0, y0, x1, y1), r, **kw)


def clamp01(x):
    return max(0.0, min(1.0, x))


def ease(x):
    x = clamp01(x)
    return x * x * (3 - 2 * x)


@lru_cache(maxsize=128)
def _mask(w, h, radius, alpha):
    m = Image.new("L", (w, h), 0)
    r = max(0, min(radius, w // 2 - 2, h // 2 - 2))
    if r < 1:
        ImageDraw.Draw(m).rectangle((0, 0, w - 1, h - 1), fill=alpha)
    else:
        ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=alpha)
    return m


def panel(img, box, alpha=150, radius=18, fill=(8, 14, 34)):
    x0, y0, x1, y1 = [int(v) for v in box]
    if x1 - x0 < 2 or y1 - y0 < 2 or alpha <= 0:
        return
    img.paste(fill, (x0, y0, x1, y1), _mask(x1 - x0, y1 - y0, radius, int(alpha)))


def wrap(text, size, max_w, bold=False):
    f = font(size, bold)
    lines, cur = [], ""
    for word in text.split():
        test = (cur + " " + word).strip()
        if f.getlength(test) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def phase_glyph(d, cx, cy, r, target):
    box = (cx - r, cy - r, cx + r, cy + r)
    lit = (238, 236, 224, 255)
    d.ellipse(box, fill=(30, 36, 58, 255), outline=(95, 110, 155, 255))
    if target == 180:
        d.ellipse(box, fill=lit)
    elif target == 90:
        d.pieslice(box, -90, 90, fill=lit)
    elif target == 270:
        d.pieslice(box, 90, 270, fill=lit)


def smooth_noise(w, h, cells, seed):
    """Smooth noise in [0,1], shape (h, w): white noise low-pass filtered in the frequency
    domain. cells = (rows, cols) is roughly how many features fit across the image."""
    rng = np.random.default_rng(seed)
    spec = np.fft.fft2(rng.standard_normal((h, w)))
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    sy, sx = h / cells[0] / 2.0, w / cells[1] / 2.0
    spec *= np.exp(-2 * np.pi ** 2 * ((sy * fy) ** 2 + (sx * fx) ** 2))
    n = np.fft.ifft2(spec).real.astype(np.float32)
    return (n - n.min()) / (n.max() - n.min() + 1e-9)


def make_albedo(R):
    """Procedural lunar surface: maria, fine texture, craters and a ray crater. (2R, 2R) in [0,1]."""
    S = 2 * R
    rng = np.random.default_rng(7)
    maria = 0.65 * smooth_noise(S, S, (4, 4), 1) + 0.35 * smooth_noise(S, S, (8, 8), 2)
    mare = np.clip((maria - 0.52) / 0.14, 0, 1)
    mare = mare * mare * (3 - 2 * mare)
    fine = smooth_noise(S, S, (60, 60), 3) + 0.6 * smooth_noise(S, S, (150, 150), 4)
    albedo = 0.68 - 0.30 * mare + 0.16 * (fine / 1.6 - 0.5)
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
    for _ in range(650):  # craters, lit from the upper left
        r = 2.0 + 30 * rng.random() ** 4.5
        cx, cy = rng.uniform(0, S, 2)
        if (cx - R) ** 2 + (cy - R) ** 2 > (R - r * 0.5) ** 2:
            continue
        x0, x1 = int(max(0, cx - r * 1.7)), int(min(S, cx + r * 1.7) + 1)
        y0, y1 = int(max(0, cy - r * 1.7)), int(min(S, cy + r * 1.7) + 1)
        dx = (xx[y0:y1, x0:x1] - cx) / r
        dy = (yy[y0:y1, x0:x1] - cy) / r
        dd = np.hypot(dx, dy)
        wall = np.exp(-((dd - 0.85) / 0.22) ** 2)
        albedo[y0:y1, x0:x1] += (-0.05 * np.clip(1 - dd ** 2, 0, 1)
                                 + 0.11 * wall * (dx + dy) / (dd * 1.414 + 1e-6)
                                 + 0.03 * np.exp(-((dd - 1.3) / 0.35) ** 2))
    cx, cy = R * 0.86, R * 1.52  # bright young crater with rays (Tycho-like)
    dx, dy = xx - cx, yy - cy
    dist = np.hypot(dx, dy)
    theta = np.arctan2(dy, dx)
    rays = np.clip(np.cos(13 * theta + 1.7 * np.sin(5 * theta)) - 0.55, 0, 1)
    albedo += 0.35 * rays * np.exp(-dist / (0.33 * R)) + 0.25 * np.exp(-((dist / 7.0) ** 2))
    return np.clip(albedo, 0.05, 1.0)


# ----------------------------------------------------------------------------
# Stage: starfield + Moon renderer
# ----------------------------------------------------------------------------
class Stage:
    R = 190
    CX, CY = W // 2, H // 2 + 10
    C = int(R * 3.4)
    NCYC = 120  # phases cached for the timelapse

    def __init__(self, prebuild=True):
        self.rng = np.random.default_rng(2026)
        self.albedo = make_albedo(self.R)
        self.base = self._background()
        self._prep_moon()
        self.cycle = None
        if prebuild:
            self.build_cycle()

    def _background(self):
        rng = self.rng
        y = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        top, bottom = np.array([2, 3, 10], np.float32), np.array([9, 14, 34], np.float32)
        bg = np.repeat(top + (bottom - top) * y ** 1.5, W, axis=1)
        h1, h2 = smooth_noise(W, H, (3, 5), 11), smooth_noise(W, H, (4, 6), 12)
        bg[..., 0] += 9 * h2 ** 2
        bg[..., 1] += 7 * h1 ** 2
        bg[..., 2] += 22 * h1 ** 2
        n = 1100
        xs, ys = rng.integers(0, W - 1, n), rng.integers(0, H - 1, n)
        bright = 45 + 210 * rng.random(n) ** 4
        tints = np.array([[1, 1, 1], [0.8, 0.88, 1], [1, 0.92, 0.78]], np.float32)
        col = tints[rng.integers(0, 3, n)] * bright[:, None]
        bg[ys, xs] = np.maximum(bg[ys, xs], col)
        big = bright > 190
        for dx, dy in ((1, 0), (0, 1), (1, 1)):
            bg[ys[big] + dy, xs[big] + dx] = np.maximum(bg[ys[big] + dy, xs[big] + dx], col[big] * 0.45)
        dist = np.hypot(xs - self.CX, ys - self.CY)
        idx = np.flatnonzero((dist > self.R * 1.9) & (bright > 70))[:240]
        self.tw_x, self.tw_y, self.tw_col = xs[idx], ys[idx], col[idx]
        self.tw_speed = rng.uniform(0.8, 3.0, len(idx))
        self.tw_phase = rng.uniform(0, 6.28, len(idx))
        return np.clip(bg, 0, 255).astype(np.uint8)

    def _prep_moon(self):
        R, C = self.R, self.C
        S = 2 * R
        yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
        self.nx = (xx - R + 0.5) / R
        ny = (yy - R + 0.5) / R
        r = np.sqrt(self.nx ** 2 + ny ** 2)
        self.z = np.sqrt(np.clip(1 - r * r, 0, 1))
        self.alpha = np.clip((1 - r) * R, 0, 1)[..., None]
        gy, gx = np.mgrid[0:C, 0:C].astype(np.float32)
        gr = np.hypot(gx - C / 2, gy - C / 2)
        glow = np.exp(-np.clip(gr - R, 0, None) / (0.30 * R)) * np.clip((C / 2 - gr) / 60, 0, 1)
        self.glow_unit = glow[..., None] * np.array([150, 175, 235], np.float32)
        self.x0, self.y0 = self.CX - C // 2, self.CY - C // 2
        self.base_patch = self.base[self.y0:self.y0 + C, self.x0:self.x0 + C].astype(np.float32)
        self.o = (C - S) // 2

    def patch(self, frac):
        """Moon + glow at phase frac (0 new, 0.5 full), composited onto the starfield patch."""
        th = 2 * math.pi * frac
        illum = (1 - math.cos(th)) / 2
        ndl = self.nx * math.sin(th) - self.z * math.cos(th)  # lit from the right when waxing
        lit = np.clip((ndl + 0.02) / 1.02, 0, 1)
        inten = self.albedo * (lit ** 0.35) * 1.12
        earth = 0.075 * self.albedo * (1 - lit)  # faint earthshine on the dark side
        col = np.stack([inten + earth * 0.7, inten * 0.985 + earth * 0.85, inten * 0.94 + earth * 1.1], -1) * 255
        region = self.base_patch + self.glow_unit * (0.05 + 0.22 * illum)
        o, S = self.o, 2 * self.R
        region[o:o + S, o:o + S] = region[o:o + S, o:o + S] * (1 - self.alpha) + col * self.alpha
        return np.clip(region, 0, 255).astype(np.uint8)

    def compose(self, patch):
        f = self.base.copy()
        f[self.y0:self.y0 + self.C, self.x0:self.x0 + self.C] = patch
        return f

    def build_cycle(self):
        self.cycle = [self.patch(i / self.NCYC) for i in range(self.NCYC)]


# ----------------------------------------------------------------------------
# Live chat (optional)
# ----------------------------------------------------------------------------
class Chat:
    """Reads YouTube live chat via pytchat (no API key). All failures are non-fatal."""

    def __init__(self, video_id):
        self.video_id = video_id
        self.enabled = bool(video_id)
        self.lock = threading.Lock()
        self.events = deque(maxlen=6)
        self.votes = {}
        self.quiz_open = False
        self.last_hello = {}
        self.last_fact = 0.0

    @staticmethod
    def clean(name):
        name = re.sub(r"\s+", " ", re.sub(r"[^\w .'\-]", "", name or "")).strip()[:20]
        return name or "friend"

    def handle(self, author, text):
        low = (text or "").strip().lower()
        who = self.clean(author)
        now = time.time()
        with self.lock:
            m = re.fullmatch(r"\(?([abc])[).!]?", low)
            if m and self.quiz_open:
                self.votes[author] = "abc".index(m.group(1))
            elif low.startswith(("!hello", "!hi")) and now - self.last_hello.get(author, 0) > 600:
                self.last_hello[author] = now
                self.events.append(("hello", who))
            elif low.startswith("!fact") and now - self.last_fact > 45:
                self.last_fact = now
                self.events.append(("fact", ""))

    def pop(self):
        with self.lock:
            return self.events.popleft() if self.events else None

    def start_question(self):
        with self.lock:
            self.votes = {}
            self.quiz_open = True

    def end_question(self):
        with self.lock:
            self.quiz_open = False

    def counts(self):
        with self.lock:
            c = [0, 0, 0]
            for v in self.votes.values():
                c[v] += 1
            return c

    def start(self):
        if self.enabled:
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            try:
                import pytchat
                chat = pytchat.create(video_id=self.video_id, interruptable=False)  # False: we are not the main thread
                print("Live chat connected", flush=True)
                while chat.is_alive():
                    for c in chat.get().sync_items():
                        self.handle(c.author.name, c.message)
                    time.sleep(1)
                print("Live chat ended, retrying", flush=True)
            except Exception as e:  # noqa: BLE001 - chat must never break the stream
                print(f"Live chat unavailable ({e!r}); retrying in 60s", flush=True)
            time.sleep(60)

    def demo(self):
        """Fake viewers, for previews and tests."""
        def loop():
            names = ["Aarav", "Maya", "Kenji", "Sofia", "Liam", "Noor", "Zoe", "Omar"]
            n = 0
            while True:
                time.sleep(random.uniform(0.3, 1.2))
                if self.quiz_open:
                    self.handle(f"viewer{random.randint(1, 60)}", random.choice("ABBC"))
                n += 1
                if n % 25 == 0:
                    self.handle(random.choice(names) + str(n), "!hello")
                if n % 70 == 0:
                    self.last_fact = 0
                    self.handle("x", "!fact")
        threading.Thread(target=loop, daemon=True).start()


# ----------------------------------------------------------------------------
# Generated audio
# ----------------------------------------------------------------------------
class Audio:
    """100% synthesized audio: every sound is a sine wave computed in this file - nothing is sampled,
    downloaded or copied, so there is no third-party recording to match.

    Each run picks a new random key and scale and composes an endless, non-repeating pad + bell texture,
    so the sound never loops and never matches a fixed track.

    AUDIO_MODE=ambient (default) | sfx (only quiz/welcome sounds, no background) | silent (no sound)"""

    SCALES = [[1, 6 / 5, 4 / 3, 3 / 2, 9 / 5],          # minor pentatonic
              [1, 9 / 8, 5 / 4, 3 / 2, 5 / 3],          # major pentatonic
              [1, 9 / 8, 6 / 5, 4 / 3, 3 / 2, 5 / 3, 16 / 9]]  # dorian

    def __init__(self, mode=None, seed=None):
        mode = (mode or os.environ.get("AUDIO_MODE", "ambient")).strip().lower()
        self.mode = mode if mode in ("ambient", "sfx", "silent") else "ambient"
        self.rng = random.Random(seed if seed is not None else int.from_bytes(os.urandom(8), "big"))
        self.root = 65.41 * 2 ** (self.rng.randrange(12) / 12)  # random key, C2..B2
        self.scale = self.rng.choice(self.SCALES)
        self.n = 0
        self.voices = []  # short bells / effects
        self.pads = []    # long evolving pad notes
        self.next_pad = int(self.rng.uniform(6, 12) * SR)
        self.next_bell = int(self.rng.uniform(3, 6) * SR)
        if self.mode == "ambient":
            for _ in range(4):  # start mid-texture, not from silence
                self._pad(offset=self.rng.uniform(0, 20))

    # -- building blocks ---------------------------------------------------------
    def _note(self, oct_lo, oct_hi, fmax=1e9):
        while True:
            f = self.root * self.rng.choice(self.scale) * 2 ** self.rng.randint(oct_lo, oct_hi)
            if f <= fmax:
                return f

    def _pad(self, offset=0.0):
        self.pads.append({"s": self.n - int(offset * SR), "dur": self.rng.uniform(24, 44),
                          "f": self._note(0, 2), "a": 0.09, "pan": self.rng.uniform(0.3, 0.7)})

    def _voice(self, delay, freq, amp, tau, pan=0.5):
        self.voices.append({"s": self.n + int(delay * SR), "f": freq, "a": amp, "tau": tau, "pan": pan})

    def sfx(self, kind):
        if self.mode == "silent":
            return
        r, sc = self.root * 8, self.scale
        if kind == "tick":
            self._voice(0, 1500, 0.16, 0.03)
        elif kind == "tock":
            self._voice(0, 2200, 0.22, 0.09)
        elif kind == "reveal":
            for i, f in enumerate((r, r * sc[2], r * sc[3], r * 2)):
                self._voice(i * 0.12, f, 0.14, 0.9, 0.3 + 0.13 * i)
        elif kind == "hello":
            self._voice(0, r * sc[3], 0.14, 0.8, 0.4)
            self._voice(0.12, r * 2 * sc[1], 0.12, 1.0, 0.6)
        elif kind == "scene":
            self._voice(0, r * sc[2], 0.08, 1.4, 0.5)

    # -- one video frame worth of audio (1/30 s) -----------------------------------
    def chunk(self):
        if self.mode == "silent":
            self.n += SPF
            return bytes(SPF * 4)  # stereo s16 silence (YouTube still gets a valid audio track)
        idx = np.arange(self.n, self.n + SPF, dtype=np.int64)
        left, right = np.zeros(SPF), np.zeros(SPF)

        if self.mode == "ambient":
            if self.n >= self.next_pad:
                self._pad()
                self.next_pad = self.n + int(self.rng.uniform(8, 16) * SR)
            if self.n >= self.next_bell:
                self._voice(0, self._note(3, 4, 1400), 0.09, 1.8, self.rng.uniform(0.2, 0.8))
                self.next_bell = self.n + int(self.rng.uniform(4, 10) * SR)
            keep = []
            for p in self.pads:
                u = (idx - p["s"]) / (p["dur"] * SR)
                env = np.where((u >= 0) & (u <= 1), np.sin(np.pi * np.clip(u, 0, 1)) ** 2, 0.0)
                tt = np.maximum((idx - p["s"]) / SR, 0.0)
                left += p["a"] * env * (np.sin(2 * np.pi * p["f"] * tt) + 0.35 * np.sin(4 * np.pi * p["f"] * tt))
                right += p["a"] * env * (np.sin(2 * np.pi * p["f"] * 1.003 * tt) + 0.35 * np.sin(4 * np.pi * p["f"] * 1.003 * tt))
                if (self.n - p["s"]) / SR < p["dur"]:
                    keep.append(p)
            self.pads = keep

        alive = []
        for v in self.voices:
            tt = np.maximum((idx - v["s"]) / SR, 0.0)
            env = np.exp(-tt / v["tau"]) * (1 - np.exp(-tt / 0.004))
            sig = v["a"] * env * (np.sin(2 * np.pi * v["f"] * tt) + 0.3 * np.sin(2 * np.pi * 2 * v["f"] * tt))
            left = left + sig * (1 - v["pan"]) * 1.4
            right = right + sig * v["pan"] * 1.4
            if (self.n - v["s"]) / SR < 6 * v["tau"] + 0.5:
                alive.append(v)
        self.voices = alive
        self.n += SPF
        out = np.clip(np.stack([left, right], 1), -0.95, 0.95)
        return (out * 32767).astype("<i2").tobytes()


# ----------------------------------------------------------------------------
# The show
# ----------------------------------------------------------------------------
class Show:
    def __init__(self, fixed_time=None, force=None, prebuild=True, chat=None):
        self.fixed_time = fixed_time
        self.force = force  # (scene, local_seconds) for previews
        self.stage = Stage(prebuild=prebuild)
        self.audio = Audio()
        self.chat = chat or Chat("")
        self.streaks = []
        self.frame_no = 0
        self.last_sec = None
        self.moon_key = None
        self.cur = None
        self.banner = None
        self._last_err = 0.0
        self.q_off = random.randrange(len(QUIZ))
        self.f_off = random.randrange(len(FACTS))
        self.cta = ["Like the stream and subscribe for more skywatching",
                    "Where are you watching from? Tell us in chat!",
                    "Share this stream with a friend and look up together",
                    "Step outside tonight and find the Moon  #ObserveTheMoon"]
        if self.chat.enabled:
            self.cta[1:1] = ["Type !hello in chat for a shout-out on screen"]
            self.cta.insert(4, "Type !fact in chat for a random Moon fact")

    # -- time & scene bookkeeping ---------------------------------------------------
    def now(self):
        return self.fixed_time or datetime.now(timezone.utc)

    def _scene(self, t):
        if self.force:
            name, tl = self.force
            return name, tl, dict(SCHEDULE)[name], 0
        t += START_OFFSET
        cyc = int(t // CYCLE)
        t %= CYCLE
        for name, dur in SCHEDULE:
            if t < dur:
                return name, t, dur, cyc
            t -= dur

    def _per_second(self, now, sec):
        self.last_sec = sec
        self.elong = elongation(now)
        self.frac = self.elong / 360.0
        self.age = self.frac * SYNODIC
        self.illum = (1 - math.cos(2 * math.pi * self.frac)) / 2
        self.full_in = days_until(now, 180.0)
        self.new_in = days_until(now, 0.0)
        if sec // 30 != self.moon_key:
            self.moon_key = sec // 30
            self.live_frame = self.stage.compose(self.stage.patch(self.frac))
            self.dim_frame = (self.live_frame.astype(np.float32) * 0.35).astype(np.uint8)

    def _enter(self, scene, cyc, now):
        self.audio.sfx("scene")
        if scene == "quiz":
            self.qi = (self.q_off + cyc) % len(QUIZ)
            self.revealed, self.tick = False, None
            self.chat.start_question()
        elif scene == "calendar":
            self.events = upcoming_phases(now)
        elif scene == "facts":
            self.fi0 = (self.f_off + cyc * 3) % len(FACTS)

    # -- per-frame ------------------------------------------------------------------
    def _twinkle(self, frame, t, scale):
        s = self.stage
        fac = (0.65 + 0.35 * np.sin(t * s.tw_speed + s.tw_phase)) * scale
        frame[s.tw_y, s.tw_x] = (s.tw_col * fac[:, None]).astype(np.uint8)

    def _streaks(self, frame, t):
        if random.random() < 1 / (FPS * 25):
            side = random.choice((-1, 1))
            self.streaks.append({"t0": t, "x": random.uniform(120, 380) if side < 0 else random.uniform(900, 1160),
                                 "y": random.uniform(30, 120), "dx": side * random.uniform(140, 240),
                                 "dy": random.uniform(60, 130), "dur": random.uniform(0.6, 1.0)})
        alive = []
        for s in self.streaks:
            age = t - s["t0"]
            if age > s["dur"]:
                continue
            alive.append(s)
            head = age / s["dur"]
            for k in range(16):
                u = head - k * 0.02
                if u < 0:
                    break
                x, y = int(s["x"] + s["dx"] * u), int(s["y"] + s["dy"] * u)
                if 0 <= x < W - 1 and 0 <= y < H - 1:
                    v = 255 * (1 - k / 16) * (1 - head ** 3)
                    frame[y:y + 2, x:x + 2] = np.maximum(frame[y:y + 2, x:x + 2],
                                                         np.array([v * 0.9, v * 0.95, v], np.uint8))
        self.streaks = alive

    def video(self):
        now = self.now()
        sec = int(now.timestamp())
        if sec != self.last_sec:
            self._per_second(now, sec)
        t = self.frame_no / FPS
        scene, tl, dur, cyc = self._scene(t)
        if self.cur != (scene, cyc):
            self.cur = (scene, cyc)
            self._enter(scene, cyc, now)

        st = self.stage
        if scene == "live":
            frame = self.live_frame.copy()
        elif scene == "timelapse":
            p = clamp01(tl / dur)
            frame = st.compose(st.cycle[int(((self.frac + p) % 1.0) * st.NCYC) % st.NCYC])
        else:
            frame = self.dim_frame.copy()
        self._twinkle(frame, t, 1.0 if scene in ("live", "timelapse") else 0.35)
        if scene in ("live", "timelapse"):
            self._streaks(frame, t)

        img = Image.fromarray(frame)
        d = ImageDraw.Draw(img, "RGBA")
        T(d, (W // 2, 50), "HARVEST MOON 2026 LIVE", 40, WHITE, True, "mm")
        for step in (lambda: getattr(self, "_draw_" + scene)(d, img, now, t, tl, dur),
                     lambda: self._draw_banner(d, img, t, scene),
                     lambda: self._draw_ribbon(d, img, t)):
            try:
                step()
            except Exception as e:  # noqa: BLE001 - keep streaming, log at most once a minute
                if time.time() - self._last_err > 60:
                    self._last_err = time.time()
                    print(f"[warn] draw error in scene '{scene}': {e!r}", flush=True)
        T(d, (W - 18, H - 14), "Northern hemisphere view  -  phases computed from Sun/Moon positions", 13, DIM, False, "rm")
        self.frame_no += 1
        return img.tobytes()

    def render(self):
        return self.video(), self.audio.chunk()

    # -- shared overlays ------------------------------------------------------------
    def _sub(self, d, text):
        T(d, (W // 2, 95), text, 22, SOFT, False, "mm")

    def _draw_ribbon(self, d, img, t):
        period = 8.0
        txt = self.cta[int(t // period) % len(self.cta)]
        ph = t % period
        a = min(1.0, ph / 0.6, (period - ph) / 0.6)
        w = font(22, True).getlength(txt)
        panel(img, (W / 2 - w / 2 - 30, 651, W / 2 + w / 2 + 30, 687), alpha=int(125 * a), radius=18)
        T(d, (W // 2, 669), txt, 22, GOLD, True, "mm", int(255 * a))

    def _draw_banner(self, d, img, t, scene):
        if self.banner is None and scene != "quiz":
            ev = self.chat.pop()
            if ev:
                kind, who = ev
                if kind == "hello":
                    text = f"Welcome, {who}!  Thanks for watching"
                else:
                    num, cap = random.choice(FACTS)
                    text = f"Moon fact:  {num}  {cap}"
                self.audio.sfx("hello")
                self.banner = {"text": text, "t0": t, "dur": 8.0}
        b = self.banner
        if not b:
            return
        age = t - b["t0"]
        if age > b["dur"]:
            self.banner = None
            return
        a = min(1.0, ease(age / 0.5), (b["dur"] - age) / 0.6)
        y = 138 - (1 - ease(age / 0.5)) * 30
        w = min(font(24, True).getlength(b["text"]), W - 140)
        panel(img, (W / 2 - w / 2 - 28, y - 22, W / 2 + w / 2 + 28, y + 22), alpha=int(190 * a), radius=22, fill=(20, 36, 84))
        rrect(d, (W / 2 - w / 2 - 28, y - 22, W / 2 + w / 2 + 28, y + 22), 22,
                            outline=ACCENT + (int(200 * a),), width=2)
        T(d, (W // 2, y), b["text"], 24, WHITE, True, "mm", int(255 * a))

    # -- scene: LIVE ---------------------------------------------------------------
    def _draw_live(self, d, img, now, t, tl, dur):
        s = self.stage
        self._sub(d, "24/7 LIVE  -  LOOK UP, WHEREVER YOU ARE")
        cy = s.CY
        # pulsing ring around the Moon
        ph = (t % 6.0) / 6.0
        rr = s.R + 8 + ph * 70
        d.ellipse((s.CX - rr, cy - rr, s.CX + rr, cy + rr), outline=(150, 175, 235, int(90 * (1 - ph))), width=2)
        # left panel
        T(d, (60, cy - 40), phase_name(self.age), 32, WHITE, True)
        T(d, (60, cy + 5), f"{self.illum * 100:.0f}% illuminated", 24, SOFT)
        T(d, (60, cy + 42), f"Moon age {self.age:.1f} days", 20, DIM)
        rrect(d, (60, cy + 68, 300, cy + 76), 4, fill=(255, 255, 255, 35))
        rrect(d, (60, cy + 68, 60 + 240 * self.illum, cy + 76), 4, fill=GOLD + (230,))
        # right panel
        rx = W - 60
        T(d, (rx, cy - 45), now.strftime("%H:%M:%S") + " UTC", 34, WHITE, True, "rm")
        T(d, (rx, cy - 5), now.strftime("%A %d %B %Y"), 20, SOFT, False, "rm")
        T(d, (rx, cy + 35), f"Full Moon in {fmt_days(self.full_in)}", 22, SOFT, False, "rm")
        T(d, (rx, cy + 68), f"New Moon in {fmt_days(self.new_in)}", 20, DIM, False, "rm")
        self._draw_orbit(d)
        # tip
        period = 20.0
        ph = t % period
        a = min(1.0, ph / 0.8, (period - ph) / 0.8)
        T(d, (W // 2, 612), TIPS[int(t // period) % len(TIPS)], 22, (200, 210, 235), False, "mm", int(255 * a))

    def _draw_orbit(self, d):
        cx, cy, r = 165, 526, 50
        T(d, (cx, 462), "WHY THE PHASES CHANGE", 13, DIM, True, "mm")
        d.ellipse((40, cy - 11, 62, cy + 11), fill=GOLD + (255,))
        for dy in (-32, -11, 11, 32):
            d.line((74, cy + dy, 104, cy + dy), fill=GOLD + (110,), width=1)
        T(d, (51, cy + 26), "SUN", 12, GOLD, True, "mm")
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(120, 135, 175, 120), width=1)
        d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=(70, 130, 230, 255))
        th = math.radians(180 + self.elong)  # counter-clockwise from the Sun direction
        mx, my = cx + r * math.cos(th), cy - r * math.sin(th)
        box = (mx - 8, my - 8, mx + 8, my + 8)
        d.ellipse(box, fill=(35, 40, 60, 255))
        d.pieslice(box, 90, 270, fill=(240, 240, 225, 255))  # the Sun always lights the left half
        T(d, (cx, cy + r + 20), "Moon's orbit seen from above", 12, DIM, False, "mm")

    # -- scene: QUIZ ---------------------------------------------------------------
    def _draw_quiz(self, d, img, now, t, tl, dur):
        q = QUIZ[self.qi]
        self._sub(d, f"MOON QUIZ  -  QUESTION {self.qi + 1}")
        panel(img, (130, 135, 1150, 570), alpha=175, radius=24)
        y = 190
        for ln in wrap(q["q"], 34, 900, True):
            T(d, (W // 2, y), ln, 34, WHITE, True, "mm")
            y += 44
        rem = QUIZ_SECONDS - tl
        revealed = rem <= 0
        counts, total = self.chat.counts(), sum(self.chat.counts())
        if not revealed:  # countdown ticks
            sec_left = int(math.ceil(rem))
            if sec_left <= 5 and sec_left != self.tick:
                self.tick = sec_left
                self.audio.sfx("tock" if sec_left == 1 else "tick")
        elif not self.revealed:
            self.revealed = True
            self.chat.end_question()
            self.audio.sfx("reveal")
        for i, opt in enumerate(q["o"]):
            x0, y0 = 170 + i * 320, 295
            correct = revealed and i == q["a"]
            faded = revealed and i != q["a"]
            fill = (30, 110, 70) if correct else (28, 40, 84)
            panel(img, (x0, y0, x0 + 300, y0 + 125), alpha=90 if faded else 185, radius=16, fill=fill)
            rrect(d, (x0, y0, x0 + 300, y0 + 125), 16,
                                outline=(GREEN if correct else ACCENT) + (255 if correct else (60 if faded else 150),), width=3)
            d.ellipse((x0 + 12, y0 + 12, x0 + 46, y0 + 46), fill=(GREEN if correct else ACCENT) + (255 if not faded else 90,))
            T(d, (x0 + 29, y0 + 29), "ABC"[i], 20, (10, 16, 36), True, "mm")
            lines = wrap(opt, 21, 225)
            ty = y0 + 62 - (len(lines) - 1) * 13
            for ln in lines:
                T(d, (x0 + 58, ty), ln, 21, WHITE, False, "lm", 110 if faded else 255)
                ty += 27
            if self.chat.enabled:
                label = (f"{counts[i] * 100 // total}%  ({counts[i]})" if revealed and total else f"{counts[i]} votes")
                T(d, (x0 + 150, y0 + 112), label, 15, SOFT, False, "mm", 120 if faded else 230)
        if not revealed:
            bx0, bx1, by = 170, 1110, 445
            rrect(d, (bx0, by, bx1, by + 12), 6, fill=(255, 255, 255, 40))
            rrect(d, (bx0, by, bx0 + (bx1 - bx0) * clamp01(rem / QUIZ_SECONDS), by + 12), 6,
                                fill=(RED if rem < 8 else ACCENT) + (240,))
            msg = ("Type A, B or C in chat!" if self.chat.enabled else "Think you know it?")
            T(d, (W // 2, 493), f"{msg}   {int(math.ceil(rem))}s", 26, GOLD, True, "mm")
        else:
            T(d, (W // 2, 462), f"Answer: {'ABC'[q['a']]}", 30, GREEN, True, "mm")
            yy = 505
            for ln in wrap(q["why"], 23, 880):
                T(d, (W // 2, yy), ln, 23, WHITE, False, "mm")
                yy += 30

    # -- scene: CALENDAR -----------------------------------------------------------
    def _draw_calendar(self, d, img, now, t, tl, dur):
        self._sub(d, "NEXT MOON PHASES  (UTC)")
        panel(img, (190, 135, 1090, 600), alpha=170, radius=24)
        for i, (days, target, name) in enumerate(self.events):
            a = ease((tl - i * 0.25) / 0.5)
            y = 190 + i * 82
            when = now + timedelta(days=days)
            phase_glyph(d, 262, y, 26, target)
            T(d, (312, y - 12), name, 30, WHITE, True, "lm", int(255 * a))
            if target == 180:
                T(d, (312, y + 22), FULL_MOON_NAMES[when.month] + " (traditional name)", 17, GOLD, False, "lm", int(255 * a))
            T(d, (1050, y - 12), when.strftime("%a %d %b   %H:%M"), 26, WHITE, False, "rm", int(255 * a))
            T(d, (1050, y + 20), f"in {fmt_days(days)}", 20, SOFT, False, "rm", int(255 * a))

    # -- scene: TIMELAPSE ----------------------------------------------------------
    def _draw_timelapse(self, d, img, now, t, tl, dur):
        self._sub(d, "MOON CYCLE TIMELAPSE  -  29.5 DAYS IN 30 SECONDS")
        s = self.stage
        p = clamp01(tl / dur)
        frac = (self.frac + p) % 1.0
        age = frac * SYNODIC
        cy = s.CY
        T(d, (60, cy - 30), phase_name(age), 32, WHITE, True)
        T(d, (60, cy + 12), f"Day {age:4.1f} of 29.5", 24, SOFT)
        rx = W - 60
        T(d, (rx, cy - 30), (now + timedelta(days=p * SYNODIC)).strftime("%d %b %Y"), 30, WHITE, True, "rm")
        T(d, (rx, cy + 10), "simulated date", 20, DIM, False, "rm")
        x0, x1, y = 190, 1090, 596
        rrect(d, (x0, y - 3, x1, y + 3), 3, fill=(255, 255, 255, 60))
        for k, lab in enumerate(("New", "First Quarter", "Full", "Last Quarter", "New")):
            x = x0 + (x1 - x0) * k / 4
            d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(200, 210, 235, 200))
            T(d, (x, y + 22), lab, 14, DIM, False, "mm")
        px = x0 + (x1 - x0) * frac
        d.ellipse((px - 9, y - 9, px + 9, y + 9), fill=GOLD + (255,), outline=(255, 255, 255, 255), width=2)

    # -- scene: FACTS --------------------------------------------------------------
    def _draw_facts(self, d, img, now, t, tl, dur):
        self._sub(d, "DID YOU KNOW?")
        panel(img, (190, 165, 1090, 545), alpha=155, radius=26)
        per = dur / 3.0
        k = min(2, int(tl // per))
        lt = tl - k * per
        a = min(1.0, lt / 0.6, (per - lt) / 0.6)
        slide = (1 - ease(lt / 0.6)) * 26
        num, cap = FACTS[(self.fi0 + k) % len(FACTS)]
        T(d, (W // 2, 300 + slide), num, 92, GOLD, True, "mm", int(255 * a))
        yy = 390 + slide
        for ln in wrap(cap, 30, 780):
            T(d, (W // 2, yy), ln, 30, WHITE, False, "mm", int(255 * a))
            yy += 40
        for i in range(3):
            x = W // 2 + (i - 1) * 30
            d.ellipse((x - 6, 505 - 6, x + 6, 505 + 6), fill=(GOLD if i == k else (120, 132, 165)) + (230,))


# ----------------------------------------------------------------------------
# Streaming
# ----------------------------------------------------------------------------
def ffmpeg_cmd(output_url, seconds, audio_fd):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-re", "-probesize", "32", "-analyzeduration", "0", "-thread_queue_size", "64", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
        "-framerate", str(FPS), "-i", "pipe:0",
        "-re", "-probesize", "32", "-analyzeduration", "0", "-thread_queue_size", "512", "-f", "s16le", "-ar", str(SR), "-ac", "2", "-i", f"pipe:{audio_fd}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-af", "aecho=0.8:0.55:520|940:0.30|0.20",
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "3000k", "-maxrate", "3000k", "-bufsize", "6000k",
        "-pix_fmt", "yuv420p", "-g", str(FPS * 2), "-keyint_min", str(FPS * 2),
        "-c:a", "aac", "-b:a", "128k", "-ar", str(SR),
        "-t", str(int(seconds)),
        "-f", "flv", output_url,
    ]


def run_segment(show, output_url, seconds):
    """Feed one ffmpeg process: video frames on stdin, generated audio on a second pipe.
    Audio is written from its own thread (with a buffer) so neither pipe can starve the other."""
    r, w = os.pipe()
    proc = subprocess.Popen(ffmpeg_cmd(output_url, seconds, r), stdin=subprocess.PIPE, pass_fds=(r,))
    os.close(r)
    aq = queue.Queue(maxsize=450)  # ~15 s of audio

    def audio_writer():
        f = os.fdopen(w, "wb")
        try:
            while True:
                chunk = aq.get()
                if chunk is None:
                    break
                f.write(chunk)
                f.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                f.close()
            except OSError:
                pass

    writer = threading.Thread(target=audio_writer, daemon=True)
    writer.start()
    stop_at = time.time() + seconds + 2
    try:
        while time.time() < stop_at and proc.poll() is None and writer.is_alive():
            v, a = show.render()
            aq.put(a, timeout=10)
            proc.stdin.write(v)
            proc.stdin.flush()
    except (BrokenPipeError, OSError, queue.Full):
        pass
    finally:
        try:
            aq.put_nowait(None)
        except queue.Full:
            pass
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        writer.join(timeout=5)
    return proc.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", help="save one frame to this PNG and exit")
    ap.add_argument("--at", help="UTC time for --snapshot, e.g. 2026-09-26T17:00")
    ap.add_argument("--scene", choices=[n for n, _ in SCHEDULE], help="scene to preview with --snapshot")
    ap.add_argument("--t", type=float, default=5.0, help="seconds into the scene (with --scene)")
    ap.add_argument("--demo-chat", action="store_true", help="simulate chat viewers")
    args = ap.parse_args()

    chat = Chat(VIDEO_ID)
    if args.demo_chat:
        chat.enabled = True
        chat.demo()

    if args.snapshot:
        at = datetime.fromisoformat(args.at).replace(tzinfo=timezone.utc) if args.at else None
        force = (args.scene, args.t) if args.scene else None
        show = Show(fixed_time=at, force=force, prebuild=(args.scene == "timelapse"), chat=chat)
        if args.scene == "quiz" and args.demo_chat:
            show.video()  # enters the scene, opens voting
            for i in range(60):
                chat.handle(f"v{i}", random.choice("ABBBC"))
        if args.scene:
            show.frame_no = int(args.t * FPS)
        Image.frombytes("RGB", (W, H), show.video()).save(args.snapshot)
        print("saved", args.snapshot)
        return

    output_url = os.environ.get("OUTPUT_URL")  # override for local testing
    if not output_url:
        key = os.environ.get("YOUTUBE_STREAM_KEY")
        if not key:
            sys.exit("Set the YOUTUBE_STREAM_KEY environment variable / secret.")
        output_url = f"rtmp://a.rtmp.youtube.com/live2/{key}"

    print("Preparing scenes...", flush=True)
    show = Show(chat=chat)
    print(f"Audio: mode={show.audio.mode}, random key {show.audio.root:.1f} Hz (synthesized, new every run)", flush=True)
    chat.start()
    end = time.time() + DURATION
    while end - time.time() > 10:
        remaining = end - time.time()
        print(f"Streaming for {int(remaining)}s...", flush=True)
        try:
            code = run_segment(show, output_url, remaining)
            print(f"ffmpeg exited with code {code}; restarting in 5s", flush=True)
        except Exception as e:  # noqa: BLE001 - keep the stream alive no matter what
            print(f"[error] segment crashed: {e!r}; restarting in 5s", flush=True)
        time.sleep(5)
    print("Segment finished - next run takes over.", flush=True)


if __name__ == "__main__":
    main()
