# International Observe the Moon Night - 24/7 YouTube Live

A fully generated live stream (no video or audio files needed): procedural Moon
showing the real current phase, twinkling stars, live UTC clock, countdowns and
ambient audio. Runs on GitHub Actions in chained ~5h40m segments.

## Setup
1. Create a **public** GitHub repo and upload everything in this folder (keep the `.github` folder).
2. In YouTube Studio, enable live streaming, then go to Go Live -> Stream and copy the reusable **stream key**.
3. In the repo: Settings -> Secrets and variables -> Actions -> New repository secret
   - Name: `YOUTUBE_STREAM_KEY`
   - Value: your stream key
4. Open the **Actions** tab, choose "Moon Night 24/7 Live Stream", click **Run workflow**.

## Preview a frame locally
    pip install numpy pillow        # also needs ffmpeg and DejaVu fonts for streaming
    python3 moon_stream.py --snapshot preview.png
    python3 moon_stream.py --snapshot preview.png --at 2026-09-26T17:00

## Notes
- GitHub jobs stop after 6 hours, so each run queues the next one; expect a brief
  buffering blip (under a minute) at each handoff. An hourly cron restarts the chain if it breaks.
- Scheduled workflows pause after 60 days of no repo activity.
- Long-running streams may conflict with GitHub's Actions terms. For a truly reliable
  24/7 stream, run `YOUTUBE_STREAM_KEY=... python3 moon_stream.py` on a small VPS instead
  (set STREAM_SECONDS to a large number, e.g. 31536000, and run it under systemd).
