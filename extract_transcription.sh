#!/bin/bash
# Strict mode:
# -e: stop on command errors
# -u: treat unset vars as errors
# -o pipefail: fail pipeline if any command fails
set -euo pipefail

# $1 : input file (video)
# $2 : language (optional; default: English)
# $3 : date (optional; YYYY-MM-DD; default: today)
#
# Backend selection:
#   WHISPER_BACKEND=local  -> local whisper only (default)
#   WHISPER_BACKEND=openai -> OpenAI API helper only
#   WHISPER_BACKEND=auto   -> local first, then OpenAI helper fallback
#
# Exit codes:
#   0 = transcript written successfully
#   1 = hard error (missing tool, missing file, etc.)
#   2 = quarantined (low confidence — Telegram notification already sent)

INPUT_FILE="${1:-}"
LANGUAGE="${2:-English}"
DATE="${3:-$(date +"%Y-%m-%d")}"  # shellcheck disable=SC2016
WHISPER_BACKEND="${WHISPER_BACKEND:-local}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEGRAM="$SCRIPT_DIR/telegram_io.sh"

if [ -z "$INPUT_FILE" ]; then
    echo "Missing input file"
    exit 1
fi

if [ ! -f "$INPUT_FILE" ]; then
    echo "Input file does not exist: $INPUT_FILE"
    exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg is required but not found in PATH"
    exit 1
fi

INPUT_BASE_DIR="$(dirname "$INPUT_FILE")"
OUTPUT_DIR="$INPUT_BASE_DIR/auto-generated"
QUARANTINE_DIR="$INPUT_BASE_DIR/quarantine"
mkdir -p "$OUTPUT_DIR"

TARGET_TRANSCRIPT_FILENAME="$OUTPUT_DIR/transcript_${DATE}.txt"
if [ -f "$TARGET_TRANSCRIPT_FILENAME" ]; then
    echo "Target transcript filename exists, creating unique filename"
    i=1
    while [[ -e "$OUTPUT_DIR/transcript_${DATE}_$i.txt" ]]; do
        ((i++))
    done
    TARGET_TRANSCRIPT_FILENAME="$OUTPUT_DIR/transcript_${DATE}_$i.txt"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
TEMP_AUDIO_FILE_PATH="$TMP_DIR/audio_for_transcription.mp3"

# Always extract/compress audio first for reliability and consistent behavior.
ffmpeg -y -i "$INPUT_FILE" -vn -ac 1 -ar 16000 -b:a 32k "$TEMP_AUDIO_FILE_PATH" >/dev/null 2>&1

OPENAI_WHISPER_HELPER="/home/gonzalo/.npm-global/lib/node_modules/openclaw/skills/openai-whisper-api/scripts/transcribe.sh"
CONDA_WHISPER="/opt/miniconda3/envs/painforwisdom/bin/whisper"

# ---------------------------------------------------------------------------
# Confidence analysis — runs after local Whisper produces JSON output
# Returns: sets QUALITY (OK|LOW) and CONFIDENCE_REPORT (multi-line string)
# ---------------------------------------------------------------------------
analyze_confidence() {
    local json_file="$1"
    local result
    result=$(python3 - "$json_file" <<'PYEOF'
import json, sys

with open(sys.argv[1]) as f:
    data = json.load(f)

segments = data.get('segments', [])
if not segments:
    print("SEGMENTS=0")
    print("BAD_SEGMENTS=0")
    print("PCT_BAD=100.0")
    print("AVG_LOGPROB=N/A")
    print("AVG_NO_SPEECH=N/A")
    print("QUALITY=LOW")
    print("REASON=No segments found — file may be silent or corrupted")
    sys.exit()

bad = 0
for s in segments:
    is_bad = (
        s.get('no_speech_prob', 0) > 0.5 or
        s.get('avg_logprob', 0) < -1.0 or
        s.get('compression_ratio', 0) > 2.4
    )
    if is_bad:
        bad += 1

total = len(segments)
pct_bad = bad / total * 100
avg_logprob = sum(s.get('avg_logprob', 0) for s in segments) / total
avg_no_speech = sum(s.get('no_speech_prob', 0) for s in segments) / total

print(f"SEGMENTS={total}")
print(f"BAD_SEGMENTS={bad}")
print(f"PCT_BAD={pct_bad:.1f}")
print(f"AVG_LOGPROB={avg_logprob:.3f}")
print(f"AVG_NO_SPEECH={avg_no_speech:.3f}")

if pct_bad >= 20:
    print("QUALITY=LOW")
    print(f"REASON={pct_bad:.1f}% of segments have low confidence (threshold: 20%)")
else:
    print("QUALITY=OK")
    print("REASON=Confidence within acceptable range")
PYEOF
)
    echo "$result"
}

# ---------------------------------------------------------------------------
# Quarantine — moves original file and saves report; notifies via Telegram
# ---------------------------------------------------------------------------
quarantine_file() {
    local report="$1"
    local basename
    basename=$(basename "$INPUT_FILE")
    local ts
    ts=$(date +"%Y-%m-%d_%H%M%S")
    local dest="$QUARANTINE_DIR/${ts}_${basename%.*}"
    mkdir -p "$dest"

    # Move original file into quarantine
    mv "$INPUT_FILE" "$dest/$basename"

    # Save confidence report
    echo "$report" > "$dest/confidence_report.txt"

    # Save the low-quality transcript if it was partially written
    if [ -f "$TARGET_TRANSCRIPT_FILENAME" ]; then
        mv "$TARGET_TRANSCRIPT_FILENAME" "$dest/low_quality_transcript.txt"
    fi

    # Save Whisper JSON if available
    local json_file="$TMP_DIR/$(basename "$TEMP_AUDIO_FILE_PATH" .mp3).json"
    if [ -f "$json_file" ]; then
        cp "$json_file" "$dest/whisper_output.json"
    fi

    echo "✗ File quarantined: $dest"

    # Telegram notification
    if [ -x "$TELEGRAM" ]; then
        local pct_bad avg_logprob avg_no_speech
        pct_bad=$(echo "$report" | grep "^PCT_BAD=" | cut -d= -f2)
        avg_logprob=$(echo "$report" | grep "^AVG_LOGPROB=" | cut -d= -f2)
        avg_no_speech=$(echo "$report" | grep "^AVG_NO_SPEECH=" | cut -d= -f2)
        "$TELEGRAM" send "⚠️ Transcription quarantined: $basename\n\nConfidence metrics:\n  Bad segments: ${pct_bad}%\n  Avg log-prob: $avg_logprob (threshold: -1.0)\n  Avg no-speech prob: $avg_no_speech (threshold: 0.5)\n\nFile moved to:\n$dest\n\nInspect and re-record if needed." || true
    else
        echo "⚠️ Telegram not available — quarantine notification skipped"
    fi
}

# ---------------------------------------------------------------------------
# Local Whisper backend — outputs JSON for confidence analysis
# ---------------------------------------------------------------------------
run_local_whisper() {
    local whisper_bin=""
    if [ -x "$CONDA_WHISPER" ]; then
        echo "Using local conda whisper binary"
        whisper_bin="$CONDA_WHISPER"
    elif command -v whisper >/dev/null 2>&1; then
        echo "Using whisper from PATH"
        whisper_bin="whisper"
    else
        return 1
    fi

    # Output JSON so we can extract confidence metrics
    "$whisper_bin" "$TEMP_AUDIO_FILE_PATH" \
        --model large \
        --language "$LANGUAGE" \
        --output_format json \
        --output_dir "$TMP_DIR" \
        >/dev/null

    local json_file="$TMP_DIR/$(basename "$TEMP_AUDIO_FILE_PATH" .mp3).json"
    if [ ! -f "$json_file" ]; then
        echo "Whisper completed but JSON output was not found"
        exit 1
    fi

    # Analyze confidence
    local report
    report=$(analyze_confidence "$json_file")
    local quality
    quality=$(echo "$report" | grep "^QUALITY=" | cut -d= -f2)

    echo "Confidence report:"
    echo "$report" | grep -E "^(SEGMENTS|BAD_SEGMENTS|PCT_BAD|AVG_LOGPROB|AVG_NO_SPEECH|QUALITY|REASON)=" | sed 's/^/  /'

    if [ "$quality" = "LOW" ]; then
        # Extract text anyway so it can be saved to quarantine for inspection
        python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
print(' '.join(s['text'].strip() for s in data.get('segments', [])))" "$json_file" > "$TARGET_TRANSCRIPT_FILENAME" 2>/dev/null || true

        quarantine_file "$report"
        exit 2
    fi

    # Confidence OK — extract clean text from JSON
    python3 -c "
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
print(' '.join(s['text'].strip() for s in data.get('segments', [])))" "$json_file" > "$TARGET_TRANSCRIPT_FILENAME"

    return 0
}

# ---------------------------------------------------------------------------
# OpenAI Whisper API backend — no confidence metrics available
# ---------------------------------------------------------------------------
run_openai_whisper() {
    if [ -f "$OPENAI_WHISPER_HELPER" ]; then
        echo "Using OpenAI Whisper API helper"
        echo "⚠️  Note: confidence metrics not available for the OpenAI API backend"
        bash "$OPENAI_WHISPER_HELPER" "$TEMP_AUDIO_FILE_PATH" --language "$LANGUAGE" --out "$TARGET_TRANSCRIPT_FILENAME" >/dev/null
        return 0
    fi
    return 1
}

case "$WHISPER_BACKEND" in
    local)
        run_local_whisper || {
            echo "Local whisper backend not available."
            echo "Checked:"
            echo "- Conda whisper: $CONDA_WHISPER"
            echo "- whisper in PATH"
            exit 1
        }
        ;;
    openai)
        run_openai_whisper || {
            echo "OpenAI helper backend not available: $OPENAI_WHISPER_HELPER"
            exit 1
        }
        ;;
    auto)
        if ! run_local_whisper; then
            run_openai_whisper || {
                echo "No transcription backend available."
                echo "Checked local + OpenAI helper."
                exit 1
            }
        fi
        ;;
    *)
        echo "Invalid WHISPER_BACKEND: $WHISPER_BACKEND (expected: local|openai|auto)"
        exit 1
        ;;
esac

echo "✓ Transcript written: $TARGET_TRANSCRIPT_FILENAME"

if [ -x "$TELEGRAM" ]; then
    "$TELEGRAM" send "✅ Transcription complete: $(basename "$TARGET_TRANSCRIPT_FILENAME")\nConfidence: OK" || true
fi
