import os
import re
import sys
import json
import math
import wave
import shutil
import subprocess
import threading
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ============================================================
# CONFIGURATION
# ============================================================
ROOT = Path(r"D:\AI Video\VideoSpliter\collection\The Frontier Prince Seasons 1–3")
HF_CACHE = Path(r"D:\AI Video\huggingface-cache")

# Default local model candidates. The program also scans the cache
# and lets you choose another local Hugging Face model.
MODEL_HINTS = [
    "microsoft/speecht5_tts",
    "facebook/mms-tts-khm",
    "facebook/mms-tts-eng",
]

TEXT_EXTENSIONS = {
    ".txt", ".srt", ".vtt", ".json", ".ass", ".ssa", ".csv", ".md"
}

# ============================================================
# HELPERS
# ============================================================
def natural_key(value):
    return [
        int(x) if x.isdigit() else x.lower()
        for x in re.split(r"(\d+)", str(value))
    ]

def read_text(path):
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp936", "gb18030"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            pass
    return path.read_text(encoding="utf-8", errors="replace")

def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig", newline="")

def parse_srt_time(s):
    h, m, rest = s.replace(",", ".").split(":")
    sec, ms = rest.split(".")
    return (
        int(h) * 3600 * 1000 +
        int(m) * 60 * 1000 +
        int(sec) * 1000 +
        int(ms.ljust(3, "0")[:3])
    )

def format_srt_time(ms):
    ms = max(0, int(ms))
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def parse_srt(text):
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    result = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue
        timing = next((x for x in lines if "-->" in x), None)
        if not timing:
            continue
        try:
            a, b = [x.strip().split()[0] for x in timing.split("-->")]
            start = parse_srt_time(a)
            end = parse_srt_time(b)
        except Exception:
            continue

        idx = lines.index(timing)
        body = "\n".join(lines[idx + 1:]).strip()
        if body:
            result.append({"start": start, "end": end, "text": body})
    return result

def parse_vtt(text):
    text = re.sub(r"^WEBVTT.*?\n", "", text, count=1, flags=re.S)
    blocks = re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n"))
    result = []
    for block in blocks:
        lines = block.strip().splitlines()
        timing = next((x for x in lines if "-->" in x), None)
        if not timing:
            continue
        try:
            a, b = [x.strip().split()[0] for x in timing.split("-->")]
            def vtt_time(x):
                parts = x.replace(",", ".").split(":")
                if len(parts) == 2:
                    parts = ["0"] + parts
                h, m, sec = parts
                whole, frac = (sec.split(".") + ["0"])[:2]
                return (
                    int(h) * 3600000 + int(m) * 60000 +
                    int(whole) * 1000 + int(frac.ljust(3, "0")[:3])
                )
            start, end = vtt_time(a), vtt_time(b)
        except Exception:
            continue
        idx = lines.index(timing)
        body = "\n".join(lines[idx + 1:]).strip()
        if body:
            result.append({"start": start, "end": end, "text": body})
    return result

def extract_segments(path):
    text = read_text(path)
    ext = path.suffix.lower()

    if ext == ".srt":
        return parse_srt(text)
    if ext == ".vtt":
        return parse_vtt(text)

    # JSON subtitle formats: supports a few common structures.
    if ext == ".json":
        try:
            data = json.loads(text)
            if isinstance(data, list):
                out = []
                for x in data:
                    if isinstance(x, dict) and "text" in x:
                        start = x.get("start", 0)
                        end = x.get("end", 0)
                        # Seconds are common in JSON subtitle files.
                        if isinstance(start, (int, float)) and start < 100000:
                            start = int(start * 1000)
                        if isinstance(end, (int, float)) and end < 100000:
                            end = int(end * 1000)
                        out.append({"start": int(start), "end": int(end),
                                    "text": str(x["text"])})
                if out:
                    return out
        except Exception:
            pass

    # Plain text: one speech item. No timing.
    return [{"start": 0, "end": 0, "text": text.strip()}] if text.strip() else []

def find_translation(source):
    stem = source.with_suffix("")
    candidates = [
        Path(str(stem) + ".kh" + source.suffix),
        Path(str(stem) + ".km" + source.suffix),
        Path(str(stem) + ".khmer" + source.suffix),
        Path(str(stem) + "_khmer" + source.suffix),
        Path(str(stem) + "_translated" + source.suffix),
        Path(str(stem) + "_translation" + source.suffix),
        Path(str(stem) + ".kh.txt"),
        Path(str(stem) + ".km.txt"),
        Path(str(stem) + ".khmer.txt"),
    ]
    for p in candidates:
        if p.exists():
            return p
    return None

def find_ffmpeg():
    x = shutil.which("ffmpeg")
    if x:
        return x
    common = [
        Path(r"C:\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"),
        Path(r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe"),
    ]
    for p in common:
        if p.exists():
            return str(p)
    return None

# ============================================================
# TTS ENGINE
# ============================================================
class LocalHFTTS:
    def __init__(self, model_path, status_callback=None):
        self.model_path = str(model_path)
        self.status = status_callback or (lambda x: None)
        self.pipe = None
        self.processor = None
        self.model = None
        self.kind = None
        self.device = None
        self.sampling_rate = None

    def load(self):
        self.status("Loading local Hugging Face TTS model...")
        try:
            import torch
        except ImportError as e:
            raise RuntimeError("PyTorch is not installed. Run the BAT installer first.") from e
        try:
            from transformers import pipeline
        except ImportError as e:
            raise RuntimeError("Transformers is not installed. Run the BAT installer first.") from e

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        config_path = Path(self.model_path) / "config.json"
        config_data = {}
        try:
            if config_path.exists():
                config_data = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass

        model_type = str(config_data.get("model_type", "")).lower()
        architectures = " ".join(str(x) for x in (config_data.get("architectures") or [])).lower()
        is_vits = model_type == "vits" or "vitsmodel" in architectures or "mms-tts" in self.model_path.lower()

        if is_vits:
            try:
                from transformers import AutoProcessor, VitsModel
                self.status("Detected VITS/MMS-TTS model. Using direct inference (compatibility mode)...")
                self.processor = AutoProcessor.from_pretrained(self.model_path, local_files_only=True)
                self.model = VitsModel.from_pretrained(self.model_path, local_files_only=True)
                self.model.to(self.device)
                self.model.eval()
                self.sampling_rate = int(getattr(self.model.config, "sampling_rate", 16000))
                self.kind = "vits"
                self.status("TTS model loaded: " + self.model_path + (" | CUDA | VITS direct" if self.device.type == "cuda" else " | CPU | VITS direct"))
                return
            except Exception as vits_error:
                self.status("Direct VITS loading failed; trying generic TTS pipeline. Reason: " + str(vits_error))

        device_index = 0 if self.device.type == "cuda" else -1
        try:
            self.pipe = pipeline("text-to-speech", model=self.model_path, device=device_index)
            self.kind = "pipeline"
            self.status("TTS model loaded: " + self.model_path + (" | CUDA | pipeline" if device_index == 0 else " | CPU | pipeline"))
            return
        except Exception as first_error:
            raise RuntimeError("Could not load this model as a Transformers text-to-speech model.\n\nModel:\n" + self.model_path + "\n\nThe model was also not loadable through the direct VITS/MMS-TTS method.\n\nOriginal error:\n" + str(first_error))

    def synthesize(self, text):
        if self.kind is None:
            self.load()
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return None, None
        if self.kind == "vits":
            return self._synthesize_vits(text)
        if self.kind == "pipeline":
            result = self.pipe(text)
            audio = result["audio"]
            sampling_rate = int(result["sampling_rate"])
            import numpy as np
            if hasattr(audio, "detach"):
                audio = audio.detach().cpu().numpy()
            return np.asarray(audio).squeeze(), sampling_rate
        raise RuntimeError("TTS model is not loaded correctly.")

    def _synthesize_vits(self, text):
        """Run VITS/MMS-TTS without BatchEncoding.to(dtype=...)."""
        import numpy as np
        import torch
        try:
            encoded = self.processor(text=text, return_tensors="pt")
            inputs = {}
            for key, value in encoded.items():
                inputs[key] = value.to(self.device) if hasattr(value, "to") else value
            with torch.no_grad():
                output = self.model(**inputs)
            audio = output.waveform[0].detach().cpu().numpy()
            audio = np.asarray(audio).squeeze().astype(np.float32)
            if audio.size == 0:
                raise RuntimeError("VITS model returned empty audio.")
            return audio, self.sampling_rate
        except Exception as e:
            raise RuntimeError("VITS/MMS-TTS synthesis failed.\n\nText:\n" + text[:300] + "\n\nError:\n" + str(e)) from e

# ============================================================
# GUI
# ============================================================
class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Frontier Prince — Manual Translation + Local AI Voice")
        self.root.geometry("1500x900")
        self.root.minsize(1150, 700)

        self.base = ROOT
        self.hf_cache = HF_CACHE
        self.parts = []
        self.current_original = None
        self.current_translation = None
        self.tts = None
        self.model_paths = {}

        self.build()
        self.scan()
        self.scan_models()

    def build(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Transcript folder:").pack(side="left")
        self.root_var = tk.StringVar(value=str(ROOT))
        ttk.Entry(top, textvariable=self.root_var, width=65).pack(side="left", padx=5)
        ttk.Button(top, text="Choose", command=self.choose_root).pack(side="left")
        ttk.Button(top, text="Refresh", command=self.scan).pack(side="left", padx=3)

        ttk.Label(top, text="Filter:").pack(side="left", padx=(15, 3))
        self.filter_var = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.filter_var, width=25)
        ent.pack(side="left")
        ent.bind("<KeyRelease>", lambda e: self.refresh_tree())

        body = ttk.PanedWindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=5)

        left = ttk.Frame(body)
        body.add(left, weight=1)

        ttk.Label(left, text="Parts / Files",
                  font=("Segoe UI", 11, "bold")).pack(anchor="w")

        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tf, columns=("kind", "translation"),
            show="tree headings"
        )
        self.tree.heading("#0", text="File")
        self.tree.heading("kind", text="Type")
        self.tree.heading("translation", text="Translation")
        self.tree.column("#0", width=350)
        self.tree.column("kind", width=75)
        self.tree.column("translation", width=100)

        sy = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sy.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        tf.rowconfigure(0, weight=1)
        tf.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.select_file)
        self.tree.bind("<Double-1>", lambda e: self.open_original())

        right = ttk.Frame(body)
        body.add(right, weight=3)

        self.file_label = ttk.Label(
            right, text="No file selected",
            font=("Segoe UI", 11, "bold")
        )
        self.file_label.pack(anchor="w")

        editors = ttk.PanedWindow(right, orient="horizontal")
        editors.pack(fill="both", expand=True, pady=5)

        ob = ttk.Frame(editors)
        tb = ttk.Frame(editors)
        editors.add(ob, weight=1)
        editors.add(tb, weight=1)

        ttk.Label(ob, text="ORIGINAL").pack(anchor="w")
        self.original = tk.Text(ob, wrap="none", font=("Consolas", 10))
        oy = ttk.Scrollbar(ob, command=self.original.yview)
        self.original.configure(yscrollcommand=oy.set)
        self.original.pack(side="left", fill="both", expand=True)
        oy.pack(side="right", fill="y")

        ttk.Label(tb, text="TRANSLATION / TEXT FOR VOICE").pack(anchor="w")
        self.translation = tk.Text(tb, wrap="none", font=("Consolas", 10))
        ty = ttk.Scrollbar(tb, command=self.translation.yview)
        self.translation.configure(yscrollcommand=ty.set)
        self.translation.pack(side="left", fill="both", expand=True)
        ty.pack(side="right", fill="y")

        # Voice panel
        voice = ttk.LabelFrame(right, text="Local AI Voice", padding=8)
        voice.pack(fill="x", pady=5)

        r1 = ttk.Frame(voice)
        r1.pack(fill="x")

        ttk.Label(r1, text="Hugging Face cache:").pack(side="left")
        self.cache_var = tk.StringVar(value=str(HF_CACHE))
        ttk.Entry(r1, textvariable=self.cache_var, width=55).pack(side="left", padx=5)
        ttk.Button(r1, text="Choose", command=self.choose_cache).pack(side="left")
        ttk.Button(r1, text="Scan Models", command=self.scan_models).pack(side="left", padx=3)
        ttk.Button(
            r1, text="Install Khmer TTS",
            command=self.install_khmer_tts
        ).pack(side="left", padx=3)

        r2 = ttk.Frame(voice)
        r2.pack(fill="x", pady=5)

        ttk.Label(r2, text="Local model:").pack(side="left")
        self.model_var = tk.StringVar()
        self.models_combo = ttk.Combobox(
            r2, textvariable=self.model_var, width=65, state="readonly"
        )
        self.models_combo.pack(side="left", padx=5)

        ttk.Button(r2, text="Load Model", command=self.load_model).pack(side="left", padx=3)

        self.cuda_var = tk.StringVar(value="Device: checking...")
        ttk.Label(r2, textvariable=self.cuda_var).pack(side="left", padx=12)

        r3 = ttk.Frame(voice)
        r3.pack(fill="x")

        ttk.Label(r3, text="Mode:").pack(side="left")
        self.mode_var = tk.StringVar(value="Auto")
        ttk.Combobox(
            r3, textvariable=self.mode_var,
            values=["Auto", "One audio file", "Subtitle-timed audio"],
            state="readonly", width=22
        ).pack(side="left", padx=5)

        ttk.Label(r3, text="Output:").pack(side="left", padx=(12, 3))
        self.output_var = tk.StringVar(value="voice_srt")
        ttk.Entry(r3, textvariable=self.output_var, width=35).pack(side="left", padx=5)

        ttk.Button(
            r3, text="GENERATE VOICE",
            command=self.generate_voice
        ).pack(side="left", padx=10)

        # Buttons
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=5)

        ttk.Button(buttons, text="Copy Original",
                   command=self.copy_original).pack(side="left", padx=2)
        ttk.Button(buttons, text="Paste Translation",
                   command=self.paste_translation).pack(side="left", padx=2)
        ttk.Button(buttons, text="Save / Update",
                   command=self.save_translation).pack(side="left", padx=8)
        ttk.Button(buttons, text="Open Original",
                   command=self.open_original).pack(side="left", padx=2)

        # --------------------------------------------------------
        # LOG PANEL
        # --------------------------------------------------------
        log_frame = ttk.LabelFrame(self.root, text="Log", padding=5)
        log_frame.pack(fill="x", side="bottom", padx=8, pady=(0, 5))

        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(fill="x")
        ttk.Button(log_toolbar, text="Clear Log", command=self.clear_log).pack(side="left")
        ttk.Button(log_toolbar, text="Save Log", command=self.save_log).pack(side="left", padx=4)
        self.log_var = tk.StringVar(value="Log ready")
        ttk.Label(log_toolbar, textvariable=self.log_var).pack(side="left", padx=10)

        log_body = ttk.Frame(log_frame)
        log_body.pack(fill="both", expand=False)
        self.log_text = tk.Text(
            log_body, height=9, wrap="none", font=("Consolas", 9),
            state="disabled"
        )
        log_y = ttk.Scrollbar(log_body, orient="vertical", command=self.log_text.yview)
        log_x = ttk.Scrollbar(log_body, orient="horizontal", command=self.log_text.xview)
        self.log_text.configure(yscrollcommand=log_y.set, xscrollcommand=log_x.set)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_y.grid(row=0, column=1, sticky="ns")
        log_x.grid(row=1, column=0, sticky="ew")
        log_body.columnconfigure(0, weight=1)
        log_body.rowconfigure(0, weight=1)

        self.status = ttk.Label(self.root, text="Ready", relief="sunken", anchor="w")
        self.status.pack(fill="x", side="bottom")

        self.log("Application started.")
        self.log(f"Transcript root: {self.base}")
        self.log(f"Hugging Face cache: {self.hf_cache}")
        self.log("TTS scanner excludes Whisper/faster-whisper ASR models.")
        self.root.after(500, self.check_device)

    def log(self, message, level="INFO"):
        """Write a timestamped message to the visible UI log."""
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = str(message)
        line = f"[{stamp}] [{level}] {text}"

        def append():
            try:
                self.log_text.configure(state="normal")
                self.log_text.insert("end", line + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
                self.log_var.set(f"Last: {level} — {text[:120]}")
            except Exception:
                pass

        if threading.current_thread() is threading.main_thread():
            append()
        else:
            self.root.after(0, append)

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.log_var.set("Log cleared")
        self.log("Log cleared by user.")

    def save_log(self):
        path = filedialog.asksaveasfilename(
            title="Save Log",
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            content = self.log_text.get("1.0", "end-1c")
            Path(path).write_text(content, encoding="utf-8")
            self.log(f"Log saved: {path}")
        except Exception as e:
            self.log(f"Could not save log: {e}", "ERROR")
            messagebox.showerror("Save Log Error", str(e))

    def set_status(self, text):
        self.log(text)
        self.root.after(0, lambda: self.status.config(text=text))

    def check_device(self):
        try:
            import torch
            if torch.cuda.is_available():
                self.cuda_var.set(
                    f"Device: CUDA ({torch.cuda.get_device_name(0)})"
                )
            else:
                self.cuda_var.set("Device: CPU")
        except Exception:
            self.cuda_var.set("Device: install PyTorch")

    def choose_root(self):
        p = filedialog.askdirectory(initialdir=str(self.base))
        if p:
            self.root_var.set(p)
            self.scan()

    def choose_cache(self):
        p = filedialog.askdirectory(initialdir=str(self.hf_cache))
        if p:
            self.cache_var.set(p)
            self.hf_cache = Path(p)
            self.scan_models()

    def scan(self):
        self.log("Scanning transcript files...")
        self.base = Path(self.root_var.get())
        self.tree.delete(*self.tree.get_children())
        self.parts = []

        if not self.base.exists():
            self.log(f"Transcript folder not found: {self.base}", "ERROR")
            self.set_status(f"Transcript folder not found: {self.base}")
            return

        for part in sorted(
            [x for x in self.base.iterdir() if x.is_dir()],
            key=lambda x: natural_key(x.name)
        ):
            parent = self.tree.insert(
                "", "end", text=part.name, values=("PART", "")
            )
            try:
                files = [
                    x for x in part.rglob("*")
                    if x.is_file() and x.suffix.lower() in TEXT_EXTENSIONS
                ]
            except Exception:
                files = []

            for f in sorted(files, key=lambda x: natural_key(x.name)):
                trans = find_translation(f)
                iid = self.tree.insert(
                    parent, "end",
                    text=f.name,
                    values=(f.suffix.upper(), "YES" if trans else "NEW")
                )
                self.parts.append((iid, f))

        self.log(f"Transcript scan complete: {len(self.parts)} file(s) found.")
        self.set_status(f"Found {len(self.parts)} transcript files.")

    def refresh_tree(self):
        q = self.filter_var.get().lower().strip()
        for iid, path in self.parts:
            visible = not q or q in path.name.lower() or q in str(path.parent).lower()
            try:
                if visible:
                    self.tree.reattach(iid, self.tree.parent(iid), self.tree.index(iid))
                else:
                    self.tree.detach(iid)
            except Exception:
                pass

    def selected_path(self):
        sel = self.tree.selection()
        if not sel:
            return None
        for iid, path in self.parts:
            if iid == sel[0]:
                return path
        return None

    def select_file(self, event=None):
        path = self.selected_path()
        if not path:
            return

        self.current_original = path
        self.current_translation = find_translation(path)
        self.file_label.config(text=str(path))

        try:
            self.original.delete("1.0", "end")
            self.original.insert("1.0", read_text(path))
            self.translation.delete("1.0", "end")

            if self.current_translation:
                self.translation.insert(
                    "1.0", read_text(self.current_translation)
                )
                self.set_status(
                    f"Loaded translation: {self.current_translation.name}"
                )
            else:
                self.set_status("No translation yet. Paste/write one and save.")
        except Exception as e:
            messagebox.showerror("Read error", str(e))

    def copy_original(self):
        text = self.original.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("Original copied.")

    def paste_translation(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Clipboard", "Clipboard has no text.")
            return
        self.translation.delete("1.0", "end")
        self.translation.insert("1.0", text)
        self.set_status("Translation pasted.")

    def save_translation(self):
        if not self.current_original:
            messagebox.showwarning("No file", "Select a transcript.")
            return

        text = self.translation.get("1.0", "end-1c")
        if not text.strip():
            messagebox.showwarning("Empty", "Translation is empty.")
            return

        target = self.current_translation
        if not target:
            target = self.current_original.with_name(
                self.current_original.stem + ".kh" +
                self.current_original.suffix
            )

        try:
            write_text(target, text)
            self.current_translation = target
            self.scan()
            self.reselect(self.current_original)
            self.set_status(f"Saved translation: {target}")
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    def reselect(self, path):
        for iid, p in self.parts:
            if p.resolve() == Path(path).resolve():
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self.select_file()
                break

    def open_original(self):
        path = self.current_original or self.selected_path()
        if not path:
            return
        try:
            os.startfile(str(path))
        except Exception as e:
            messagebox.showerror("Open error", str(e))

    # --------------------------------------------------------
    # MODEL DISCOVERY
    # --------------------------------------------------------
    def scan_models(self):
        self.log("Starting Hugging Face model scan...")
        r"""Find local Hugging Face models robustly.

        Supports:
          D:\...\huggingface-cache\models--org--model\snapshots\HASH
          D:\...\huggingface-cache\models--org--model
          D:\...\huggingface-cache\org\model
          D:\...\huggingface-cache\some-local-model
        """
        self.hf_cache = Path(self.cache_var.get()).expanduser()

        self.models_combo["values"] = ()
        self.model_var.set("")

        if not self.hf_cache.exists():
            self.log(f"Model cache does not exist: {self.hf_cache}", "ERROR")
            self.set_status(
                f"Model cache does not exist: {self.hf_cache}"
            )
            return

        if not self.hf_cache.is_dir():
            self.log(f"Model cache is not a folder: {self.hf_cache}", "ERROR")
            self.set_status(
                f"Model cache is not a folder: {self.hf_cache}"
            )
            return

        self.set_status("Scanning Hugging Face cache...")
        self.log(f"Scanning cache path: {self.hf_cache}")

        def worker():
            candidates = []
            errors = []

            def add_candidate(folder, label=None):
                try:
                    folder = Path(folder).resolve()
                except Exception:
                    return

                if not folder.is_dir():
                    return

                # A usable local Transformers model normally has config.json.
                config = folder / "config.json"
                if not config.exists():
                    return

                try:
                    data = json.loads(
                        config.read_text(encoding="utf-8")
                    )
                except Exception as e:
                    errors.append(f"{config}: {e}")
                    return

                # Don't reject a model just because its architecture name
                # is unfamiliar. If it has config.json + model/tokenizer
                # files, let the user try loading it.
                files = set()
                try:
                    for f in folder.iterdir():
                        if f.is_file():
                            files.add(f.name.lower())
                except Exception as e:
                    errors.append(f"{folder}: {e}")

                model_markers = (
                    "model.safetensors",
                    "pytorch_model.bin",
                    "tf_model.h5",
                    "flax_model.msgpack",
                    "model.safetensors.index.json",
                    "pytorch_model.bin.index.json",
                    "preprocessor_config.json",
                    "tokenizer.json",
                    "vocab.json",
                    "spiece.model",
                )

                has_model_files = any(
                    name in files or any(
                        x.startswith(name[:-5]) if name.endswith(".json")
                        else False for x in files
                    )
                    for name in model_markers
                )

                # A config-only folder may still be useful for custom models,
                # so include it if the folder name strongly resembles a TTS
                # model or the config says it is a speech model.
                architectures = " ".join(
                    str(x) for x in (data.get("architectures") or [])
                ).lower()
                model_type = str(data.get("model_type", "")).lower()
                auto_map = str(data.get("auto_map", "")).lower()
                speech_text = (
                    architectures + " " + model_type + " " +
                    auto_map + " " + str(folder).lower()
                )

                # IMPORTANT:
                # Whisper/faster-whisper/Wav2Vec2 are speech-to-text (ASR),
                # not text-to-speech. Never offer them in the TTS selector.
                asr_markers = (
                    "whisper", "faster-whisper", "wav2vec",
                    "speech_to_text", "speech2text", "automatic-speech",
                    "ctc", "hubert", "sew", "unispeech"
                )
                tts_markers = (
                    "tts", "text-to-speech", "text_to_speech",
                    "vits", "speecht5", "mms-tts", "bark",
                    "tacotron", "fastspeech", "xtts", "parler"
                )

                looks_asr = any(x in speech_text for x in asr_markers)
                looks_tts = any(x in speech_text for x in tts_markers)

                if looks_asr and not looks_tts:
                    self.log(f"Skipping non-TTS speech model: {folder}", "INFO")
                    return

                if looks_tts and has_model_files:
                    label_text = label or str(folder)
                    candidates.append((label_text, str(folder)))

            # ----------------------------------------------------
            # 1. Standard Hugging Face cache:
            # models--facebook--mms-tts-khm
            #   snapshots/<hash>/config.json
            # ----------------------------------------------------
            try:
                top = list(self.hf_cache.iterdir())
            except Exception as e:
                self.root.after(
                    0, lambda e=e: messagebox.showerror(
                        "Model scan error",
                        f"Cannot read:\n{self.hf_cache}\n\n{e}"
                    )
                )
                return

            for item in top:
                if not item.is_dir():
                    continue

                if item.name.startswith("models--"):
                    repo_name = item.name[len("models--"):].replace("--", "/")
                    snapshots = item / "snapshots"

                    if snapshots.is_dir():
                        try:
                            for snap in snapshots.iterdir():
                                if snap.is_dir():
                                    add_candidate(
                                        snap,
                                        f"{repo_name}  [{snap.name[:12]}]"
                                    )
                        except Exception as e:
                            errors.append(f"{snapshots}: {e}")

                    # Some cache layouts may put config directly in repo dir.
                    add_candidate(item, repo_name)

            # ----------------------------------------------------
            # 2. Search below cache for config.json.
            # Limit traversal so a huge cache does not freeze the UI.
            # ----------------------------------------------------
            seen_dirs = set()
            try:
                for config in self.hf_cache.rglob("config.json"):
                    try:
                        folder = config.parent.resolve()
                    except Exception:
                        continue

                    if folder in seen_dirs:
                        continue
                    seen_dirs.add(folder)

                    # Ignore obvious non-model Python/cache metadata.
                    if any(
                        part in {".git", "__pycache__", "node_modules"}
                        for part in folder.parts
                    ):
                        continue

                    add_candidate(folder)
            except Exception as e:
                errors.append(f"Recursive scan: {e}")

            # ----------------------------------------------------
            # 3. Also inspect direct folders supplied by user.
            # ----------------------------------------------------
            for item in top:
                if item.is_dir():
                    add_candidate(item)

            # De-duplicate by actual path.
            unique = {}
            for label, path in candidates:
                unique[path] = label

            final = sorted(
                [(label, path) for path, label in unique.items()],
                key=lambda x: natural_key(x[0].lower())
            )

            def update():
                self.log(f"Model scan finished. Found {len(final)} candidate model folder(s).")
                for label, path in final:
                    self.log(f"MODEL: {label} -> {path}")
                if errors:
                    for err in errors[:20]:
                        self.log(f"SCAN ERROR: {err}", "ERROR")
                values = [label for label, path in final]
                self.model_paths = {
                    label: path for label, path in final
                }
                self.models_combo["values"] = values

                if values:
                    self.model_var.set(values[0])
                    self.set_status(
                        f"Found {len(values)} local Hugging Face model(s). "
                        f"Select one and click Load Model."
                    )
                else:
                    self.log("No usable local model detected.", "WARNING")
                    self.set_status(
                        "No local model detected. "
                        "Click Choose and select the exact model folder, "
                        "or verify the Hugging Face cache path."
                    )

                    # Give useful diagnostic information instead of silently
                    # doing nothing.
                    msg = (
                        "No usable local model was detected.\n\n"
                        f"Scanned folder:\n{self.hf_cache}\n\n"
                        "Expected examples:\n"
                        "  models--facebook--mms-tts-khm\\snapshots\\<hash>\n"
                        "  models--microsoft--speecht5_tts\\snapshots\\<hash>\n"
                        "  or a local model folder containing config.json.\n\n"
                        "You can also use Choose to select the model's "
                        "snapshot folder directly."
                    )
                    if errors:
                        msg += "\n\nSome scan errors occurred:\n" + "\n".join(errors[:8])

                    messagebox.showinfo("Model Scan", msg)

            self.root.after(0, update)

        threading.Thread(target=worker, daemon=True).start()

    def get_selected_model_path(self):
        label = self.model_var.get().strip()
        if not label:
            return None

        # model_paths is created by scan_models().
        path = getattr(self, "model_paths", {}).get(label)
        if path:
            return path

        # If the user pasted/selected a real directory into the combo.
        candidate = Path(label)
        if candidate.is_dir():
            return str(candidate)

        return label

    def install_khmer_tts(self):
        """Download facebook/mms-tts-khm into the configured local HF cache."""
        cache = Path(self.cache_var.get()).expanduser()
        repo_id = "facebook/mms-tts-khm"

        self.log(f"Requested TTS download: {repo_id}")
        self.log(f"Target Hugging Face cache: {cache}")

        def work():
            try:
                from huggingface_hub import snapshot_download
            except ImportError as e:
                self.log(
                    "huggingface_hub is not installed. Run the BAT installer.",
                    "ERROR"
                )
                self.root.after(
                    0,
                    lambda: messagebox.showerror(
                        "Missing package",
                        "huggingface_hub is not installed.\n\n"
                        "Run the BAT file again to install dependencies."
                    )
                )
                return

            try:
                cache.mkdir(parents=True, exist_ok=True)
                self.set_status("Downloading Khmer TTS model...")
                self.log(f"Downloading {repo_id} ...")
                path = snapshot_download(
                    repo_id=repo_id,
                    cache_dir=str(cache)
                )
                self.log(f"Khmer TTS download complete: {path}")
                self.set_status("Khmer TTS ready.")
                self.root.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Khmer TTS ready",
                        "Khmer text-to-speech model downloaded.\n\n"
                        "Click Scan Models, select facebook/mms-tts-khm, "
                        "then Load Model."
                    )
                )
                self.root.after(0, self.scan_models)
            except Exception as e:
                self.log(f"Khmer TTS download error: {e}", "ERROR")
                self.log(traceback.format_exc(), "ERROR")
                error_text = str(e)
                self.root.after(
                    0,
                    lambda error_text=error_text: messagebox.showerror(
                        "TTS download error",
                        "Could not download facebook/mms-tts-khm.\n\n"
                        f"{error_text}"
                    )
                )

        threading.Thread(target=work, daemon=True).start()

    def load_model(self):
        model = self.get_selected_model_path()
        self.log(f"Load Model requested: {model or '<none>'}")

        if not model:
            messagebox.showwarning(
                "Model",
                "No model selected.\n\n"
                "Run Scan Models first, or select a local model folder."
            )
            return

        model_path = Path(model)
        if not model_path.exists():
            messagebox.showerror(
                "Model not found",
                f"The selected model path does not exist:\n\n{model_path}"
            )
            return

        config = model_path / "config.json"
        if not config.exists():
            messagebox.showerror(
                "Not a Transformers model folder",
                "This folder does not contain config.json:\n\n"
                f"{model_path}\n\n"
                "Choose the Hugging Face model snapshot folder that "
                "contains config.json."
            )
            return

        self.set_status(f"Loading model: {model_path}")

        def work():
            try:
                self.tts = LocalHFTTS(model_path, self.set_status)
                self.tts.load()
                self.log(f"Model loaded successfully: {model_path}")
                self.root.after(
                    0, lambda: messagebox.showinfo(
                        "Model ready",
                        f"Local TTS model loaded successfully:\n\n{model_path}"
                    )
                )
            except Exception as e:
                self.log(f"MODEL LOAD ERROR: {e}", "ERROR")
                self.log(traceback.format_exc(), "ERROR")
                error_text = str(e)
                self.root.after(
                    0, lambda error_text=error_text: messagebox.showerror(
                        "Model load error",
                        f"Could not load:\n{model_path}\n\n{error_text}"
                    )
                )

        threading.Thread(target=work, daemon=True).start()

    # --------------------------------------------------------
    # VOICE GENERATION
    # --------------------------------------------------------
    def generate_voice(self):
        self.log("Generate Voice requested.")
        if not self.current_original:
            messagebox.showwarning("No transcript", "Select a transcript first.")
            return

        model = self.get_selected_model_path()
        if not model:
            messagebox.showwarning(
                "No model",
                "Select a local Hugging Face TTS model first."
            )
            return

        text = self.translation.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning(
                "No translation",
                "Paste or save the translation before generating voice."
            )
            return

        self.log(f"Generation model: {model}")
        self.log(f"Translation text length: {len(text)} characters")
        if self.tts is None or Path(self.tts.model_path).resolve() != Path(model).resolve():
            self.load_model_then_generate(model, text)
            return

        self.start_generation(text)

    def load_model_then_generate(self, model, text):
        def work():
            try:
                self.tts = LocalHFTTS(model, self.set_status)
                self.tts.load()
                self.log(f"Model loaded for generation: {model}")
                self.root.after(0, lambda: self.start_generation(text))
            except Exception as e:
                self.log(f"MODEL ERROR: {e}", "ERROR")
                self.log(traceback.format_exc(), "ERROR")
                self.root.after(
                    0, lambda: messagebox.showerror("Model error", str(e))
                )
        threading.Thread(target=work, daemon=True).start()

    def start_generation(self, full_text):
        mode = self.mode_var.get()
        if mode == "Auto":
            if self.current_original.suffix.lower() in (".srt", ".vtt"):
                mode = "Subtitle-timed audio"
                self.log("Auto mode: SRT/VTT detected -> Subtitle-timed audio.")
            else:
                mode = "One audio file"

        out_base = self.output_var.get().strip() or "voice"

        if not Path(out_base).is_absolute():
            out_base = str(self.current_original.parent / out_base)

        self.log(f"Voice mode: {mode}")
        self.log(f"Output base: {out_base}")

        def work():
            try:
                if mode == "Subtitle-timed audio":
                    self.generate_timed(out_base)
                else:
                    self.generate_single(out_base, full_text)
                self.log("Voice generation completed successfully.")
                self.root.after(
                    0, lambda: messagebox.showinfo(
                        "Voice complete",
                        f"Voice generation finished.\n\nOutput:\n{out_base}"
                    )
                )
            except Exception as e:
                self.log(f"VOICE GENERATION ERROR: {e}", "ERROR")
                self.log(traceback.format_exc(), "ERROR")
                self.root.after(
                    0, lambda: messagebox.showerror("Voice generation error", str(e))
                )

        threading.Thread(target=work, daemon=True).start()

    def generate_single(self, out_base, text):
        import numpy as np
        import soundfile as sf

        self.set_status("Generating AI voice...")
        self.log("Starting single-file TTS synthesis...")
        audio, sr = self.tts.synthesize(text)
        wav = Path(out_base).with_suffix(".wav")
        sf.write(str(wav), audio, sr)
        self.log(f"Single-file WAV saved: {wav}")
        self.set_status(f"Voice saved: {wav}")

    def generate_timed(self, out_base):
        import numpy as np
        import soundfile as sf

        self.log("SRT/VTT -> timed AI voice generation started.")
        self.log(f"Reading subtitle segments from: {self.current_original}")
        segments = extract_segments(self.current_original)
        self.log(f"Original subtitle segments: {len(segments)}")
        if not segments:
            raise RuntimeError("No subtitle segments found.")

        # If translation has the same SRT structure, use it.
        translation_segments = []
        if self.current_translation and self.current_translation.exists():
            translation_segments = extract_segments(self.current_translation)

        # Prefer translated segment text where the counts match.
        self.log(f"Translation subtitle segments: {len(translation_segments)}")
        if len(translation_segments) == len(segments):
            texts = [x["text"] for x in translation_segments]
        else:
            # Fall back to splitting translation into non-empty lines.
            lines = [
                x.strip() for x in
                self.translation.get("1.0", "end-1c").splitlines()
                if x.strip()
            ]
            if len(lines) == len(segments):
                texts = lines
            else:
                # One translation document that is not timestamped:
                # generate each original segment using its corresponding
                # source text only as a last resort.
                raise RuntimeError(
                    "The selected translation does not have the same number "
                    "of subtitle segments as the original.\n\n"
                    f"Original segments: {len(segments)}\n"
                    f"Translation segments: {len(translation_segments)}\n\n"
                    "Save the translation as .kh.srt with matching timestamps "
                    "for subtitle-timed voice generation."
                )

        temp = Path(out_base + "_voice_parts")
        temp.mkdir(parents=True, exist_ok=True)

        wav_parts = []
        for i, (seg, txt) in enumerate(zip(segments, texts), 1):
            txt = re.sub(r"<[^>]+>", "", txt)
            txt = re.sub(r"\s+", " ", txt).strip()
            if not txt:
                continue

            self.set_status(
                f"Generating voice {i}/{len(segments)}..."
            )
            audio, sr = self.tts.synthesize(txt)
            part = temp / f"{i:05d}.wav"
            sf.write(str(part), audio, sr)
            wav_parts.append((part, seg, sr))
            self.log(f"Segment {i}: saved {part.name} ({len(audio)} samples, {sr} Hz)")

        if not wav_parts:
            raise RuntimeError("No voice segments were generated.")

        # Assemble one WAV using subtitle timestamps.
        sr = wav_parts[0][2]
        last_end = max(x["end"] for _, x, _ in wav_parts)
        output = np.zeros(
            int(math.ceil(last_end / 1000 * sr)) + sr,
            dtype=np.float32
        )

        for part, seg, part_sr in wav_parts:
            audio, _ = sf.read(str(part), dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)

            if part_sr != sr:
                raise RuntimeError(
                    "TTS segments returned different sample rates."
                )

            start = int(seg["start"] / 1000 * sr)

            # If speech is longer than the subtitle slot, extend the output
            # rather than cutting speech off.
            end = start + len(audio)
            if end > len(output):
                output = np.pad(output, (0, end - len(output)))

            # Mix into the timeline.
            output[start:end] += audio

        # Normalize to avoid clipping.
        peak = float(np.max(np.abs(output))) if len(output) else 0
        if peak > 0.98:
            output = output * (0.98 / peak)

        wav = Path(out_base).with_suffix(".wav")
        sf.write(str(wav), output, sr)
        self.log(f"Timed WAV saved: {wav}")

        # Optional MP3 conversion through ffmpeg.
        ffmpeg = find_ffmpeg()
        mp3 = None
        if ffmpeg:
            mp3 = Path(out_base).with_suffix(".mp3")
            self.log(f"Converting WAV to MP3 with ffmpeg: {ffmpeg}")
            subprocess.run(
                [
                    ffmpeg, "-y", "-i", str(wav),
                    "-codec:a", "libmp3lame", "-q:a", "2",
                    str(mp3)
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        self.log(f"Timed voice complete: {wav}" + (f" | MP3: {mp3}" if mp3 else ""))
        self.set_status(
            f"Timed voice complete: {wav}" +
            (f" | MP3: {mp3}" if mp3 else "")
        )

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
