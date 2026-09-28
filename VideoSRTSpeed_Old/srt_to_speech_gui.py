"""
SRT -> Speech TTS GUI
---------------------
Desktop GUI using Tkinter + edge-tts.

Features:
- Select .srt
- Select TTS language/voice
- Adjustable rate, volume, pitch
- Generates one synchronized WAV per subtitle and a final WAV
- Subtitle timing is preserved: speech starts at the SRT start time.
- If speech is longer than its subtitle slot, the next subtitle is not overwritten;
  the generated audio is placed at its start time, so overlaps can occur naturally.
- Optional MP3 output if ffmpeg is installed.
"""

import asyncio
import re
import shutil
import subprocess
import threading
import wave
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import edge_tts
except ImportError:
    edge_tts = None

APP_TITLE = "SRT Folder Parts → Speech TTS Generator"
DEFAULT_VOICE = "km-KH-SreymomNeural"


def parse_timestamp(ts: str) -> float:
    m = re.match(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})$", ts.strip())
    if not m:
        raise ValueError(f"Invalid timestamp: {ts}")
    h, mi, s, ms = map(int, m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def parse_srt(path: Path):
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    items = []

    for block in blocks:
        lines = [x.strip("\ufeff") for x in block.split("\n")]
        if len(lines) < 2:
            continue

        time_idx = next((i for i, x in enumerate(lines) if "-->" in x), None)
        if time_idx is None:
            continue

        left, right = [x.strip() for x in lines[time_idx].split("-->", 1)]
        start = parse_timestamp(left.split()[0])
        end = parse_timestamp(right.split()[0])

        subtitle = "\n".join(lines[time_idx + 1:]).strip()
        subtitle = re.sub(r"<[^>]+>", "", subtitle)
        subtitle = re.sub(r"\{\\.*?\}", "", subtitle)
        subtitle = re.sub(r"\s+", " ", subtitle).strip()

        if subtitle and end >= start:
            items.append({
                "index": len(items) + 1,
                "start": start,
                "end": end,
                "text": subtitle
            })

    return items


def seconds_to_ms(seconds):
    return int(round(seconds * 1000))


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("900x720")
        self.root.minsize(800, 650)

        self.srt_path = tk.StringVar()
        self.selected_srts = []
        self.out_dir = tk.StringVar()
        self.source_folder = tk.StringVar()
        self.voice = tk.StringVar(value=DEFAULT_VOICE)
        self.rate = tk.IntVar(value=0)
        self.volume = tk.IntVar(value=0)
        self.pitch = tk.IntVar(value=0)
        self.format = tk.StringVar(value="WAV")
        self.status = tk.StringVar(value="Ready.")
        self.progress = tk.DoubleVar(value=0)

        self.build_ui()

    def build_ui(self):
        pad = {"padx": 12, "pady": 7}

        title = ttk.Label(self.root, text="SRT → Speech Generator",
                          font=("Segoe UI", 20, "bold"))
        title.pack(anchor="w", **pad)

        frame = ttk.LabelFrame(self.root, text="1. Input")
        frame.pack(fill="x", **pad)

        ttk.Label(frame, text="Source Folder:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(
            frame, textvariable=self.source_folder
        ).grid(row=0, column=1, sticky="ew", **pad)
        ttk.Button(
            frame, text="Browse Folder…",
            command=self.browse_folder
        ).grid(row=0, column=2, **pad)

        ttk.Label(frame, text="SRT Parts:").grid(row=1, column=0, sticky="nw", **pad)

        list_frame = ttk.Frame(frame)
        list_frame.grid(row=1, column=1, columnspan=2, sticky="ew", **pad)
        list_frame.columnconfigure(0, weight=1)

        self.srt_listbox = tk.Listbox(
            list_frame, height=6, selectmode=tk.EXTENDED,
            exportselection=False
        )
        self.srt_listbox.grid(row=0, column=0, sticky="ew")

        list_scroll = ttk.Scrollbar(
            list_frame, orient="vertical", command=self.srt_listbox.yview
        )
        list_scroll.grid(row=0, column=1, sticky="ns")
        self.srt_listbox.configure(yscrollcommand=list_scroll.set)
        self.srt_listbox.bind("<Control-a>", self.select_all_srts)
        self.srt_listbox.bind("<<ListboxSelect>>", self.on_part_selection)

        ttk.Label(
            list_frame,
            text="Select one part for Single, or select multiple parts for Multiple.",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))

        btn_frame = ttk.Frame(list_frame)
        btn_frame.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        ttk.Button(
            btn_frame, text="Browse Multiple…",
            command=self.browse_srt
        ).pack(side="left")

        ttk.Button(
            btn_frame, text="Add More…",
            command=self.add_srt_files
        ).pack(side="left", padx=6)

        ttk.Button(
            btn_frame, text="Remove Selected",
            command=self.remove_selected_srts
        ).pack(side="left", padx=6)

        ttk.Button(
            btn_frame, text="Clear All",
            command=self.clear_srts
        ).pack(side="left")

        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Output folder:").grid(row=2, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.out_dir).grid(
            row=2, column=1, sticky="ew", **pad)
        ttk.Button(frame, text="Browse…", command=self.browse_out).grid(
            row=2, column=2, **pad)

        settings = ttk.LabelFrame(self.root, text="2. Speech settings")
        settings.pack(fill="x", **pad)

        ttk.Label(settings, text="Voice:").grid(row=0, column=0, sticky="w", **pad)
        self.voice_combo = ttk.Combobox(
            settings,
            textvariable=self.voice,
            values=[
                "km-KH-SreymomNeural",
                "km-KH-PisethNeural",
                "zh-CN-XiaoxiaoNeural",
                "zh-CN-YunxiNeural",
                "en-US-AriaNeural",
                "en-US-GuyNeural",
            ],
            width=32
        )
        self.voice_combo.grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(settings, text="Rate (%):").grid(row=1, column=0, sticky="w", **pad)
        ttk.Spinbox(settings, from_=-50, to=100, textvariable=self.rate,
                    width=10).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(settings, text="Volume (%):").grid(row=2, column=0, sticky="w", **pad)
        ttk.Spinbox(settings, from_=-50, to=50, textvariable=self.volume,
                    width=10).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(settings, text="Pitch (Hz):").grid(row=3, column=0, sticky="w", **pad)
        ttk.Spinbox(settings, from_=-50, to=50, textvariable=self.pitch,
                    width=10).grid(row=3, column=1, sticky="w", **pad)

        ttk.Label(settings, text="Final format:").grid(row=4, column=0, sticky="w", **pad)
        ttk.Combobox(settings, textvariable=self.format,
                     values=["WAV", "MP3"], state="readonly",
                     width=10).grid(row=4, column=1, sticky="w", **pad)

        action = ttk.Frame(self.root)
        action.pack(fill="x", **pad)

        self.generate_btn = ttk.Button(
            action, text="Generate Selected",
            command=self.start_generation
        )
        self.generate_btn.pack(side="left")

        self.generate_single_btn = ttk.Button(
            action, text="Generate Single",
            command=self.start_single_generation
        )
        self.generate_single_btn.pack(side="left", padx=8)

        self.generate_multi_btn = ttk.Button(
            action, text="Generate Multiple",
            command=self.start_multiple_generation
        )
        self.generate_multi_btn.pack(side="left")

        ttk.Button(
            action, text="Clear Log",
            command=lambda: self.log.delete("1.0", "end")
        ).pack(side="left", padx=8)

        self.progress_bar = ttk.Progressbar(
            self.root, variable=self.progress, maximum=100)
        self.progress_bar.pack(fill="x", **pad)

        ttk.Label(self.root, textvariable=self.status).pack(anchor="w", padx=12)

        log_frame = ttk.LabelFrame(self.root, text="Progress / Log")
        log_frame.pack(fill="both", expand=True, **pad)

        self.log = tk.Text(log_frame, wrap="word", height=14)
        self.log.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set)

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Folder Containing SRT Parts")
        if not folder:
            return

        self.source_folder.set(folder)

        # Show all SRT files in the selected folder.
        paths = sorted(
            Path(folder).glob("*.srt"),
            key=lambda p: (
                self._natural_sort_key(p.stem),
                p.name.lower()
            )
        )

        if not paths:
            self.clear_srts()
            messagebox.showinfo(
                "No SRT files",
                "No .srt files were found in the selected folder."
            )
            return

        self.clear_srts()
        self._add_srt_paths([str(p) for p in paths])

        if not self.out_dir.get():
            self.out_dir.set(str(Path(folder) / "tts_output"))

        self.status.set(f"Found {len(paths)} SRT part(s) in folder.")

    @staticmethod
    def _natural_sort_key(value):
        return [
            int(x) if x.isdigit() else x.lower()
            for x in re.split(r"(\d+)", value)
        ]

    def browse_srt(self):
        paths = filedialog.askopenfilenames(
            title="Select SRT Parts",
            filetypes=[("SubRip subtitles", "*.srt"), ("All files", "*.*")]
        )
        if paths:
            self._add_srt_paths(paths)
            try:
                common_parent = Path(paths[0]).parent
                if all(Path(p).parent == common_parent for p in paths):
                    self.source_folder.set(str(common_parent))
            except Exception:
                pass

    def add_srt_files(self):
        paths = filedialog.askopenfilenames(
            title="Add More SRT Parts",
            filetypes=[("SubRip subtitles", "*.srt"), ("All files", "*.*")]
        )
        if paths:
            self._add_srt_paths(paths)

    def _add_srt_paths(self, paths):
        existing = {str(Path(p).resolve()).lower() for p in self.selected_srts}
        for path in paths:
            normalized = str(Path(path).resolve())
            if normalized.lower() not in existing:
                self.selected_srts.append(normalized)
                self.srt_listbox.insert("end", Path(normalized).name)
                existing.add(normalized.lower())

        if self.selected_srts:
            self.srt_path.set(self.selected_srts[0])
            if not self.out_dir.get():
                self.out_dir.set(str(Path(self.selected_srts[0]).parent / "tts_output"))

        self.status.set(f"{len(self.selected_srts)} SRT part(s) selected.")

    def on_part_selection(self, event=None):
        indices = self.srt_listbox.curselection()
        if not indices:
            self.status.set(f"{len(self.selected_srts)} SRT part(s) loaded.")
            return

        if len(indices) == 1:
            path = self.selected_srts[indices[0]]
            self.srt_path.set(path)
            self.status.set(f"Selected: {Path(path).name}")
        else:
            self.status.set(f"{len(indices)} SRT part(s) selected.")

    def select_all_srts(self, event=None):
        self.srt_listbox.selection_set(0, "end")
        return "break"

    def remove_selected_srts(self):
        indices = list(self.srt_listbox.curselection())
        if not indices:
            return

        for i in reversed(indices):
            self.srt_listbox.delete(i)
            del self.selected_srts[i]

        if self.selected_srts:
            self.srt_path.set(self.selected_srts[0])
        else:
            self.srt_path.set("")

        self.status.set(f"{len(self.selected_srts)} SRT part(s) selected.")

    def clear_srts(self):
        self.selected_srts.clear()
        self.srt_listbox.delete(0, "end")
        self.srt_path.set("")
        self.status.set("No SRT parts selected.")

    def browse_out(self):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.out_dir.set(path)

    def log_msg(self, msg):
        self.root.after(0, lambda: (
            self.log.insert("end", msg + "\n"),
            self.log.see("end")
        ))

    def _validate_generation(self):
        if edge_tts is None:
            messagebox.showerror(
                "Missing package",
                "Install edge-tts first:\n\npip install edge-tts"
            )
            return False

        if not self.selected_srts:
            messagebox.showwarning(
                "Missing SRT",
                "Please select one or more .srt parts."
            )
            return False

        if not self.out_dir.get():
            self.out_dir.set(
                str(Path(self.selected_srts[0]).parent / "tts_output")
            )

        return True

    def start_generation(self):
        # Generate all currently selected parts.
        self.start_multiple_generation()

    def start_single_generation(self):
        if not self._validate_generation():
            return

        selection = self.srt_listbox.curselection()
        if not selection:
            messagebox.showwarning(
                "Select one part",
                "Select exactly one SRT part from the list."
            )
            return

        if len(selection) != 1:
            messagebox.showwarning(
                "Single generation",
                "Please select only one SRT part."
            )
            return

        selected_path = self.selected_srts[selection[0]]
        threading.Thread(
            target=self.generate_thread,
            args=([selected_path],),
            daemon=True
        ).start()

    def start_multiple_generation(self):
        if not self._validate_generation():
            return

        selection = list(self.srt_listbox.curselection())
        if not selection:
            paths = list(self.selected_srts)
        else:
            paths = [self.selected_srts[i] for i in selection]

        threading.Thread(
            target=self.generate_thread,
            args=(paths,),
            daemon=True
        ).start()

    def generate_thread(self, paths):
        buttons = (
            self.generate_btn,
            self.generate_single_btn,
            self.generate_multi_btn,
        )
        self.root.after(
            0, lambda: [b.config(state="disabled") for b in buttons]
        )
        try:
            asyncio.run(self.generate_multiple(paths))
        except Exception as e:
            self.log_msg(f"ERROR: {e}")
            self.root.after(
                0,
                lambda err=str(e): messagebox.showerror(
                    "Generation failed", err
                )
            )
        finally:
            self.root.after(
                0, lambda: [b.config(state="normal") for b in buttons]
            )

    async def generate_multiple(self, paths):
        total_parts = len(paths)
        self.root.after(0, lambda: self.progress.set(0))

        for part_no, srt_path in enumerate(paths, 1):
            self.log_msg("=" * 70)
            self.log_msg(f"PART {part_no}/{total_parts}: {Path(srt_path).name}")
            self.root.after(
                0,
                lambda n=part_no, total=total_parts, name=Path(srt_path).name:
                    self.status.set(f"Generating part {n}/{total}: {name}")
            )

            await self.generate_one(Path(srt_path), part_no, total_parts)

        self.root.after(0, lambda: self.progress.set(100))
        self.root.after(0, lambda: self.status.set(
            f"Finished {total_parts} part(s)."
        ))
        self.log_msg(f"ALL PARTS FINISHED: {total_parts}")

        self.root.after(
            0,
            lambda: messagebox.showinfo(
                "Complete",
                f"Speech generated successfully for {total_parts} part(s)."
            )
        )

    async def generate_one(self, srt, part_no, total_parts):
        base_out = Path(
            self.out_dir.get() or (srt.parent / "tts_output")
        )
        base_out.mkdir(parents=True, exist_ok=True)

        # Each SRT gets its own output folder to avoid collisions.
        out = base_out / srt.stem
        out.mkdir(parents=True, exist_ok=True)

        items = parse_srt(srt)
        if not items:
            self.log_msg(f"SKIPPED: No valid subtitle entries in {srt.name}")
            return

        clips = out / "clips"
        clips.mkdir(exist_ok=True)

        self.log_msg(f"Loaded {len(items)} subtitle lines from {srt.name}.")
        self.log_msg(f"Voice: {self.voice.get()}")

        rate = int(self.rate.get())
        volume = int(self.volume.get())
        pitch = int(self.pitch.get())

        rate_str = f"{rate:+d}%"
        volume_str = f"{volume:+d}%"
        pitch_str = f"{pitch:+d}Hz"

        clip_files = []

        for n, item in enumerate(items, 1):
            clip = clips / f"{n:05d}.mp3"

            communicate = edge_tts.Communicate(
                item["text"],
                self.voice.get(),
                rate=rate_str,
                volume=volume_str,
                pitch=pitch_str,
            )
            await communicate.save(str(clip))
            clip_files.append((item, clip))

            pct = ((part_no - 1) + (n / len(items))) / total_parts * 100
            self.root.after(0, lambda p=pct: self.progress.set(p))
            self.root.after(
                0,
                lambda n=n, total=len(items), text=item["text"],
                       part=part_no, parts=total_parts:
                    self.status.set(
                        f"Part {part}/{parts} • Line {n}/{total}: {text[:70]}"
                    )
            )
            self.log_msg(
                f"[Part {part_no}/{total_parts} | {n}/{len(items)}] "
                f"{item['start']:.3f}s → {item['end']:.3f}s | {item['text']}"
            )

        final_wav = out / f"{srt.stem}_speech.wav"
        self.log_msg(f"Building synchronized timeline for {srt.name}…")
        self.build_timeline(clip_files, final_wav)

        final_path = final_wav

        if self.format.get() == "MP3":
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                self.log_msg("ffmpeg not found. Keeping WAV output.")
            else:
                final_mp3 = out / f"{srt.stem}_speech.mp3"
                subprocess.run(
                    [
                        ffmpeg, "-y", "-i", str(final_wav),
                        "-codec:a", "libmp3lame", "-q:a", "2",
                        str(final_mp3)
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                final_path = final_mp3

        self.log_msg(f"FINISHED PART: {final_path}")

    def get_mp3_duration(self, path):
        # ffprobe is preferred. If unavailable, use a conservative fallback.
        ffprobe = shutil.which("ffprobe")
        if ffprobe:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries",
                 "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                 str(path)],
                capture_output=True, text=True, check=True
            )
            return float(result.stdout.strip())

        # Estimate by decoding with ffmpeg if available.
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            result = subprocess.run(
                [ffmpeg, "-i", str(path), "-f", "null", "-"],
                capture_output=True, text=True
            )
            m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", result.stderr)
            if m:
                h, mi, sec = m.groups()
                return int(h) * 3600 + int(mi) * 60 + float(sec)

        raise RuntimeError("ffprobe or ffmpeg is required to build the synchronized WAV.")

    def build_timeline(self, clips, output):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError(
                "ffmpeg is required for synchronization.\n"
                "Install FFmpeg and make sure ffmpeg.exe is in PATH."
            )

        durations = [(item, clip, self.get_mp3_duration(clip))
                     for item, clip in clips]

        end_time = max(item["end"] for item, _, _ in durations)
        for item, _, dur in durations:
            end_time = max(end_time, item["start"] + dur)

        # Generate silence + each speech clip as a delayed stream, then mix.
        inputs = []
        filters = []

        # Silence base using anullsrc.
        inputs.append("-f")
        inputs.append("lavfi")
        inputs.append("-i")
        inputs.append("anullsrc=r=24000:cl=mono")

        # Each clip as an input.
        for _, clip, _ in durations:
            inputs.extend(["-i", str(clip)])

        filters.append(
            f"[0:a]atrim=0:{end_time:.3f},asetpts=N/SR/TB[base]"
        )

        mix_labels = ["[base]"]

        for i, (item, _, _) in enumerate(durations, start=1):
            delay = max(0, seconds_to_ms(item["start"]))
            label = f"a{i}"
            filters.append(
                f"[{i}:a]aresample=24000,adelay={delay}|{delay},"
                f"asetpts=N/SR/TB[{label}]"
            )
            mix_labels.append(f"[{label}]")

        filters.append(
            "".join(mix_labels) +
            f"amix=inputs={len(mix_labels)}:duration=longest:dropout_transition=0,"
            f"aresample=24000[out]"
        )

        cmd = [
            ffmpeg, "-y",
            *inputs,
            "-filter_complex", ";".join(filters),
            "-map", "[out]",
            "-c:a", "pcm_s16le",
            str(output)
        ]

        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
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
