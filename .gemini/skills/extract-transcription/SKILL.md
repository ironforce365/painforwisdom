---
name: extract-transcription
description: >
  Extract a transcription from a video file using Whisper via extract_transcription.sh.
  Usage: /extract-transcription <video_file> [language] [date YYYY-MM-DD]
---

Extract a transcription from a video file using the `extract_transcription.sh` script.

## Steps

1. Parse the arguments the user provided after `/extract-transcription`:
   - `$1` — path to the video file (required)
   - `$2` — language (optional, default: English)
   - `$3` — date in YYYY-MM-DD format (optional, default: today)

2. If no video file path was given, ask the user for it before proceeding.

3. Verify the video file exists:
   ```bash
   ls "$VIDEO_FILE"
   ```
   If the file does not exist, report the error and stop.

4. Run the extraction script from the project root:
   ```bash
   bash ./extract_transcription.sh "$VIDEO_FILE" "$LANGUAGE" "$DATE"
   ```
   Stream the output to the user so progress is visible.

5. After the script completes, locate the transcript file that was produced (it will be in an `auto-generated/` subfolder inside the video's directory, named `transcript_<DATE>.txt` or `transcript_<DATE>_<N>.txt`).

6. Confirm success to the user and print the full path to the transcript file.
   If the transcript file is not found, report the failure and show the script output.
