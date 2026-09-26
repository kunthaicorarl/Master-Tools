import re
import subprocess
from pathlib import Path
from datetime import timedelta

# ============================================================
# CONFIG
# ============================================================

INPUT_DIR = Path(r"D:\All Movies\The Frontier Prince Seasons 1–3")
CURRENT = Path(__file__).resolve().parent

VIDEO_FILE = INPUT_DIR / "video.mp4"
TRANSCRIPT_FILE = INPUT_DIR / "transcript.srt"

# Movie title = current folder name
MOVIE_TITLE = INPUT_DIR.name

# Output:
# D:\All Movies\The Frontier Prince Seasons 1–3\
#     collection\
#         The Frontier Prince Seasons 1–3\
OUTPUT_DIR =CURRENT / "collection" / MOVIE_TITLE


PART_SECONDS = 5 * 60  # 5 minutes


# ============================================================
# SRT PARSER / WRITER
# ============================================================
TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


def srt_time_to_seconds(value: str) -> float:
    h, m, s_ms = value.split(":", 2)
    s, ms = s_ms.split(",", 1)
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def seconds_to_srt_time(seconds: float) -> str:
    # Avoid floating point artifacts.
    total_ms = max(0, round(seconds * 1000))
    h = total_ms // 3_600_000
    total_ms %= 3_600_000
    m = total_ms // 60_000
    total_ms %= 60_000
    s = total_ms // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path: Path):
    text = path.read_text(encoding="utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

    blocks = re.split(r"\n\s*\n", text)
    entries = []

    for block in blocks:
        lines = block.split("\n")
        if not lines:
            continue

        match = None
        time_line_index = None

        for i, line in enumerate(lines):
            match = TIME_RE.search(line)
            if match:
                time_line_index = i
                break

        if not match:
            continue

        start = srt_time_to_seconds(
            f"{match.group(1)}:{match.group(2)}:{match.group(3)},{match.group(4)}"
        )
        end = srt_time_to_seconds(
            f"{match.group(5)}:{match.group(6)}:{match.group(7)},{match.group(8)}"
        )

        subtitle_text = "\n".join(lines[time_line_index + 1:]).strip()

        if subtitle_text:
            entries.append({
                "start": start,
                "end": end,
                "text": subtitle_text,
            })

    return entries


def write_srt(entries, path: Path, offset: float):
    with path.open("w", encoding="utf-8-sig", newline="\n") as f:
        for index, item in enumerate(entries, start=1):
            start = max(0, item["start"] - offset)
            end = max(start, item["end"] - offset)

            f.write(f"{index}\n")
            f.write(
                f"{seconds_to_srt_time(start)} --> "
                f"{seconds_to_srt_time(end)}\n"
            )
            f.write(f'{item["text"]}\n\n')


# ============================================================
# VIDEO HELPERS
# ============================================================
def get_video_duration(video_path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    return float(result.stdout.strip())


def run_ffmpeg(video_path: Path, output_path: Path, start: float, duration: float):
    # Re-encode is avoided: this uses stream copy.
    # -reset_timestamps 1 makes each part start from 00:00:00.
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(duration),
        "-map", "0",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-reset_timestamps", "1",
        str(output_path),
    ]

    print("Running:", " ".join(f'"{x}"' if " " in x else x for x in cmd))
    subprocess.run(cmd, check=True)


# ============================================================
# MAIN
# ============================================================
def main():
    if not VIDEO_FILE.exists():
        raise FileNotFoundError(f"Video not found: {VIDEO_FILE}")

    if not TRANSCRIPT_FILE.exists():
        raise FileNotFoundError(f"Transcript not found: {TRANSCRIPT_FILE}")

    # Check FFmpeg / FFprobe
    for command in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run(
                [command, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise RuntimeError(
                f"{command} was not found. Install FFmpeg and add it to PATH."
            )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("5-MINUTE VIDEO + TRANSCRIPT SPLITTER")
    print("=" * 70)
    print(f"Input : {INPUT_DIR}")
    print(f"Output: {OUTPUT_DIR}")

    duration = get_video_duration(VIDEO_FILE)
    subtitles = parse_srt(TRANSCRIPT_FILE)

    part_count = int((duration + PART_SECONDS - 1) // PART_SECONDS)

    print(f"Video duration : {duration:.2f} seconds")
    print(f"Transcript     : {len(subtitles)} subtitle entries")
    print(f"Parts          : {part_count}")
    print()

    for part_index in range(part_count):
        part_start = part_index * PART_SECONDS
        part_end = min((part_index + 1) * PART_SECONDS, duration)
        part_duration = part_end - part_start

        part_name = f"Part {part_index + 1:02d}"

        # Each part gets its own folder:
        # collection\Part 01\
        # collection\Part 02\
        part_dir = OUTPUT_DIR / part_name
        part_dir.mkdir(parents=True, exist_ok=True)

        output_video = part_dir / f"{part_name}.mp4"
        output_transcript = part_dir / "transcript.txt"

        # Select subtitles that overlap this video section.
        part_subtitles = [
            item for item in subtitles
            if item["end"] > part_start and item["start"] < part_end
        ]

        # Clip subtitle timing to the part boundaries.
        clipped = []
        for item in part_subtitles:
            clipped.append({
                "start": max(item["start"], part_start),
                "end": min(item["end"], part_end),
                "text": item["text"],
            })

        print(f"[{part_index + 1}/{part_count}] {part_name}")
        print(f"  Time: {seconds_to_srt_time(part_start)} -> "
              f"{seconds_to_srt_time(part_end)}")
        print(f"  Subtitles: {len(clipped)}")

        run_ffmpeg(
            VIDEO_FILE,
            output_video,
            part_start,
            part_duration,
        )

        write_srt(
            clipped,
            output_transcript,
            part_start,
        )

        print(f"  Video      : {output_video}")
        print(f"  Transcript : {output_transcript}")
        print()

    print("=" * 70)
    print("DONE")
    print(f"Collection folder: {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
