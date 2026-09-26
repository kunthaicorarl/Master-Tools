import re
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

PART_SECONDS = 5 * 60

TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)

def srt_time_to_seconds(value):
    h, m, s_ms = value.split(":", 2)
    s, ms = s_ms.split(",", 1)
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

def seconds_to_srt_time(seconds):
    total_ms = max(0, round(seconds * 1000))
    h = total_ms // 3600000
    total_ms %= 3600000
    m = total_ms // 60000
    total_ms %= 60000
    s = total_ms // 1000
    ms = total_ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def parse_srt(path):
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    entries = []

    for block in re.split(r"\n\s*\n", text):
        lines = block.split("\n")
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
            entries.append({"start": start, "end": end, "text": subtitle_text})

    return entries

def write_srt(entries, path, offset):
    with path.open("w", encoding="utf-8-sig", newline="\n") as f:
        for index, item in enumerate(entries, 1):
            start = max(0, item["start"] - offset)
            end = max(start, item["end"] - offset)
            f.write(f"{index}\n")
            f.write(f"{seconds_to_srt_time(start)} --> {seconds_to_srt_time(end)}\n")
            f.write(f'{item["text"]}\n\n')

def check_ffmpeg():
    for command in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run(
                [command, "-version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise RuntimeError(
                f"{command} was not found. Install FFmpeg and add it to PATH."
            )

def get_video_duration(video_path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video_path)
        ],
        capture_output=True, text=True, check=True
    )
    return float(result.stdout.strip())

def run_ffmpeg(video_path, output_path, start, duration):
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(duration),
        "-map", "0",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
        "-reset_timestamps", "1",
        str(output_path)
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("5-Minute Video + SRT Splitter")
        self.root.geometry("780x560")
        self.root.minsize(700, 500)

        self.video_var = tk.StringVar()
        self.srt_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Select video and SRT file.")
        self.progress_var = tk.DoubleVar(value=0)

        self.build_ui()

    def build_ui(self):
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        ttk.Label(
            outer, text="5-Minute Video + Transcript Splitter",
            font=("Segoe UI", 20, "bold")
        ).pack(anchor="w", pady=(0, 18))

        video_frame = ttk.LabelFrame(outer, text="Video File", padding=12)
        video_frame.pack(fill="x", pady=7)
        ttk.Entry(video_frame, textvariable=self.video_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(video_frame, text="Browse…", command=self.select_video).pack(
            side="left", padx=(10, 0)
        )

        srt_frame = ttk.LabelFrame(outer, text="Transcript (.srt)", padding=12)
        srt_frame.pack(fill="x", pady=7)
        ttk.Entry(srt_frame, textvariable=self.srt_var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(srt_frame, text="Browse…", command=self.select_srt).pack(
            side="left", padx=(10, 0)
        )

        info = ttk.LabelFrame(outer, text="Settings", padding=12)
        info.pack(fill="x", pady=7)
        ttk.Label(
            info,
            text="Each part = 5 minutes\n"
                 "Output: collection\\<video folder name>\\Part 01, Part 02, ..."
        ).pack(anchor="w")

        self.start_button = ttk.Button(
            outer, text="START SPLITTING", command=self.start
        )
        self.start_button.pack(fill="x", pady=(16, 8), ipady=8)

        ttk.Progressbar(
            outer, variable=self.progress_var, maximum=100
        ).pack(fill="x", pady=5)

        ttk.Label(outer, textvariable=self.status_var).pack(anchor="w", pady=5)

        log_frame = ttk.LabelFrame(outer, text="Log", padding=6)
        log_frame.pack(fill="both", expand=True, pady=7)

        self.log = tk.Text(log_frame, height=10, wrap="word", font=("Consolas", 9))
        self.log.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)

    def select_video(self):
        path = filedialog.askopenfilename(
            title="Select Video File",
            filetypes=[
                ("Video files", "*.mp4 *.mkv *.avi *.mov *.webm"),
                ("All files", "*.*")
            ]
        )
        if path:
            self.video_var.set(path)
            possible = Path(path).parent / "transcript.srt"
            if possible.exists() and not self.srt_var.get():
                self.srt_var.set(str(possible))

    def select_srt(self):
        path = filedialog.askopenfilename(
            title="Select SRT Transcript",
            filetypes=[("SubRip subtitles", "*.srt"), ("All files", "*.*")]
        )
        if path:
            self.srt_var.set(path)

    def log_message(self, message):
        self.root.after(0, self._append_log, message)

    def _append_log(self, message):
        self.log.insert("end", message + "\n")
        self.log.see("end")

    def set_status(self, message):
        self.root.after(0, self.status_var.set, message)

    def start(self):
        video = self.video_var.get().strip()
        srt = self.srt_var.get().strip()

        if not video:
            messagebox.showwarning("Video required", "Please select the video file.")
            return
        if not srt:
            messagebox.showwarning("SRT required", "Please select the .srt file.")
            return

        video_path = Path(video)
        srt_path = Path(srt)

        if not video_path.exists():
            messagebox.showerror("Error", "Video file does not exist.")
            return
        if not srt_path.exists():
            messagebox.showerror("Error", "SRT file does not exist.")
            return

        self.start_button.config(state="disabled")
        self.progress_var.set(0)
        self.log.delete("1.0", "end")

        threading.Thread(
            target=self.worker, args=(video_path, srt_path), daemon=True
        ).start()

    def worker(self, video_path, srt_path):
        try:
            self.run_splitter(video_path, srt_path)
        except Exception as e:
            self.log_message(f"ERROR: {e}")
            self.set_status("Failed.")
            self.root.after(0, messagebox.showerror, "Failed", str(e))
        finally:
            self.root.after(0, lambda: self.start_button.config(state="normal"))

    def run_splitter(self, video_path, srt_path):
        check_ffmpeg()

        current = Path(__file__).resolve().parent
        movie_title = video_path.parent.name
        output_dir = current / "collection" / movie_title
        output_dir.mkdir(parents=True, exist_ok=True)

        self.log_message("=" * 70)
        self.log_message("5-MINUTE VIDEO + TRANSCRIPT SPLITTER")
        self.log_message("=" * 70)
        self.log_message(f"Video : {video_path}")
        self.log_message(f"SRT   : {srt_path}")
        self.log_message(f"Output: {output_dir}")
        self.log_message("")

        duration = get_video_duration(video_path)
        subtitles = parse_srt(srt_path)
        part_count = int((duration + PART_SECONDS - 1) // PART_SECONDS)

        self.log_message(f"Video duration : {duration:.2f} seconds")
        self.log_message(f"Transcript     : {len(subtitles)} subtitle entries")
        self.log_message(f"Parts          : {part_count}")
        self.log_message("")

        for part_index in range(part_count):
            part_start = part_index * PART_SECONDS
            part_end = min((part_index + 1) * PART_SECONDS, duration)
            part_duration = part_end - part_start

            part_name = f"Part {part_index + 1:02d}"
            part_dir = output_dir / part_name
            part_dir.mkdir(parents=True, exist_ok=True)

            output_video = part_dir / f"{part_name}.mp4"
            output_transcript = part_dir / "transcript.srt"

            part_subtitles = [
                item for item in subtitles
                if item["end"] > part_start and item["start"] < part_end
            ]

            clipped = [
                {
                    "start": max(item["start"], part_start),
                    "end": min(item["end"], part_end),
                    "text": item["text"]
                }
                for item in part_subtitles
            ]

            self.log_message(f"[{part_index + 1}/{part_count}] {part_name}")
            self.log_message(
                f"  Time      : {seconds_to_srt_time(part_start)} -> "
                f"{seconds_to_srt_time(part_end)}"
            )
            self.log_message(f"  Subtitles : {len(clipped)}")

            self.set_status(
                f"Processing {part_name} ({part_index + 1}/{part_count})..."
            )

            run_ffmpeg(video_path, output_video, part_start, part_duration)
            write_srt(clipped, output_transcript, part_start)

            self.log_message(f"  Video      : {output_video}")
            self.log_message(f"  Transcript : {output_transcript}")
            self.log_message("")

            self.root.after(
                0, self.progress_var.set, ((part_index + 1) / part_count) * 100
            )

        self.log_message("=" * 70)
        self.log_message("DONE")
        self.log_message(f"Collection folder: {output_dir}")
        self.log_message("=" * 70)
        self.set_status("Done.")

        self.root.after(
            0,
            messagebox.showinfo,
            "Completed",
            f"All video parts and SRT files were created.\n\n{output_dir}"
        )

def main():
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.1)
    except Exception:
        pass
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
