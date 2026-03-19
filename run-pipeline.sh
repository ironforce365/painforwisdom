#!/bin/bash
# Usage:
#   ./run_pipeline.sh [--yes] [--no-input] <directory|file>
# Examples:
#   ./run_pipeline.sh ./bulk-ingestion-up-to-feb-27/
#   ./run_pipeline.sh --yes ./bulk-ingestion-up-to-feb-27/
#   ./run_pipeline.sh ./bulk-ingestion-up-to-feb-27/transcript_2026-02-10.txt
#   ./run_pipeline.sh ./runs/2026-02-17_run.mp4

set -euo pipefail

AUTO_YES=false
NO_INPUT=false
INPUT=""

usage() {
    cat <<EOH
Usage: ./run_pipeline.sh [--yes] [--no-input] <directory|file>

Options:
  --yes       Auto-confirm bulk processing prompts.
  --no-input  Non-interactive mode for this wrapper (skip local prompts).
              Note: Claude-side pauses for approvals are still handled by Claude flow.
  -h, --help  Show this help.
EOH
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes)
            AUTO_YES=true
            shift
            ;;
        --no-input)
            NO_INPUT=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*)
            echo "✗ Unknown option: $1"
            usage
            exit 1
            ;;
        *)
            INPUT="$1"
            shift
            ;;
    esac
done

if [ -z "${INPUT:-}" ]; then
    usage
    exit 1
fi

# Create one master run directory for this entire invocation.
# All files processed in this run share the same RUN_ID and pipeline.log.
RUN_ID=$(date +%Y-%m-%d_%H%M%S)
RUN_BASE="./processed/$RUN_ID"
LOG_FILE="$RUN_BASE/pipeline.log"
mkdir -p "$RUN_BASE"
echo "$(date +%Y-%m-%dT%H:%M:%S) PIPELINE_START run_id=$RUN_ID input=$INPUT" >> "$LOG_FILE"
echo "Run ID: $RUN_ID"

is_video() {
    echo "${1##*.}" | grep -qiE "^(mp4|mov|m4v|avi|mkv)$"
}

extract_date_from_filename() {
    local name
    name=$(basename "$1")
    # Try YYYY-MM-DD first
    local d
    d=$(echo "$name" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1)
    if [ -n "$d" ]; then
        echo "$d"
        return
    fi
    # Fall back to YYYYMMDD (e.g. PXL_20260227_...)
    d=$(echo "$name" | grep -oE '[0-9]{8}' | head -1)
    if [ -n "$d" ]; then
        echo "${d:0:4}-${d:4:2}-${d:6:2}"
    fi
}

confirm_bulk() {
    local count="$1"
    if [ "$AUTO_YES" = true ] || [ "$NO_INPUT" = true ]; then
        return 0
    fi

    read -r -p "Process all ${count} files? (yes/no): " CONFIRM
    [ "$CONFIRM" = "yes" ]
}

run_file() {
    local FILE="$1"
    local DATE
    DATE=$(basename "$FILE" .txt | sed 's/transcript_//')

    echo "Processing: $FILE"
    echo "$(date +%Y-%m-%dT%H:%M:%S) FILE_START file=$(basename "$FILE" .txt)" >> "$LOG_FILE"
    claude -p "Run the content pipeline on this transcript. RUN_ID: $RUN_ID. LOG_FILE: $LOG_FILE. Date: $DATE. Transcript file: $FILE. Transcript: $(cat "$FILE")"
}

# Video detection and transcript extraction
if [ -f "$INPUT" ] && is_video "$INPUT"; then
    DATE=$(extract_date_from_filename "$INPUT")
    if [ -z "$DATE" ]; then
        echo "✗ Could not extract a YYYY-MM-DD date from filename: $INPUT"
        exit 1
    fi

    echo "Video detected. Extracting transcript (date: $DATE)..."
    if ! claude -p "/extract-transcription \"$INPUT\" English $DATE"; then
        TRANSCRIPT="$(dirname "$INPUT")/auto-generated/transcript_${DATE}.txt"
        if [ ! -f "$TRANSCRIPT" ]; then
            echo "✗ Extraction failed for: $(basename "$INPUT")"
            echo "$(date +%Y-%m-%dT%H:%M:%S) EXTRACTION_FAILED file=$(basename "$INPUT")" >> "$LOG_FILE"
            ./telegram_io.sh send "❌ Extraction failed — $(basename "$INPUT")\nRun ID: $RUN_ID\nCheck terminal output for details (auth error, missing tool, etc.)" || true
            exit 2
        fi
    fi

    TRANSCRIPT="$(dirname "$INPUT")/auto-generated/transcript_${DATE}.txt"

    if [ ! -f "$TRANSCRIPT" ]; then
        echo "✗ Transcript not found after extraction. Expected: $TRANSCRIPT"
        exit 1
    fi

    INPUT="$TRANSCRIPT"
    echo "✓ Transcript extracted: $INPUT"
fi

# if the original INPUT was a video file, then INPUT will refer to the transcript extracted
if [ -f "$INPUT" ]; then
    run_file "$INPUT"

elif [ -d "$INPUT" ]; then
    # Extract transcripts from any video files in the directory first
    for f in "$INPUT"/*; do
        [ -f "$f" ] || continue
        if is_video "$f"; then
            DATE=$(extract_date_from_filename "$f")
            if [ -z "$DATE" ]; then
                echo "✗ Could not extract date from video: $f — skipping"
                continue
            fi
            TRANSCRIPT="$(dirname "$f")/auto-generated/transcript_${DATE}.txt"
            if [ -f "$TRANSCRIPT" ]; then
                echo "✓ Transcript already exists for $DATE — skipping extraction"
            else
                echo "Video found: $(basename "$f") — extracting transcript (date: $DATE)..."
                if ! claude -p "/extract-transcription \"$f\" English $DATE"; then
                    echo "✗ Extraction failed for: $(basename "$f") — skipping"
                    echo "$(date +%Y-%m-%dT%H:%M:%S) EXTRACTION_FAILED file=$(basename "$f")" >> "$LOG_FILE"
                    ./telegram_io.sh send "❌ Extraction failed — $(basename "$f")\nRun ID: $RUN_ID\nCheck terminal output for details (auth error, missing tool, etc.)" || true
                    continue
                fi
                if [ ! -f "$TRANSCRIPT" ]; then
                    echo "✗ Transcript not found after extraction. Expected: $TRANSCRIPT — skipping"
                else
                    echo "✓ Transcript extracted: $TRANSCRIPT"
                fi
            fi
        fi
    done

    FILES=$(find "$INPUT" -maxdepth 2 -name "transcript_*.txt" 2>/dev/null | \
        awk -F/ '{print $NF, $0}' | sort | cut -d' ' -f2-)

    if [ -z "$FILES" ]; then
        echo "✗ No transcript_*.txt files found in $INPUT"
        exit 1
    fi

    echo "Files to process:"
    echo "$FILES" | nl
    echo ""

    COUNT=$(echo "$FILES" | wc -l | xargs)
    if ! confirm_bulk "$COUNT"; then
        echo "Aborted."
        exit 0
    fi

    while IFS= read -r FILE <&3; do
        run_file "$FILE"
    done 3<<< "$FILES"

    echo ""
    echo "✓ Bulk ingestion complete."

else
    echo "✗ Input is not a valid file or directory: $INPUT"
    exit 1
fi

echo "$(date +%Y-%m-%dT%H:%M:%S) BULK_COMPLETE run_id=$RUN_ID" >> "$LOG_FILE"
