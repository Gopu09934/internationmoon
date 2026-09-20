# International Observe the Moon Night - interactive 24/7 YouTube Live

A fully generated live show (no video or audio files needed) that runs on GitHub Actions.

## What viewers get
Scenes rotate on an ~8.5 minute loop:
- **Live Moon** - real current phase, UTC clock, countdown to next Full/New Moon, Sun-Earth-Moon diagram, tips
- **Moon Quiz** - 12 questions, 30 s countdown with ticking, answer reveal (live chat voting: type A, B or C)
- **Next Moon Phases** - exact dates/times and countdowns for the next 5 phases
- **Timelapse** - a whole lunar cycle in 30 seconds
- **Did you know?** - animated Moon fact cards
- Generated ambient audio with chimes, countdown ticks and reveal sounds
- Rotating "like / subscribe / share" ribbon

Live chat commands (optional, see step 5): `A` `B` `C` vote in the quiz, `!hello` shows the viewer's
name on screen with a chime, `!fact` shows a random Moon fact.

## Setup
1. Create a **public** GitHub repo and upload everything here (keep the `.github` folder).
2. In YouTube Studio enable live streaming, then Go Live -> Stream and copy the reusable **stream key**.
3. Repo -> Settings -> Secrets and variables -> Actions -> **Secrets** -> New secret:
   `YOUTUBE_STREAM_KEY` = your stream key.
4. Actions tab -> "Moon Night 24/7 Live Stream" -> **Run workflow**.
5. (Optional, for chat features) Once the stream is live, open it on YouTube and copy the video ID from
   the URL (`youtube.com/watch?v=VIDEO_ID`). Then Settings -> Secrets and variables -> Actions ->
   **Variables** -> New variable: `YOUTUBE_VIDEO_ID` = that ID. Update it if the video ID ever changes.
   Chat reading uses the unofficial `pytchat` library; if it stops working the stream keeps running
   without the chat features.

## Preview locally
    pip install numpy pillow          # streaming also needs ffmpeg and DejaVu fonts
    python3 moon_stream.py --snapshot p.png                          # live scene
    python3 moon_stream.py --snapshot p.png --scene quiz --t 12 --demo-chat
    python3 moon_stream.py --snapshot p.png --scene timelapse --t 15
    python3 moon_stream.py --snapshot p.png --at 2026-09-26T17:00    # preview a date (UTC)
Scenes: live, quiz, calendar, timelapse, facts. `--demo-chat` fakes viewers.

## Notes
- GitHub jobs stop after 6 hours, so each run queues the next; expect a short buffering gap at each
  handoff. An hourly cron restarts the chain if it breaks. Scheduled workflows pause after 60 days
  without repo activity.
- Long-running streams may conflict with GitHub's Actions terms. For a truly reliable 24/7 stream run
  `YOUTUBE_STREAM_KEY=... STREAM_SECONDS=31536000 python3 moon_stream.py` on a small VPS under systemd.
- Viewer display names appear on screen for `!hello`; use YouTube's chat moderation / blocked words.
- Quiz answers and facts are hard-coded in `moon_stream.py` (QUIZ, FACTS) - edit or add your own.
