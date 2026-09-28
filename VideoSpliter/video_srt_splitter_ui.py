import re
import subprocess
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ============================================================
# CONFIG
# ============================================================

DEFAULT_HF_CACHE = Path(r"D:\AI Video\huggingface-cache")

PART_SECONDS = 5 * 60

TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->\s*"
    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})"
)


# ============================================================
# TIME HELPERS
# ============================================================

def srt_time_to_seconds(value: str) -> float:
    value = value.strip()

    h, m, s_ms = value.split(":", 2)
    s, ms = s_ms.split(",", 1)

    return (
        int(h) * 3600
        + int(m) * 60
        + int(s)
        + int(ms) / 1000
    )


def seconds_to_srt_time(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))

    h = total_ms // 3_600_000
    total_ms %= 3_600_000

    m = total_ms // 60_000
    total_ms %= 60_000

    s = total_ms // 1000
    ms = total_ms % 1000

    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ============================================================
# SRT PARSER
# ============================================================

def parse_srt(path: Path):

    text = path.read_text(
        encoding="utf-8-sig",
        errors="replace"
    )

    text = text.replace("\r\n", "\n")
    text = text.replace("\r", "\n")
    text = text.strip()

    blocks = re.split(r"\n\s*\n", text)

    entries = []

    for block in blocks:

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
            f"{match.group(1)}:"
            f"{match.group(2)}:"
            f"{match.group(3)},"
            f"{match.group(4)}"
        )

        end = srt_time_to_seconds(
            f"{match.group(5)}:"
            f"{match.group(6)}:"
            f"{match.group(7)},"
            f"{match.group(8)}"
        )

        subtitle_text = "\n".join(
            lines[time_line_index + 1:]
        ).strip()

        if subtitle_text:

            entries.append({
                "start": start,
                "end": end,
                "text": subtitle_text,
            })

    return entries


# ============================================================
# WRITE SRT
# ============================================================

def write_srt(entries, path: Path, offset: float = 0):

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="\n"
    ) as f:

        for index, item in enumerate(entries, start=1):

            start = max(
                0,
                item["start"] - offset
            )

            end = max(
                start,
                item["end"] - offset
            )

            f.write(f"{index}\n")

            f.write(
                f"{seconds_to_srt_time(start)} --> "
                f"{seconds_to_srt_time(end)}\n"
            )

            f.write(
                f'{item["text"]}\n\n'
            )


# ============================================================
# VIDEO
# ============================================================

def get_video_duration(video_path: Path) -> float:

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    return float(result.stdout.strip())


def run_ffmpeg(
    video_path: Path,
    output_path: Path,
    start: float,
    duration: float
):

    cmd = [
        "ffmpeg",
        "-y",

        "-ss",
        str(start),

        "-i",
        str(video_path),

        "-t",
        str(duration),

        "-map",
        "0",

        "-c",
        "copy",

        "-avoid_negative_ts",
        "make_zero",

        "-reset_timestamps",
        "1",

        str(output_path),
    ]

    subprocess.run(
        cmd,
        check=True
    )


# ============================================================
# FIND WHISPER MODEL
# ============================================================

def find_local_whisper_model(cache_dir: Path, model_name: str):

    """
    Looks for a faster-whisper model inside:

        D:\\AI Video\\huggingface-cache

    Supports common Hugging Face cache layouts.
    """

    possible_names = [
        f"faster-whisper-{model_name}",
        model_name,
        f"models--Systran--faster-whisper-{model_name}",
        f"models--mobiuslabsgmbh--faster-whisper-{model_name}",
    ]

    # --------------------------------------------------------
    # Direct folders
    # --------------------------------------------------------

    for name in possible_names:

        direct = cache_dir / name

        if direct.exists():

            if (
                (direct / "model.bin").exists()
                or (direct / "config.json").exists()
            ):
                return direct

    # --------------------------------------------------------
    # Search recursively
    # --------------------------------------------------------

    target = model_name.lower()

    for path in cache_dir.rglob("config.json"):

        parent = path.parent

        parent_text = str(parent).lower()

        if "faster-whisper" in parent_text:

            if target in parent_text:

                if (
                    (parent / "model.bin").exists()
                    or (parent / "model.safetensors").exists()
                ):
                    return parent

    # --------------------------------------------------------
    # HuggingFace snapshots
    # --------------------------------------------------------

    for path in cache_dir.rglob("snapshots"):

        if not path.is_dir():
            continue

        for snapshot in path.iterdir():

            if not snapshot.is_dir():
                continue

            if (
                (snapshot / "model.bin").exists()
                and (snapshot / "config.json").exists()
            ):

                text = str(snapshot).lower()

                if target in text:

                    return snapshot

    return None


# ============================================================
# WHISPER TRANSCRIPTION
# ============================================================

def generate_whisper_srt(
    video_path: Path,
    output_srt: Path,
    cache_dir: Path,
    model_name: str,
    language: str,
    log
):

    try:

        from faster_whisper import WhisperModel

    except ImportError:

        raise RuntimeError(
            "faster-whisper is not installed.\n\n"
            "Run:\n"
            "pip install faster-whisper"
        )

    log("")
    log("=" * 60)
    log("LOCAL WHISPER TRANSCRIPTION")
    log("=" * 60)

    log(f"Cache : {cache_dir}")
    log(f"Model : {model_name}")

    model_path = find_local_whisper_model(
        cache_dir,
        model_name
    )

    if model_path is None:

        raise RuntimeError(
            f"Could not find local Whisper model:\n\n"
            f"{model_name}\n\n"
            f"inside:\n"
            f"{cache_dir}\n\n"
            f"Make sure the model is already downloaded."
        )

    log(f"Model path: {model_path}")

    log("Loading local model...")

    # Your current laptop is Intel UHD 620,
    # so CPU INT8 is the safe default.
    model = WhisperModel(
        str(model_path),
        device="cpu",
        compute_type="int8",
        local_files_only=True,
    )

    log("Model loaded.")
    log("Transcribing...")
    log("")

    language_value = language.strip()

    if language_value.lower() in (
        "",
        "auto",
        "automatic"
    ):
        language_value = None

    segments, info = model.transcribe(
        str(video_path),
        language=language_value,
        beam_size=5,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    entries = []

    for segment in segments:

        text = segment.text.strip()

        if not text:
            continue

        entries.append({
            "start": float(segment.start),
            "end": float(segment.end),
            "text": text,
        })

        log(
            f"[{seconds_to_srt_time(segment.start)} -> "
            f"{seconds_to_srt_time(segment.end)}] "
            f"{text}"
        )

    write_srt(
        entries,
        output_srt,
        offset=0
    )

    detected_language = getattr(
        info,
        "language",
        "unknown"
    )

    log("")
    log(
        f"Detected language: {detected_language}"
    )

    log(
        f"Subtitle entries: {len(entries)}"
    )

    log(
        f"Saved: {output_srt}"
    )

    return entries


# ============================================================
# MAIN PROCESS
# ============================================================

def split_movie(
    input_dir: Path,
    video_file: Path,
    transcript_file: Path,
    output_root: Path,
    log
):

    movie_title = input_dir.name

    output_dir = (
        output_root /
        movie_title
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    log("")
    log("=" * 70)
    log("5-MINUTE VIDEO + TRANSCRIPT SPLITTER")
    log("=" * 70)

    log(f"Movie       : {movie_title}")
    log(f"Video       : {video_file}")
    log(f"Transcript  : {transcript_file}")
    log(f"Output      : {output_dir}")

    duration = get_video_duration(
        video_file
    )

    subtitles = parse_srt(
        transcript_file
    )

    part_count = int(
        (duration + PART_SECONDS - 1)
        // PART_SECONDS
    )

    log("")
    log(
        f"Video duration : "
        f"{duration:.2f} seconds"
    )

    log(
        f"Transcript     : "
        f"{len(subtitles)} entries"
    )

    log(
        f"Parts          : "
        f"{part_count}"
    )

    log("")

    for part_index in range(part_count):

        part_start = (
            part_index *
            PART_SECONDS
        )

        part_end = min(
            (part_index + 1) *
            PART_SECONDS,
            duration
        )

        part_duration = (
            part_end -
            part_start
        )

        part_name = (
            f"Part {part_index + 1:02d}"
        )

        part_dir = (
            output_dir /
            part_name
        )

        part_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        output_video = (
            part_dir /
            f"{part_name}.mp4"
        )

        output_transcript = (
            part_dir /
            "transcript.srt"
        )

        # ----------------------------------------------------
        # Select overlapping subtitles
        # ----------------------------------------------------

        part_subtitles = [
            item
            for item in subtitles
            if (
                item["end"] > part_start
                and
                item["start"] < part_end
            )
        ]

        clipped = []

        for item in part_subtitles:

            clipped.append({
                "start": max(
                    item["start"],
                    part_start
                ),

                "end": min(
                    item["end"],
                    part_end
                ),

                "text": item["text"],
            })

        log(
            f"[{part_index + 1}/{part_count}] "
            f"{part_name}"
        )

        log(
            f"  Time: "
            f"{seconds_to_srt_time(part_start)} "
            f"-> "
            f"{seconds_to_srt_time(part_end)}"
        )

        log(
            f"  Subtitles: "
            f"{len(clipped)}"
        )

        # ----------------------------------------------------
        # Video
        # ----------------------------------------------------

        if output_video.exists():

            log(
                "  Video already exists. "
                "Skipping..."
            )

        else:

            log("  Creating video...")

            run_ffmpeg(
                video_file,
                output_video,
                part_start,
                part_duration
            )

        # ----------------------------------------------------
        # Transcript
        # ----------------------------------------------------

        write_srt(
            clipped,
            output_transcript,
            part_start
        )

        log(
            f"  Video      : "
            f"{output_video}"
        )

        log(
            f"  Transcript : "
            f"{output_transcript}"
        )

        log("")

    log("=" * 70)
    log("DONE")
    log("=" * 70)

    log(
        f"Collection folder:\n{output_dir}"
    )


# ============================================================
# GUI
# ============================================================

class MovieSplitterApp:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "Movie Video + Whisper Transcript Splitter"
        )

        self.root.geometry(
            "1000x720"
        )

        self.root.minsize(
            850,
            600
        )

        self.input_dir = None
        self.video_file = None
        self.transcript_file = None

        self.create_widgets()

    # --------------------------------------------------------
    # LOG
    # --------------------------------------------------------

    def log(self, message):

        def update():

            self.log_text.insert(
                tk.END,
                str(message) + "\n"
            )

            self.log_text.see(
                tk.END
            )

        self.root.after(
            0,
            update
        )

    # --------------------------------------------------------
    # FOLDER
    # --------------------------------------------------------

    def browse_folder(self):

        folder = filedialog.askdirectory(
            title="Select Movie Folder"
        )

        if not folder:
            return

        self.input_dir = Path(folder)

        self.folder_var.set(
            str(self.input_dir)
        )

        # Automatically find video
        auto_video = (
            self.input_dir /
            "video.mp4"
        )

        if auto_video.exists():

            self.video_file = auto_video

            self.video_var.set(
                str(auto_video)
            )

        # Automatically find transcript
        candidates = [
            self.input_dir / "transcript.srt",
            self.input_dir / "transcript.txt",
            self.input_dir / "subtitle.srt",
            self.input_dir / "subtitles.srt",
        ]

        found = None

        for candidate in candidates:

            if candidate.exists():

                found = candidate
                break

        if found:

            self.transcript_file = found

            self.transcript_var.set(
                str(found)
            )

        self.log(
            f"Movie folder selected: "
            f"{self.input_dir}"
        )

    # --------------------------------------------------------
    # VIDEO
    # --------------------------------------------------------

    def browse_video(self):

        filename = filedialog.askopenfilename(
            title="Select Video",
            filetypes=[
                (
                    "Video files",
                    "*.mp4 *.mkv *.avi *.mov *.m4v"
                ),
                (
                    "All files",
                    "*.*"
                ),
            ]
        )

        if not filename:
            return

        self.video_file = Path(
            filename
        )

        self.video_var.set(
            str(self.video_file)
        )

    # --------------------------------------------------------
    # TRANSCRIPT
    # --------------------------------------------------------

    def browse_transcript(self):

        filename = filedialog.askopenfilename(
            title="Select Transcript / SRT",
            filetypes=[
                (
                    "Subtitle / Transcript",
                    "*.srt *.txt"
                ),
                (
                    "SRT",
                    "*.srt"
                ),
                (
                    "Text",
                    "*.txt"
                ),
                (
                    "All files",
                    "*.*"
                ),
            ]
        )

        if not filename:
            return

        self.transcript_file = Path(
            filename
        )

        self.transcript_var.set(
            str(self.transcript_file)
        )

        self.source_var.set(
            "Existing transcript"
        )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    def browse_output(self):

        folder = filedialog.askdirectory(
            title="Select Collection Output Folder"
        )

        if not folder:
            return

        self.output_var.set(
            str(Path(folder))
        )

    # --------------------------------------------------------
    # CACHE
    # --------------------------------------------------------

    def browse_cache(self):

        folder = filedialog.askdirectory(
            title="Select Hugging Face Cache"
        )

        if not folder:
            return

        self.cache_var.set(
            str(Path(folder))
        )

    # --------------------------------------------------------
    # GENERATE WHISPER
    # --------------------------------------------------------

    def generate_transcript(self):

        if not self.video_file:

            messagebox.showerror(
                "Missing Video",
                "Please select a video first."
            )

            return

        cache_dir = Path(
            self.cache_var.get().strip()
        )

        if not cache_dir.exists():

            messagebox.showerror(
                "Cache Not Found",
                f"Cache folder does not exist:\n\n"
                f"{cache_dir}"
            )

            return

        output_srt = (
            self.video_file.parent /
            "transcript.srt"
        )

        model_name = (
            self.model_var.get()
        )

        language = (
            self.language_var.get()
        )

        self.disable_buttons()

        def worker():

            try:

                entries = generate_whisper_srt(
                    self.video_file,
                    output_srt,
                    cache_dir,
                    model_name,
                    language,
                    self.log
                )

                def success():

                    self.transcript_file = (
                        output_srt
                    )

                    self.transcript_var.set(
                        str(output_srt)
                    )

                    self.source_var.set(
                        "Local Whisper"
                    )

                    self.enable_buttons()

                    messagebox.showinfo(
                        "Complete",
                        f"Transcript created:\n\n"
                        f"{output_srt}\n\n"
                        f"Entries: {len(entries)}"
                    )

                self.root.after(
                    0,
                    success
                )

            except Exception as e:

                error_text = (
                    str(e)
                    +
                    "\n\n"
                    +
                    traceback.format_exc()
                )

                self.log(
                    error_text
                )

                def fail():

                    self.enable_buttons()

                    messagebox.showerror(
                        "Whisper Error",
                        str(e)
                    )

                self.root.after(
                    0,
                    fail
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # --------------------------------------------------------
    # SPLIT
    # --------------------------------------------------------

    def start_split(self):

        if not self.input_dir:

            messagebox.showerror(
                "Missing Folder",
                "Please select the movie folder."
            )

            return

        if not self.video_file:

            messagebox.showerror(
                "Missing Video",
                "Please select the video."
            )

            return

        if not self.transcript_file:

            messagebox.showerror(
                "Missing Transcript",
                "Please browse for an SRT/TXT or "
                "generate one using Whisper."
            )

            return

        if not self.video_file.exists():

            messagebox.showerror(
                "Video Not Found",
                str(self.video_file)
            )

            return

        if not self.transcript_file.exists():

            messagebox.showerror(
                "Transcript Not Found",
                str(self.transcript_file)
            )

            return

        output_root = Path(
            self.output_var.get().strip()
        )

        output_root.mkdir(
            parents=True,
            exist_ok=True
        )

        self.disable_buttons()

        def worker():

            try:

                split_movie(
                    self.input_dir,
                    self.video_file,
                    self.transcript_file,
                    output_root,
                    self.log
                )

                def success():

                    self.enable_buttons()

                    messagebox.showinfo(
                        "Complete",
                        "Video splitting completed."
                    )

                self.root.after(
                    0,
                    success
                )

            except Exception as e:

                self.log(
                    traceback.format_exc()
                )

                def fail():

                    self.enable_buttons()

                    messagebox.showerror(
                        "Error",
                        str(e)
                    )

                self.root.after(
                    0,
                    fail
                )

        threading.Thread(
            target=worker,
            daemon=True
        ).start()

    # --------------------------------------------------------
    # BUTTON STATE
    # --------------------------------------------------------

    def disable_buttons(self):

        for button in self.action_buttons:

            button.config(
                state=tk.DISABLED
            )

    def enable_buttons(self):

        for button in self.action_buttons:

            button.config(
                state=tk.NORMAL
            )

    # --------------------------------------------------------
    # GUI
    # --------------------------------------------------------

    def create_widgets(self):

        main = ttk.Frame(
            self.root,
            padding=15
        )

        main.pack(
            fill=tk.BOTH,
            expand=True
        )

        title = ttk.Label(
            main,
            text="Movie Splitter + Local Whisper",
            font=("Segoe UI", 18, "bold")
        )

        title.pack(
            anchor="w",
            pady=(0, 15)
        )

        # ----------------------------------------------------
        # Movie Folder
        # ----------------------------------------------------

        ttk.Label(
            main,
            text="Movie Folder"
        ).pack(
            anchor="w"
        )

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(3, 10))

        self.folder_var = tk.StringVar()

        ttk.Entry(
            row,
            textvariable=self.folder_var
        ).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True
        )

        ttk.Button(
            row,
            text="Browse...",
            command=self.browse_folder
        ).pack(
            side=tk.LEFT,
            padx=(5, 0)
        )

        # ----------------------------------------------------
        # Video
        # ----------------------------------------------------

        ttk.Label(
            main,
            text="Video"
        ).pack(
            anchor="w"
        )

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(3, 10))

        self.video_var = tk.StringVar()

        ttk.Entry(
            row,
            textvariable=self.video_var
        ).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True
        )

        ttk.Button(
            row,
            text="Browse Video",
            command=self.browse_video
        ).pack(
            side=tk.LEFT,
            padx=(5, 0)
        )

        # ----------------------------------------------------
        # Transcript
        # ----------------------------------------------------

        ttk.Label(
            main,
            text="Transcript / SRT"
        ).pack(
            anchor="w"
        )

        row = ttk.Frame(main)
        row.pack(fill=tk.X, pady=(3, 10))

        self.transcript_var = tk.StringVar()

        ttk.Entry(
            row,
            textvariable=self.transcript_var
        ).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True
        )

        ttk.Button(
            row,
            text="Browse SRT / TXT",
            command=self.browse_transcript
        ).pack(
            side=tk.LEFT,
            padx=(5, 0)
        )

        # ----------------------------------------------------
        # Whisper
        # ----------------------------------------------------

        whisper_frame = ttk.LabelFrame(
            main,
            text="Local Whisper",
            padding=10
        )

        whisper_frame.pack(
            fill=tk.X,
            pady=(5, 10)
        )

        # Cache
        ttk.Label(
            whisper_frame,
            text="Hugging Face Cache"
        ).grid(
            row=0,
            column=0,
            sticky="w",
            padx=(0, 5),
            pady=3
        )

        self.cache_var = tk.StringVar(
            value=str(DEFAULT_HF_CACHE)
        )

        ttk.Entry(
            whisper_frame,
            textvariable=self.cache_var
        ).grid(
            row=0,
            column=1,
            sticky="ew",
            pady=3
        )

        ttk.Button(
            whisper_frame,
            text="Browse",
            command=self.browse_cache
        ).grid(
            row=0,
            column=2,
            padx=(5, 0),
            pady=3
        )

        # Model
        ttk.Label(
            whisper_frame,
            text="Model"
        ).grid(
            row=1,
            column=0,
            sticky="w",
            pady=3
        )

        self.model_var = tk.StringVar(
            value="small"
        )

        model_combo = ttk.Combobox(
            whisper_frame,
            textvariable=self.model_var,
            values=[
                "tiny",
                "base",
                "small",
                "medium",
                "large-v1",
                "large-v2",
                "large-v3",
                "turbo",
            ],
            state="readonly"
        )

        model_combo.grid(
            row=1,
            column=1,
            sticky="w",
            pady=3
        )

        # Language
        ttk.Label(
            whisper_frame,
            text="Language"
        ).grid(
            row=2,
            column=0,
            sticky="w",
            pady=3
        )

        self.language_var = tk.StringVar(
            value="zh"
        )

        language_combo = ttk.Combobox(
            whisper_frame,
            textvariable=self.language_var,
            values=[
                "auto",
                "zh",
                "en",
                "ja",
                "ko",
                "th",
                "vi",
                "km",
            ],
            state="readonly",
            width=15
        )

        language_combo.grid(
            row=2,
            column=1,
            sticky="w",
            pady=3
        )

        ttk.Label(
            whisper_frame,
            text="For Chinese subtitles use: zh"
        ).grid(
            row=2,
            column=2,
            sticky="w",
            padx=10
        )

        whisper_frame.columnconfigure(
            1,
            weight=1
        )

        # ----------------------------------------------------
        # Source
        # ----------------------------------------------------

        row = ttk.Frame(main)
        row.pack(
            fill=tk.X,
            pady=(0, 10)
        )

        ttk.Label(
            row,
            text="Transcript source:"
        ).pack(
            side=tk.LEFT
        )

        self.source_var = tk.StringVar(
            value="Not selected"
        )

        ttk.Label(
            row,
            textvariable=self.source_var,
            font=("Segoe UI", 9, "bold")
        ).pack(
            side=tk.LEFT,
            padx=5
        )

        # ----------------------------------------------------
        # Output
        # ----------------------------------------------------

        ttk.Label(
            main,
            text="Collection Output"
        ).pack(
            anchor="w"
        )

        row = ttk.Frame(main)
        row.pack(
            fill=tk.X,
            pady=(3, 10)
        )

        self.output_var = tk.StringVar(
            value=str(
                Path(__file__).resolve().parent /
                "collection"
            )
        )

        ttk.Entry(
            row,
            textvariable=self.output_var
        ).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True
        )

        ttk.Button(
            row,
            text="Browse",
            command=self.browse_output
        ).pack(
            side=tk.LEFT,
            padx=(5, 0)
        )

        # ----------------------------------------------------
        # Buttons
        # ----------------------------------------------------

        button_frame = ttk.Frame(main)

        button_frame.pack(
            fill=tk.X,
            pady=(5, 10)
        )

        self.action_buttons = []

        generate_button = ttk.Button(
            button_frame,
            text="Generate Transcript with Whisper",
            command=self.generate_transcript
        )

        generate_button.pack(
            side=tk.LEFT,
            padx=(0, 5)
        )

        self.action_buttons.append(
            generate_button
        )

        split_button = ttk.Button(
            button_frame,
            text="Split Video into 5-Min Parts",
            command=self.start_split
        )

        split_button.pack(
            side=tk.LEFT
        )

        self.action_buttons.append(
            split_button
        )

        # ----------------------------------------------------
        # Log
        # ----------------------------------------------------

        ttk.Label(
            main,
            text="Log"
        ).pack(
            anchor="w"
        )

        log_frame = ttk.Frame(main)

        log_frame.pack(
            fill=tk.BOTH,
            expand=True
        )

        scrollbar = ttk.Scrollbar(
            log_frame
        )

        scrollbar.pack(
            side=tk.RIGHT,
            fill=tk.Y
        )

        self.log_text = tk.Text(
            log_frame,
            wrap=tk.WORD,
            yscrollcommand=scrollbar.set,
            font=("Consolas", 9)
        )

        self.log_text.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True
        )

        scrollbar.config(
            command=self.log_text.yview
        )

        self.log(
            "Ready."
        )

        self.log(
            f"Default Whisper cache: "
            f"{DEFAULT_HF_CACHE}"
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    root = tk.Tk()

    app = MovieSplitterApp(
        root
    )

    root.mainloop()