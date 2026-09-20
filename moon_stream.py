#!/usr/bin/env python3
"""
International Observe the Moon Night - 24/7 YouTube live stream.

Everything is generated in code, no video or audio files needed:
  * a procedural Moon showing the current phase (computed from Sun/Moon positions)
  * twinkling stars, drifting haze and occasional shooting stars
  * live UTC clock, illumination %, countdown to next full / new Moon
  * rotating stargazing tips
  * generated ambient drone audio

Usage
  YOUTUBE_STREAM_KEY=xxxx python3 moon_stream.py          # go live
  python3 moon_stream.py --snapshot preview.png            # save one frame
  python3 moon_stream.py --snapshot p.png --at 2026-09-26T17:00   # preview a date (UTC)

Requires: ffmpeg, numpy, Pillow, DejaVu fonts.
"""
import argparse
import math
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 30
DURATION = int(os.environ.get("STREAM_SECONDS", 20400))  # 5h40m: under GitHub's 6h job limit

SYNODIC = 29.530588853  # mean length of a lunar cycle, days
J2000 = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)

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
    "Share what you see with #ObserveTheMoon",
]


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def font(size, bold=False):
    path = "/usr/share/fonts/truetype/dejavu/" + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


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
    """Approximate age in days since new Moon (derived from the true elongation)."""
    return elongation(now) / 360.0 * SYNODIC


def days_until(now, target_deg):
    """Days until the elongation next reaches target_deg (0 = new Moon, 180 = full Moon)."""
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

    # craters, lit from the upper left: lower-right inner wall bright, upper-left in shadow
    for _ in range(650):
        r = 2.0 + 30 * rng.random() ** 4.5
        cx, cy = rng.uniform(0, S, 2)
        if (cx - R) ** 2 + (cy - R) ** 2 > (R - r * 0.5) ** 2:
            continue
        x0, x1 = int(max(0, cx - r * 1.7)), int(min(S, cx + r * 1.7) + 1)
        y0, y1 = int(max(0, cy - r * 1.7)), int(min(S, cy + r * 1.7) + 1)
        dx = (xx[y0:y1, x0:x1] - cx) / r
        dy = (yy[y0:y1, x0:x1] - cy) / r
        d = np.hypot(dx, dy)
        wall = np.exp(-((d - 0.85) / 0.22) ** 2)
        albedo[y0:y1, x0:x1] += (-0.05 * np.clip(1 - d ** 2, 0, 1)
                                 + 0.11 * wall * (dx + dy) / (d * 1.414 + 1e-6)
                                 + 0.03 * np.exp(-((d - 1.3) / 0.35) ** 2))

    # one bright young crater with rays (Tycho-like)
    cx, cy = R * 0.86, R * 1.52
    dx, dy = xx - cx, yy - cy
    dist = np.hypot(dx, dy)
    theta = np.arctan2(dy, dx)
    rays = np.clip(np.cos(13 * theta + 1.7 * np.sin(5 * theta)) - 0.55, 0, 1)
    albedo += 0.35 * rays * np.exp(-dist / (0.33 * R)) + 0.25 * np.exp(-((dist / 7.0) ** 2))
    return np.clip(albedo, 0.05, 1.0)


# ----------------------------------------------------------------------------
# Scene renderer
# ----------------------------------------------------------------------------
class Scene:
    R = 190
    CX, CY = W // 2, H // 2 + 10
    C = int(R * 3.4)

    def __init__(self, fixed_time=None):
        self.fixed_time = fixed_time
        self.rng = np.random.default_rng(2026)
        self.albedo = make_albedo(self.R)
        self.base = self._make_background()
        self.bg_moon = self.base
        self.overlay = np.zeros((H, W, 3), np.uint8)
        self.streaks = []
        self.last_sec = None
        self.moon_key = None
        self.frame_no = 0

    # -- static background: gradient, haze, stars -------------------------------
    def _make_background(self):
        rng = self.rng
        y = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        top = np.array([2, 3, 10], np.float32)
        bottom = np.array([9, 14, 34], np.float32)
        bg = np.repeat(top + (bottom - top) * y ** 1.5, W, axis=1)
        h1 = smooth_noise(W, H, (3, 5), 11)
        h2 = smooth_noise(W, H, (4, 6), 12)
        bg[..., 0] += 9 * h2 ** 2
        bg[..., 1] += 7 * h1 ** 2
        bg[..., 2] += 22 * h1 ** 2

        n = 1100
        xs = rng.integers(0, W - 1, n)
        ys = rng.integers(0, H - 1, n)
        bright = 45 + 210 * rng.random(n) ** 4
        tints = np.array([[1, 1, 1], [0.8, 0.88, 1], [1, 0.92, 0.78]], np.float32)
        col = tints[rng.integers(0, 3, n)] * bright[:, None]
        bg[ys, xs] = np.maximum(bg[ys, xs], col)
        big = bright > 190
        for dx, dy in ((1, 0), (0, 1), (1, 1)):
            bg[ys[big] + dy, xs[big] + dx] = np.maximum(bg[ys[big] + dy, xs[big] + dx], col[big] * 0.45)

        # stars that twinkle (kept away from the Moon)
        dist = np.hypot(xs - self.CX, ys - self.CY)
        idx = np.flatnonzero((dist > self.R * 1.9) & (bright > 70))[:240]
        self.tw_x, self.tw_y, self.tw_col = xs[idx], ys[idx], col[idx]
        self.tw_speed = rng.uniform(0.8, 3.0, len(idx))
        self.tw_phase = rng.uniform(0, 6.28, len(idx))
        return np.clip(bg, 0, 255).astype(np.uint8)

    # -- moon sprite composited onto the background (rebuilt once a minute) ------
    def _build_moon_bg(self, frac):
        R, C = self.R, self.C
        S = 2 * R
        yy, xx = np.mgrid[0:S, 0:S].astype(np.float32)
        nx = (xx - R + 0.5) / R
        ny = (yy - R + 0.5) / R
        r2 = nx * nx + ny * ny
        r = np.sqrt(r2)
        z = np.sqrt(np.clip(1 - r2, 0, 1))
        th = 2 * math.pi * frac
        # Sun direction: right-lit when waxing (northern-hemisphere view)
        ndl = nx * math.sin(th) - z * math.cos(th)
        lit = np.clip((ndl + 0.02) / 1.02, 0, 1)
        inten = self.albedo * (lit ** 0.35) * 1.12
        earth = 0.075 * self.albedo * (1 - lit)  # faint earthshine on the dark side
        col = np.stack([inten + earth * 0.7, inten * 0.985 + earth * 0.85, inten * 0.94 + earth * 1.1], -1) * 255
        alpha = np.clip((1 - r) * R, 0, 1)

        o = (C - S) // 2
        A = np.zeros((C, C), np.float32)
        M = np.zeros((C, C, 3), np.float32)
        A[o:o + S, o:o + S] = alpha
        M[o:o + S, o:o + S] = col

        gy, gx = np.mgrid[0:C, 0:C].astype(np.float32)
        gr = np.hypot(gx - C / 2, gy - C / 2)
        illum = (1 - math.cos(th)) / 2
        glow = np.exp(-np.clip(gr - R, 0, None) / (0.30 * R)) * (0.05 + 0.22 * illum)
        glow *= np.clip((C / 2 - gr) / 60, 0, 1)  # fade to zero at the patch border
        glow_rgb = glow[..., None] * np.array([150, 175, 235], np.float32)

        x0, y0 = self.CX - C // 2, self.CY - C // 2
        region = self.base[y0:y0 + C, x0:x0 + C].astype(np.float32) + glow_rgb
        region = region * (1 - A[..., None]) + M * A[..., None]
        out = self.base.copy()
        out[y0:y0 + C, x0:x0 + C] = np.clip(region, 0, 255).astype(np.uint8)
        return out

    # -- text overlay (rebuilt once a second) ------------------------------------
    def _draw_overlay(self, now, age, frac):
        img = Image.new("RGB", (W, H), (0, 0, 0))
        d = ImageDraw.Draw(img)
        white, soft, dim = (240, 244, 255), (170, 185, 220), (105, 118, 150)

        d.text((W // 2, 50), "INTERNATIONAL OBSERVE THE MOON NIGHT", font=font(40, True), fill=white, anchor="mm")
        d.text((W // 2, 95), "24/7 LIVE  -  LOOK UP, WHEREVER YOU ARE", font=font(22), fill=soft, anchor="mm")

        illum = (1 - math.cos(2 * math.pi * frac)) / 2 * 100
        lx, cy = 60, self.CY
        d.text((lx, cy - 40), phase_name(age), font=font(32, True), fill=white, anchor="lm")
        d.text((lx, cy + 5), f"{illum:.0f}% illuminated", font=font(24), fill=soft, anchor="lm")
        d.text((lx, cy + 42), f"Moon age {age:.1f} days", font=font(20), fill=dim, anchor="lm")

        rx = W - 60
        d.text((rx, cy - 45), now.strftime("%H:%M:%S") + " UTC", font=font(34, True), fill=white, anchor="rm")
        d.text((rx, cy - 5), now.strftime("%A %d %B %Y"), font=font(20), fill=soft, anchor="rm")
        d.text((rx, cy + 35), f"Full Moon in {fmt_days(days_until(now, 180.0))}", font=font(22), fill=soft, anchor="rm")
        d.text((rx, cy + 68), f"New Moon in {fmt_days(days_until(now, 0.0))}", font=font(20), fill=dim, anchor="rm")

        tip = TIPS[int(now.timestamp() // 20) % len(TIPS)]
        d.text((W // 2, 615), tip, font=font(23), fill=(200, 210, 235), anchor="mm")
        d.text((W // 2, 660), "#ObserveTheMoon", font=font(20, True), fill=soft, anchor="mm")
        d.text((W - 18, H - 14), "Northern hemisphere view", font=font(14), fill=dim, anchor="rm")
        return np.asarray(img)

    # -- shooting stars ----------------------------------------------------------
    def _spawn_streak(self, t):
        side = random.choice((-1, 1))
        x = random.uniform(120, 380) if side < 0 else random.uniform(900, 1160)
        self.streaks.append({"t0": t, "x": x, "y": random.uniform(30, 120),
                             "dx": side * random.uniform(140, 240), "dy": random.uniform(60, 130),
                             "dur": random.uniform(0.6, 1.0)})

    def _draw_streaks(self, frame, t):
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
                    px = np.array([v * 0.9, v * 0.95, v], np.uint8)
                    frame[y:y + 2, x:x + 2] = np.maximum(frame[y:y + 2, x:x + 2], px)
        self.streaks = alive

    # -- per-frame ---------------------------------------------------------------
    def now(self):
        return self.fixed_time or datetime.now(timezone.utc)

    def render(self):
        now = self.now()
        sec = int(now.timestamp())
        if sec != self.last_sec:
            self.last_sec = sec
            age = moon_age(now)
            frac = age / SYNODIC
            if sec // 60 != self.moon_key:
                self.moon_key = sec // 60
                self.bg_moon = self._build_moon_bg(frac)
            self.overlay = self._draw_overlay(now, age, frac)

        t = self.frame_no / FPS
        frame = np.maximum(self.bg_moon, self.overlay)
        fac = 0.65 + 0.35 * np.sin(t * self.tw_speed + self.tw_phase)
        vals = (self.tw_col * fac[:, None]).astype(np.uint8)
        frame[self.tw_y, self.tw_x] = np.maximum(vals, self.overlay[self.tw_y, self.tw_x])
        if random.random() < 1 / (FPS * 25):
            self._spawn_streak(t)
        self._draw_streaks(frame, t)
        self.frame_no += 1
        return frame


# ----------------------------------------------------------------------------
# Streaming
# ----------------------------------------------------------------------------
def audio_expr(detune):
    """Slow-breathing A-minor drone; a slightly detuned copy per channel gives stereo width."""
    parts = [(110.0, 0.10, 0.031, 0), (164.81, 0.07, 0.043, 1), (220.0, 0.05, 0.057, 2), (329.63, 0.03, 0.071, 3)]
    return "+".join(
        f"{a}*sin(2*PI*{f * detune:.3f}*t)*(0.7+0.3*sin(2*PI*{lfo}*t+{ph}))" for f, a, lfo, ph in parts
    )


def ffmpeg_cmd(output_url, seconds):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-re", "-thread_queue_size", "512", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-framerate", str(FPS), "-i", "pipe:0",
        "-re", "-f", "lavfi", "-i", f"aevalsrc=exprs={audio_expr(1.0)}|{audio_expr(1.004)}:s=44100",
        "-map", "0:v:0", "-map", "1:a:0",
        "-af", "lowpass=f=900,aecho=0.8:0.6:700|1300:0.35|0.25,volume=1.5",
        "-c:v", "libx264", "-preset", "veryfast", "-b:v", "2500k", "-maxrate", "2500k", "-bufsize", "5000k",
        "-pix_fmt", "yuv420p", "-g", str(FPS * 2), "-keyint_min", str(FPS * 2),
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-t", str(int(seconds)),
        "-f", "flv", output_url,
    ]


def run_segment(scene, output_url, seconds):
    proc = subprocess.Popen(ffmpeg_cmd(output_url, seconds), stdin=subprocess.PIPE)
    stop_at = time.time() + seconds + 2
    try:
        while time.time() < stop_at and proc.poll() is None:
            proc.stdin.write(scene.render())
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    return proc.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", help="save one frame to this PNG and exit")
    ap.add_argument("--at", help="UTC time for --snapshot, e.g. 2026-09-26T17:00")
    args = ap.parse_args()

    if args.snapshot:
        at = datetime.fromisoformat(args.at).replace(tzinfo=timezone.utc) if args.at else None
        scene = Scene(fixed_time=at)
        Image.fromarray(scene.render()).save(args.snapshot)
        print("saved", args.snapshot)
        return

    output_url = os.environ.get("OUTPUT_URL")  # override for local testing
    if not output_url:
        key = os.environ.get("YOUTUBE_STREAM_KEY")
        if not key:
            sys.exit("Set the YOUTUBE_STREAM_KEY environment variable / secret.")
        output_url = f"rtmp://a.rtmp.youtube.com/live2/{key}"

    scene = Scene()
    end = time.time() + DURATION
    while end - time.time() > 10:
        remaining = end - time.time()
        print(f"Streaming for {int(remaining)}s...", flush=True)
        code = run_segment(scene, output_url, remaining)
        print(f"ffmpeg exited with code {code}; restarting in 5s", flush=True)
        time.sleep(5)
    print("Segment finished - next run takes over.", flush=True)


if __name__ == "__main__":
    main()
