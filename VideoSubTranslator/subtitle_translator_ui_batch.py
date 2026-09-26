from pathlib import Path
import os
import re
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None


APP_TITLE = "Subtitle Translator - Batch / Manual"
DEFAULT_SOURCE = "zh-CN"
DEFAULT_TARGET = "km"
DEFAULT_BATCH_SIZE = 20
DEFAULT_DELAY = 1.5
DEFAULT_RETRIES = 5


def parse_srt(path):
    entries = []
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()

    blocks = re.split(r"\n\s*\n", text.strip())
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue

        # Find timestamp line, allowing unusual SRT numbering.
        time_index = None
        for i, line in enumerate(lines):
            if "-->" in line:
                time_index = i
                break

        if time_index is None:
            continue

        number = lines[time_index - 1].strip() if time_index > 0 else str(len(entries) + 1)
        m = re.match(
            r"\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3})(.*)",
            lines[time_index],
        )
        if not m:
            continue

        start = m.group(1).replace(".", ",")
        end = m.group(2).replace(".", ",")
        extra = m.group(3).strip()

        text_lines = lines[time_index + 1:]
        original = "\n".join(text_lines).strip()

        if extra:
            original = (extra + "\n" + original).strip()

        entries.append({
            "number": number,
            "start": start,
            "end": end,
            "text": original,
            "translation": "",
        })

    return entries


def write_srt(path, entries):
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        for i, e in enumerate(entries, 1):
            f.write(f"{e.get('number', i)}\n")
            f.write(f"{e['start']} --> {e['end']}\n")
            translation = e.get("translation", "").strip()
            # Never write a blank subtitle. If translation is empty,
            # preserve the original source text.
            if not translation:
                translation = e.get("text", "")
            f.write(f"{translation}\n\n")


def parse_json_subtitles(path):
    """Load subtitle JSON in common array/object formats."""
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if isinstance(data, dict):
        # Support {"subtitles": [...]} / {"entries": [...]}
        for key in ("subtitles", "entries", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        raise ValueError("JSON subtitle file must contain an array of subtitle objects.")

    entries = []
    for i, item in enumerate(data, 1):
        if not isinstance(item, dict):
            continue

        start = item.get("start", item.get("from", ""))
        end = item.get("end", item.get("to", ""))
        original = item.get("text", item.get("original", ""))
        translation = item.get(
            "khmer",
            item.get("translation", item.get("translated", ""))
        )

        entries.append({
            "number": item.get("number", i),
            "start": str(start),
            "end": str(end),
            "text": str(original or ""),
            "translation": str(translation or ""),
        })

    return entries


def write_json_subtitles(path, entries):
    """Save subtitles in the user's JSON style with khmer as translation field."""
    data = []
    for i, e in enumerate(entries, 1):
        data.append({
            "start": e.get("start", ""),
            "end": e.get("end", ""),
            "text": e.get("text", ""),
            "khmer": (
                e.get("translation", "").strip()
                or e.get("text", "")
            ),
        })

    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def find_source_file(part_dir):
    """Find an SRT or JSON source file, preferring common source names."""
    preferred = [
        "transcript.srt", "original.srt", "source.srt",
        "transcript.json", "original.json", "source.json",
    ]

    for name in preferred:
        p = part_dir / name
        if p.exists():
            return p

    candidates = sorted(
        list(part_dir.glob("*.srt")) + list(part_dir.glob("*.json"))
    )
    excluded = {
        "translated.srt", "translation.srt", "translated_batch.srt",
        "translated.json", "translation.json", "translated_batch.json",
    }
    for p in candidates:
        if p.name.lower() not in excluded:
            return p

    return None


def parse_subtitle_file(path):
    if path.suffix.lower() == ".json":
        return parse_json_subtitles(path)
    return parse_srt(path)


def write_subtitle_file(path, entries):
    if path.suffix.lower() == ".json":
        write_json_subtitles(path, entries)
    else:
        write_srt(path, entries)


def find_source_file(part_dir):
    preferred = [
        "transcript.srt",
        "original.srt",
        "source.srt",
    ]

    for name in preferred:
        p = part_dir / name
        if p.exists():
            return p

    candidates = sorted(part_dir.glob("*.srt"))
    for p in candidates:
        if p.name.lower() not in {
            "translated.srt",
            "translation.srt",
            "translated_batch.srt",
        }:
            return p

    return None


class TranslatorApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1250x760")
        self.root.minsize(1000, 650)

        self.collection_dir = None
        self.parts = []
        self.entries = []
        self.current_part = None
        self.current_source = None

        self.source_var = tk.StringVar(value=DEFAULT_SOURCE)
        self.target_var = tk.StringVar(value=DEFAULT_TARGET)
        self.output_var = tk.StringVar(value="translated.srt")
        self.format_var = tk.StringVar(value="Auto")
        self.batch_size_var = tk.IntVar(value=DEFAULT_BATCH_SIZE)
        self.delay_var = tk.DoubleVar(value=DEFAULT_DELAY)
        self.retry_var = tk.IntVar(value=DEFAULT_RETRIES)
        self.status_var = tk.StringVar(value="Select a collection folder.")

        self.build_ui()

    def build_ui(self):
        top = ttk.Frame(self.root, padding=8)
        top.pack(fill="x")

        ttk.Button(top, text="Browse Collection Folder",
                   command=self.browse_collection).pack(side="left")

        self.folder_label = ttk.Label(top, text="No folder selected")
        self.folder_label.pack(side="left", padx=10)

        settings = ttk.LabelFrame(self.root, text="Translation Settings", padding=8)
        settings.pack(fill="x", padx=8, pady=(0, 8))

        ttk.Label(settings, text="Source:").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            settings, textvariable=self.source_var,
            values=["zh-CN", "zh-TW", "en", "ja", "ko", "th", "vi"],
            width=10, state="normal"
        ).grid(row=0, column=1, padx=5)

        ttk.Label(settings, text="Target:").grid(row=0, column=2, sticky="w")
        ttk.Combobox(
            settings, textvariable=self.target_var,
            values=["km", "en", "zh-CN", "th", "vi", "ja", "ko"],
            width=10, state="normal"
        ).grid(row=0, column=3, padx=5)

        ttk.Label(settings, text="Batch size:").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(
            settings, from_=1, to=50,
            textvariable=self.batch_size_var, width=6
        ).grid(row=0, column=5, padx=5)

        ttk.Label(settings, text="Delay (sec):").grid(row=0, column=6, sticky="w")
        ttk.Spinbox(
            settings, from_=0.5, to=30.0, increment=0.5,
            textvariable=self.delay_var, width=7
        ).grid(row=0, column=7, padx=5)

        ttk.Label(settings, text="Retries:").grid(row=0, column=8, sticky="w")
        ttk.Spinbox(
            settings, from_=1, to=10,
            textvariable=self.retry_var, width=6
        ).grid(row=0, column=9, padx=5)

        ttk.Label(settings, text="Output:").grid(row=0, column=10, sticky="w")
        ttk.Entry(settings, textvariable=self.output_var, width=20).grid(
            row=0, column=11, padx=5
        )

        ttk.Label(settings, text="Edit format:").grid(row=1, column=10, sticky="w", pady=(6, 0))
        ttk.Combobox(
            settings, textvariable=self.format_var,
            values=["Auto", "SRT", "JSON"],
            width=10, state="readonly"
        ).grid(row=1, column=11, padx=5, pady=(6, 0), sticky="w")

        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(main, padding=5)
        right = ttk.Frame(main, padding=5)
        main.add(left, weight=1)
        main.add(right, weight=5)

        ttk.Label(left, text="Parts").pack(anchor="w")

        list_frame = ttk.Frame(left)
        list_frame.pack(fill="both", expand=True)

        self.part_list = tk.Listbox(
            list_frame, selectmode=tk.EXTENDED,
            exportselection=False
        )
        self.part_list.pack(side="left", fill="both", expand=True)
        self.part_list.bind("<<ListboxSelect>>", self.on_part_selected)

        sb = ttk.Scrollbar(list_frame, orient="vertical",
                           command=self.part_list.yview)
        sb.pack(side="right", fill="y")
        self.part_list.configure(yscrollcommand=sb.set)

        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=5)

        ttk.Button(buttons, text="Select All",
                   command=self.select_all_parts).pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="Clear",
                   command=self.clear_parts).pack(side="left", fill="x", expand=True)

        ttk.Label(right, text="Subtitles").pack(anchor="w")

        tree_frame = ttk.Frame(right)
        tree_frame.pack(fill="both", expand=True)

        columns = ("num", "time", "original", "translation")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                 selectmode="extended")

        self.tree.heading("num", text="#")
        self.tree.heading("time", text="Time")
        self.tree.heading("original", text="Original")
        self.tree.heading("translation", text="Translation")

        self.tree.column("num", width=55, anchor="center")
        self.tree.column("time", width=180, anchor="w")
        self.tree.column("original", width=420, anchor="w")
        self.tree.column("translation", width=420, anchor="w")

        self.tree.pack(side="left", fill="both", expand=True)

        yscroll = ttk.Scrollbar(tree_frame, orient="vertical",
                                command=self.tree.yview)
        yscroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=yscroll.set)

        xscroll = ttk.Scrollbar(right, orient="horizontal",
                                command=self.tree.xview)
        xscroll.pack(fill="x")
        self.tree.configure(xscrollcommand=xscroll.set)

        self.tree.bind("<Double-1>", self.edit_selected_line)

        action = ttk.Frame(self.root, padding=8)
        action.pack(fill="x")

        ttk.Button(
            action, text="Edit Selected Line",
            command=self.edit_selected_line
        ).pack(side="left", padx=3)

        ttk.Button(
            action, text="Edit Whole Part",
            command=self.edit_whole_part
        ).pack(side="left", padx=3)

        ttk.Button(
            action, text="Edit Whole Part",
            command=self.edit_whole_part
        ).pack(side="left", padx=3)

        ttk.Button(
            action, text="Save Current Part",
            command=self.save_current_part
        ).pack(side="left", padx=3)

        ttk.Button(
            action, text="Auto Translate Selected Lines",
            command=self.auto_translate_selected_lines
        ).pack(side="left", padx=3)

        ttk.Button(
            action, text="Auto Translate Current Part",
            command=self.auto_translate_current_part
        ).pack(side="left", padx=3)

        ttk.Button(
            action, text="Translate Selected Parts",
            command=self.translate_selected_parts
        ).pack(side="left", padx=3)

        progress_frame = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        progress_frame.pack(fill="x")

        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(fill="x")

        ttk.Label(progress_frame, textvariable=self.status_var).pack(
            anchor="w", pady=(3, 0)
        )

        log_frame = ttk.LabelFrame(self.root, text="Log", padding=5)
        log_frame.pack(fill="x", padx=8, pady=(0, 8))

        self.log_text = tk.Text(log_frame, height=8, wrap="word")
        self.log_text.pack(fill="x")

    def log(self, message):
        def _write():
            self.log_text.insert("end", message + "\n")
            self.log_text.see("end")
        self.root.after(0, _write)

    def set_status(self, message):
        self.root.after(0, lambda: self.status_var.set(message))

    def browse_collection(self):
        folder = filedialog.askdirectory(title="Select subtitle collection folder")
        if not folder:
            return

        self.collection_dir = Path(folder)
        self.folder_label.config(text=str(self.collection_dir))

        self.parts = []
        self.part_list.delete(0, "end")

        # Find Part folders directly inside the selected collection folder.
        direct_parts = []
        for p in self.collection_dir.iterdir():
            if p.is_dir() and re.match(r"^Part\s*\d+\b", p.name, re.I):
                direct_parts.append(p)

        # If the selected folder itself contains a single collection folder,
        # also look one level deeper. This supports both:
        #
        # Collection\Part 01
        # and
        # Selected Folder\The Frontier Prince Seasons 1–3\Part 01
        if not direct_parts:
            for child in self.collection_dir.iterdir():
                if not child.is_dir():
                    continue
                for p in child.iterdir():
                    if p.is_dir() and re.match(r"^Part\s*\d+\b", p.name, re.I):
                        direct_parts.append(p)

        # Sort numerically: Part 1, Part 2, Part 10 rather than
        # Part 1, Part 10, Part 2.
        def part_number(p):
            m = re.search(r"Part\s*(\d+)", p.name, re.I)
            return int(m.group(1)) if m else 999999

        self.parts = sorted(direct_parts, key=lambda p: (part_number(p), p.name.lower()))

        # If there are no named Part folders, keep the previous fallback:
        # show all immediate subfolders so the user can still work with them.
        if not self.parts:
            self.parts = [
                p for p in sorted(self.collection_dir.iterdir())
                if p.is_dir()
            ]

        for i, p in enumerate(self.parts, 1):
            source = find_source_file(p)
            output_name = self.output_var.get().strip() or "translated.srt"
            output_path = p / output_name

            if source and output_path.exists():
                marker = "✓"
                status = "source + translated"
            elif source:
                marker = "○"
                status = "source SRT"
            else:
                marker = "✗"
                status = "NO SRT"

            # Show every part clearly with its number, folder name, and status.
            self.part_list.insert(
                "end",
                f"{marker}  {i:02d}. {p.name}   [{status}]"
            )

        self.log(f"Found {len(self.parts)} part folder(s).")
        if self.parts:
            self.status_var.set(
                f"Found {len(self.parts)} Part folder(s). "
                f"Select one or multiple parts from the list."
            )
            # Automatically select the first part so its subtitles appear.
            self.part_list.selection_set(0)
            self.load_part(self.parts[0])
        else:
            self.status_var.set("No Part folders found.")

    def select_all_parts(self):
        if self.parts:
            self.part_list.selection_set(0, "end")
            self.status_var.set(
                f"Selected all {len(self.parts)} Part folder(s)."
            )

    def clear_parts(self):
        self.part_list.selection_clear(0, "end")
        self.status_var.set("No Part folders selected.")

    def get_selected_part_indexes(self):
        return list(self.part_list.curselection())

    def get_selected_parts(self):
        return [self.parts[i] for i in self.get_selected_part_indexes()]

    def on_part_selected(self, event=None):
        indexes = self.get_selected_part_indexes()
        if not indexes:
            return

        # Load the first selected part for preview/editing.
        self.load_part(self.parts[indexes[0]])

    def load_part(self, part_dir):
        source = find_source_file(part_dir)
        if not source:
            self.log(f"No source SRT found: {part_dir}")
            return

        self.current_part = part_dir
        self.current_source = source
        self.entries = parse_subtitle_file(source)

        # Load existing translation if available.
        output_name = self.output_var.get().strip() or "translated.srt"
        output_path = part_dir / output_name

        if output_path.exists():
            try:
                translated_entries = parse_subtitle_file(output_path)
                for i, e in enumerate(translated_entries):
                    if i < len(self.entries):
                        self.entries[i]["translation"] = e.get("text", "")
                self.log(f"Loaded existing translation: {output_path.name}")
            except Exception as e:
                self.log(f"Could not load existing translation: {e}")

        self.refresh_tree()
        self.set_status(f"{part_dir.name}: {len(self.entries)} subtitle lines")

    def refresh_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

        for i, e in enumerate(self.entries):
            time_text = f"{e['start']} --> {e['end']}"
            self.tree.insert(
                "", "end", iid=str(i),
                values=(
                    e.get("number", i + 1),
                    time_text,
                    e.get("text", ""),
                    e.get("translation", "")
                )
            )

    def edit_selected_line(self, event=None):
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo("Select line", "Select a subtitle line first.")
            return

        index = int(selected[0])
        e = self.entries[index]

        win = tk.Toplevel(self.root)
        win.title(f"Edit subtitle #{e.get('number', index + 1)}")
        win.geometry("800x450")
        win.transient(self.root)

        ttk.Label(win, text="Original").pack(anchor="w", padx=10, pady=(10, 3))

        original = tk.Text(win, height=7, wrap="word")
        original.pack(fill="x", padx=10)
        original.insert("1.0", e.get("text", ""))
        original.configure(state="disabled")

        ttk.Label(win, text="Translation").pack(anchor="w", padx=10, pady=(10, 3))

        translation = tk.Text(win, height=9, wrap="word")
        translation.pack(fill="both", expand=True, padx=10)
        translation.insert("1.0", e.get("translation", ""))

        def save_edit():
            e["translation"] = translation.get("1.0", "end-1c").strip()
            self.refresh_tree()
            self.tree.selection_set(str(index))
            win.destroy()

        ttk.Button(win, text="Save Translation",
                   command=save_edit).pack(pady=10)

    def edit_whole_part(self):
        if not self.entries:
            messagebox.showwarning("No subtitles", "Load a part first.")
            return

        win = tk.Toplevel(self.root)
        win.title(f"Edit Whole Part - {self.current_part.name}")
        win.geometry("1100x800")
        win.transient(self.root)

        top = ttk.Frame(win, padding=8)
        top.pack(fill="x")

        ttk.Label(
            top,
            text=f"Notepad Editor — {self.current_part.name}"
        ).pack(side="left")

        ttk.Label(
            top,
            text="  Edit the entire part directly below"
        ).pack(side="left")

        editor_frame = ttk.Frame(win)
        editor_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        editor = tk.Text(
            editor_frame,
            wrap="none",
            undo=True,
            font=("Consolas", 11),
            padx=8,
            pady=8
        )
        editor.pack(side="left", fill="both", expand=True)

        yscroll = ttk.Scrollbar(
            editor_frame,
            orient="vertical",
            command=editor.yview
        )
        yscroll.pack(side="right", fill="y")

        xscroll = ttk.Scrollbar(
            win,
            orient="horizontal",
            command=editor.xview
        )
        xscroll.pack(fill="x", padx=8)

        editor.configure(
            yscrollcommand=yscroll.set,
            xscrollcommand=xscroll.set
        )

        # Determine the editing format.
        fmt = self.format_var.get().strip().lower()
        if fmt == "auto":
            fmt = self.current_source.suffix.lower().lstrip(".")
            if fmt not in ("srt", "json"):
                fmt = "srt"

        # Put the complete current part into the editor.
        if fmt == "json":
            import json

            data = []
            for e in self.entries:
                data.append({
                    "start": e.get("start", ""),
                    "end": e.get("end", ""),
                    "text": e.get("text", ""),
                    "khmer": e.get("translation", "")
                })

            content = json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            )
        else:
            lines = []
            for i, e in enumerate(self.entries, 1):
                lines.append(str(e.get("number", i)))
                lines.append(
                    f"{e.get('start', '')} --> {e.get('end', '')}"
                )
                lines.append(e.get("translation", ""))
                lines.append("")

            content = "\n".join(lines)

        editor.insert("1.0", content)

        # Bottom controls.
        buttons = ttk.Frame(win, padding=8)
        buttons.pack(fill="x")

        status = tk.StringVar(
            value=f"{fmt.upper()} format • Ctrl+S = Save"
        )
        ttk.Label(buttons, textvariable=status).pack(side="left")

        def save_notepad():
            raw = editor.get("1.0", "end-1c")

            try:
                if fmt == "json":
                    import json

                    data = json.loads(raw)

                    if isinstance(data, dict):
                        for key in ("subtitles", "entries", "data"):
                            if isinstance(data.get(key), list):
                                data = data[key]
                                break

                    if not isinstance(data, list):
                        raise ValueError(
                            "JSON must contain an array of subtitle objects."
                        )

                    new_entries = []
                    for i, item in enumerate(data, 1):
                        if not isinstance(item, dict):
                            continue

                        new_entries.append({
                            "number": item.get("number", i),
                            "start": str(
                                item.get("start", item.get("from", ""))
                            ),
                            "end": str(
                                item.get("end", item.get("to", ""))
                            ),
                            "text": str(
                                item.get("text", item.get("original", ""))
                            ),
                            "translation": str(
                                item.get(
                                    "khmer",
                                    item.get(
                                        "translation",
                                        item.get("translated", "")
                                    )
                                ) or ""
                            )
                        })

                else:
                    # Parse the edited Notepad content as SRT.
                    import tempfile

                    temp_path = Path(
                        tempfile.gettempdir()
                    ) / "_subtitle_notepad_edit.srt"

                    temp_path.write_text(
                        raw,
                        encoding="utf-8-sig"
                    )

                    new_entries = parse_srt(temp_path)

                    try:
                        temp_path.unlink()
                    except Exception:
                        pass

                if not new_entries:
                    raise ValueError("No subtitle entries were found.")

                self.entries = new_entries

                if self.save_current_part(silent=True):
                    self.refresh_tree()
                    status.set("Saved successfully.")
                    self.log(
                        f"Saved whole part from Notepad editor: "
                        f"{self.current_part}"
                    )

            except Exception as exc:
                messagebox.showerror(
                    "Cannot save",
                    f"Please check the edited {fmt.upper()} content.\n\n"
                    f"Error: {exc}"
                )

        def close_editor():
            win.destroy()

        ttk.Button(
            buttons,
            text="Save Whole Part",
            command=save_notepad
        ).pack(side="right", padx=3)

        ttk.Button(
            buttons,
            text="Close",
            command=close_editor
        ).pack(side="right", padx=3)

        # Ctrl+S works like Notepad.
        win.bind("<Control-s>", lambda event: save_notepad())
        win.bind("<Control-S>", lambda event: save_notepad())

        editor.focus_set()
        editor.mark_set("insert", "1.0")

    def save_current_part(self, silent=False):
        if not self.current_part or not self.entries:
            if not silent:
                messagebox.showwarning("No part", "Load a part first.")
            return False

        output_name = self.output_var.get().strip() or "translated.srt"
        fmt = self.format_var.get().strip().lower()
        if fmt == "json":
            output_name = re.sub(r"\.srt$", "", output_name, flags=re.I)
            if not output_name.lower().endswith(".json"):
                output_name += ".json"
        elif fmt == "srt":
            output_name = re.sub(r"\.json$", "", output_name, flags=re.I)
            if not output_name.lower().endswith(".srt"):
                output_name += ".srt"
        elif fmt == "auto":
            if not output_name.lower().endswith((".srt", ".json")):
                output_name += ".srt"

        output_path = self.current_part / output_name
        write_subtitle_file(output_path, self.entries)

        self.log(f"Saved: {output_path}")
        if not silent:
            messagebox.showinfo("Saved", f"Saved:\n{output_path}")

        return True

    def get_translator(self):
        if GoogleTranslator is None:
            messagebox.showerror(
                "Missing package",
                "deep-translator is not installed.\n\n"
                "Run the supplied BAT file to install it automatically."
            )
            return None

        return GoogleTranslator(
            source=self.source_var.get().strip() or DEFAULT_SOURCE,
            target=self.target_var.get().strip() or DEFAULT_TARGET
        )

    def translate_batch_with_retry(self, translator, texts, batch_number, total_batches):
        retries = max(1, int(self.retry_var.get()))
        base_delay = max(0.5, float(self.delay_var.get()))

        for attempt in range(1, retries + 1):
            try:
                self.log(
                    f"Batch {batch_number}/{total_batches}: "
                    f"{len(texts)} line(s), attempt {attempt}"
                )

                # deep-translator supports a list of strings here.
                result = translator.translate_batch(texts)

                if isinstance(result, str):
                    result = [result]

                if result is None:
                    raise RuntimeError("Translator returned no result.")

                result = list(result)

                if len(result) != len(texts):
                    raise RuntimeError(
                        f"Expected {len(texts)} translations, got {len(result)}."
                    )

                return result

            except Exception as e:
                error_text = str(e)
                self.log(
                    f"Batch {batch_number} ERROR attempt {attempt}: {error_text}"
                )

                if attempt >= retries:
                    raise

                # Longer waits for rate-limit/server errors.
                lower = error_text.lower()
                if "too many requests" in lower or "429" in lower or "rate" in lower:
                    wait = max(5.0, base_delay * (2 ** (attempt - 1)))
                else:
                    wait = base_delay * attempt

                self.log(f"Waiting {wait:.1f} sec before retry...")
                self.set_status(
                    f"Rate/server error. Waiting {wait:.1f}s before retry..."
                )
                time.sleep(wait)

        raise RuntimeError("Translation failed.")

    def translate_entries(self, entries, indexes, save_callback=None):
        translator = self.get_translator()
        if translator is None:
            return

        valid_indexes = [
            i for i in indexes
            if 0 <= i < len(entries) and entries[i].get("text", "").strip()
        ]

        if not valid_indexes:
            self.log("Nothing to translate.")
            return

        try:
            batch_size = max(1, min(50, int(self.batch_size_var.get())))
        except Exception:
            batch_size = DEFAULT_BATCH_SIZE

        batches = [
            valid_indexes[i:i + batch_size]
            for i in range(0, len(valid_indexes), batch_size)
        ]

        total = len(valid_indexes)

        for batch_no, batch_indexes in enumerate(batches, 1):
            texts = [entries[i]["text"] for i in batch_indexes]

            self.set_status(
                f"Translating batch {batch_no}/{len(batches)} "
                f"({len(texts)} lines)..."
            )

            try:
                translations = self.translate_batch_with_retry(
                    translator, texts, batch_no, len(batches)
                )
            except Exception as e:
                self.log(
                    f"STOPPED after batch {batch_no}: {e}"
                )
                self.set_status("Translation stopped. Previous batches were saved.")
                if save_callback:
                    save_callback()
                return

            for idx, translated in zip(batch_indexes, translations):
                entries[idx]["translation"] = str(translated or "").strip()

            done = sum(
                1 for i in valid_indexes
                if entries[i].get("translation", "").strip()
            )
            self.root.after(
                0,
                lambda d=done, t=total: self.progress.configure(
                    maximum=t, value=d
                )
            )

            # Save after every successful batch.
            if save_callback:
                save_callback()

            # Delay between batches to avoid hitting Google's limit.
            if batch_no < len(batches):
                delay = max(0.5, float(self.delay_var.get()))
                self.log(f"Waiting {delay:.1f} sec before next batch...")
                time.sleep(delay)

        self.set_status(f"Translation complete: {total} line(s).")
        self.log(f"Completed {total} subtitle line(s).")

    def auto_translate_selected_lines(self):
        if not self.entries:
            messagebox.showwarning("No subtitles", "Load a part first.")
            return

        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(
                "Select lines",
                "Select one or more subtitle lines first."
            )
            return

        indexes = [int(x) for x in selected]

        # Only translate blank lines unless user explicitly selected lines
        # with existing text. Existing translations will be replaced.
        if not messagebox.askyesno(
            "Translate selected lines",
            f"Translate {len(indexes)} selected line(s)?\n\n"
            "Existing translations in these selected lines will be replaced."
        ):
            return

        self.start_background_translation(
            self.entries,
            indexes,
            save_callback=self.save_current_part
        )

    def auto_translate_current_part(self):
        if not self.entries:
            messagebox.showwarning("No subtitles", "Load a part first.")
            return

        if not messagebox.askyesno(
            "Translate current part",
            f"Translate all {len(self.entries)} subtitle lines in "
            f"{self.current_part.name}?\n\n"
            "Existing translations will be replaced."
        ):
            return

        indexes = list(range(len(self.entries)))

        self.start_background_translation(
            self.entries,
            indexes,
            save_callback=self.save_current_part
        )

    def translate_selected_parts(self):
        selected_parts = self.get_selected_parts()

        if not selected_parts:
            messagebox.showinfo(
                "Select parts",
                "Select one or more Part folders first."
            )
            return

        if not messagebox.askyesno(
            "Translate selected parts",
            f"Translate {len(selected_parts)} selected part(s)?\n\n"
            "Existing translated.srt files will be updated."
        ):
            return

        self.start_batch_parts(selected_parts)

    def start_background_translation(self, entries, indexes, save_callback):
        if not indexes:
            return

        def worker():
            self.progress.configure(maximum=len(indexes), value=0)
            self.translate_entries(entries, indexes, save_callback)
            self.root.after(0, self.refresh_tree)

        threading.Thread(target=worker, daemon=True).start()

    def start_batch_parts(self, parts):
        def worker():
            total_parts = len(parts)

            for part_no, part_dir in enumerate(parts, 1):
                self.set_status(
                    f"Part {part_no}/{total_parts}: loading {part_dir.name}"
                )
                self.log(f"===== {part_dir.name} =====")

                source = find_source_file(part_dir)
                if not source:
                    self.log(f"SKIP: no source SRT in {part_dir}")
                    continue

                entries = parse_subtitle_file(source)

                output_name = self.output_var.get().strip() or "translated.srt"
                if not output_name.lower().endswith(".srt"):
                    output_name += ".srt"

                output_path = part_dir / output_name

                # Resume from existing translation where possible.
                if output_path.exists():
                    try:
                        old = parse_subtitle_file(output_path)
                        for i, old_entry in enumerate(old):
                            if i < len(entries):
                                old_text = old_entry.get("text", "").strip()
                                if old_text:
                                    entries[i]["translation"] = old_text
                        self.log(f"Loaded existing progress: {output_path.name}")
                    except Exception as e:
                        self.log(f"Could not load old translation: {e}")

                indexes = list(range(len(entries)))
                self.progress.configure(maximum=max(1, len(indexes)), value=0)

                def save_part(e=entries, p=output_path):
                    write_subtitle_file(p, e)
                    self.log(f"Saved progress: {p}")

                self.translate_entries(entries, indexes, save_callback=save_part)

                write_subtitle_file(output_path, entries)
                self.log(f"Finished: {output_path}")

            self.set_status("Selected parts finished.")
            messagebox.showinfo(
                "Finished",
                f"Finished processing {total_parts} selected part(s).\n"
                f"See translated.srt in each Part folder."
            )

        threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk()
    app = TranslatorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
