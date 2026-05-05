#!/usr/bin/env bash
# build_carousel_reel.sh — stitch per-slide MP4s into one continuous reel.
# Usage: build_carousel_reel.sh <slug> <motion_folder_path>
# Output: <motion_folder_path>/carousel_reel.mp4 — 9:16, 1080×1920, libx264/yuv420p
#
# Slide sources (priority per index):
#   1. <motion_dir>/black_NN_*_motion.mp4  (real clip — primary)
#   2. <motion_dir>/black_NN_*_kling.mp4   (Kling animated cover — fallback)
#   3. <png_dir>/black_NN_*_html.png       (Ken Burns 5s from static PNG — last resort)
#   <png_dir> = <motion_dir>/../png/
#
# Prints "[KB slide NN]" for every Ken Burns fallback used.
# Requires: ffmpeg, bash ≥ 4 (standard on GitHub Actions Ubuntu runners).

set -euo pipefail

SLUG="${1:?Usage: build_carousel_reel.sh <slug> <motion_folder_path>}"
MOTION_DIR="${2:?Missing motion_folder_path}"
PNG_DIR="$(dirname "$MOTION_DIR")/png"
OUTPUT="$MOTION_DIR/carousel_reel.mp4"
TMP_DIR=$(mktemp -d /tmp/carousel_reel_XXXXXX)
CONCAT_LIST="$TMP_DIR/concat.txt"

# 9:16 output dimensions
W=1080
H=1920
FPS=25
KB_DUR=5

# Scale + pad to fill 9:16 with black bars (letterbox/pillarbox as needed)
VF_NORM="scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

# Extract zero-padded 2-digit index from filenames: black_02_foo_motion.mp4 → "02"
_idx() {
    local base
    base=$(basename "$1")
    if [[ "$base" =~ _([0-9]{2})_ ]]; then echo "${BASH_REMATCH[1]}"; else echo ""; fi
}

declare -A MP4_MAP PNG_MAP

# Collect MP4s — skip the reel output itself; prefer _motion over _kling for same slot
while IFS= read -r -d '' f; do
    base=$(basename "$f")
    [[ "$base" == carousel_reel.mp4 ]] && continue
    idx=$(_idx "$f")
    [[ -z "$idx" ]] && continue
    if [[ -z "${MP4_MAP[$idx]+x}" ]]; then
        MP4_MAP["$idx"]="$f"
    elif [[ "$f" == *_motion.mp4 ]]; then
        # _motion wins over _kling for same slot
        MP4_MAP["$idx"]="$f"
    fi
done < <(find "$MOTION_DIR" -maxdepth 1 -name "black_*.mp4" -print0 2>/dev/null)

# Collect PNGs for Ken Burns fallback
if [[ -d "$PNG_DIR" ]]; then
    while IFS= read -r -d '' f; do
        idx=$(_idx "$f")
        [[ -z "$idx" ]] && continue
        PNG_MAP["$idx"]="$f"
    done < <(find "$PNG_DIR" -maxdepth 1 -name "black_*_html.png" -print0 2>/dev/null)
fi

# Merge indices from both maps, sort numerically
mapfile -t SORTED_IDXS < <(
    { for k in "${!MP4_MAP[@]}"; do echo "$k"; done
      for k in "${!PNG_MAP[@]}"; do echo "$k"; done
    } | sort -u -n
)

if [[ ${#SORTED_IDXS[@]} -eq 0 ]]; then
    echo "[carousel_reel] No slides found in $MOTION_DIR — skipping ($SLUG)"
    exit 0
fi

KB_SLIDES=()
SEG_COUNT=0

for idx in "${SORTED_IDXS[@]}"; do
    seg="$TMP_DIR/seg_${idx}.mp4"

    if [[ -n "${MP4_MAP[$idx]+x}" ]]; then
        # Real clip — normalize to 9:16
        ffmpeg -y -i "${MP4_MAP[$idx]}" \
            -vf "$VF_NORM" -r "$FPS" \
            -c:v libx264 -preset fast -pix_fmt yuv420p -an \
            "$seg" 2>/dev/null
    elif [[ -n "${PNG_MAP[$idx]+x}" ]]; then
        # Ken Burns zoom from static PNG — 5s at 25fps = 125 frames
        ffmpeg -y -loop 1 -framerate "$FPS" -i "${PNG_MAP[$idx]}" \
            -vf "${VF_NORM},zoompan=z='min(zoom+0.0015,1.5)':d=$((FPS * KB_DUR)):x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=${W}x${H}" \
            -t "$KB_DUR" -r "$FPS" \
            -c:v libx264 -preset fast -pix_fmt yuv420p -an \
            "$seg" 2>/dev/null
        KB_SLIDES+=("$idx")
        echo "[carousel_reel] [KB slide $idx] $(basename "${PNG_MAP[$idx]}")"
    else
        continue
    fi

    [[ -f "$seg" ]] || continue
    echo "file '$seg'" >> "$CONCAT_LIST"
    SEG_COUNT=$((SEG_COUNT + 1))
done

if [[ $SEG_COUNT -lt 3 ]]; then
    echo "[carousel_reel] Only $SEG_COUNT segment(s) — need ≥ 3, skipping ($SLUG)"
    exit 0
fi

# Concat all pre-normalized segments (same codec/resolution → -c copy is safe)
ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$OUTPUT" 2>&1

if [[ -f "$OUTPUT" ]]; then
    SIZE_KB=$(( $(wc -c < "$OUTPUT") / 1024 ))
    echo "[carousel_reel] Built: carousel_reel.mp4 (${SIZE_KB}KB, ${SEG_COUNT} slides)"
    [[ ${#KB_SLIDES[@]} -gt 0 ]] && \
        echo "[carousel_reel] Ken Burns fallback used for slide(s): ${KB_SLIDES[*]}"
    exit 0
else
    echo "[carousel_reel] ERROR: ffmpeg concat failed to produce output"
    exit 1
fi
