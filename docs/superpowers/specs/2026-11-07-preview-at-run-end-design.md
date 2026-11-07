# Preview copy made by the run

## Why

Phone videos are HEVC, which Chrome doesn't decode. Until now the console started converting a playable copy only when the user opened the YouTube package and the browser failed. The first view always showed "This browser cannot decode this video. Converting a preview…" for about half the video's length.

The ask: have the video ready when the package is opened, without making the run any slower. If it is still converting when the package is opened, show an animation until it is ready.

## What changed

- **The run starts the copy itself.** When `youtube_upload` writes the package (any mode but `off`), it starts `python -m pipeline.video_preview <video> <copy>` in a process of its own (`start_new_session`) and returns at once.
  - The run never waits for it; the process outlives the run.
  - Same ffmpeg settings as before: H.264, long side capped at 1280, HDR tone-mapped, at `nice 10`.
- **Only what needs it is converted.** The run converts only when ffprobe says the video isn't H.264. If the probe fails, it converts nothing and the console's on-demand path still works.
- **Progress lives in files**, beside the copy in `<run>/console-preview/`, so the console sees work a different process started:
  - `.job` holds the converter's pid; a dead pid counts as not running.
  - `.part.mp4` is the copy being written.
  - `.mp4` is the finished copy.
  - `.error` records why the conversion failed.
- **The console asks before playing:**
  - A copy is ready: it plays at once, and the original is never fetched.
  - The run is still converting: an animation takes the video's place and the note says "Converting a preview of this video…". The copy swaps in when it's done.
  - No copy: the original plays, and the on-demand conversion works as before. That path now shows the same animation instead of a black box with sound.
- **Delete waits** while a run's conversion is still going, as it already did for the console's own.

## Decisions made without the user

- Any mode but `off` gets the copy, `auto` included. A copy that nobody watches costs only background CPU.
- A replayed node doesn't start a second conversion, because it finds the copy or the live job first.
- If the run's conversion failed before the package was opened, the page plays the original and converts on demand.

## Limits

- The copy competes for CPU with the rest of the run, at low priority. The run's own steps are mostly waiting on the API, so they shouldn't slow down much. That's not measured.
- Runs from before this change have no copy, and convert on demand as before.
