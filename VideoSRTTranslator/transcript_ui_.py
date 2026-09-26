import os
import json
import shutil
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

ROOT = Path(r"D:\AI Video\VideoSpliter\collection\The Frontier Prince Seasons 1–3")

# File extensions treated as transcript/text files.
TEXT_EXTENSIONS = {
    ".txt", ".srt", ".vtt", ".json", ".ass", ".ssa", ".csv", ".md"
}

TRANSLATION_SUFFIXES = (
    ".km", ".kh", ".khmer", "_khmer", "_km", "_translated", "_translation"
)

class TranscriptApp:
    def __init__(self, root):
        self.root = root
        self.root.title("The Frontier Prince — Transcript Translator")
        self.root.geometry("1400x850")
        self.root.minsize(1050, 650)

        self.current_original = None
        self.current_translation = None
        self.current_source_text = ""

        self.build_ui()
        self.scan()

    def build_ui(self):
        # Top toolbar
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill="x")

        ttk.Label(toolbar, text="Folder:").pack(side="left")
        self.root_var = tk.StringVar(value=str(ROOT))
        ttk.Entry(toolbar, textvariable=self.root_var, width=75).pack(side="left", padx=6)

        ttk.Button(toolbar, text="Scan / Refresh", command=self.scan).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Choose Folder", command=self.choose_folder).pack(side="left", padx=3)

        # Search/filter
        ttk.Label(toolbar, text=" Filter:").pack(side="left", padx=(15, 3))
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(toolbar, textvariable=self.filter_var, width=28)
        entry.pack(side="left")
        entry.bind("<KeyRelease>", lambda e: self.refresh_tree())

        # Main splitter
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # Left: folders/files
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        ttk.Label(left, text="Parts / Transcript Files",
                   font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 5))

        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("type", "translation"), show="tree headings")
        self.tree.heading("#0", text="File / Part")
        self.tree.heading("type", text="Type")
        self.tree.heading("translation", text="Translation")
        self.tree.column("#0", width=330)
        self.tree.column("type", width=90)
        self.tree.column("translation", width=110)

        ys = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self.on_double_click)

        # Right editor
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        # File header
        header = ttk.Frame(right)
        header.pack(fill="x", pady=(0, 5))

        self.file_label = ttk.Label(
            header, text="No transcript selected",
            font=("Segoe UI", 11, "bold")
        )
        self.file_label.pack(side="left")

        self.status_label = ttk.Label(header, text="")
        self.status_label.pack(side="right")

        # Original / translation split
        editors = ttk.PanedWindow(right, orient="vertical")
        editors.pack(fill="both", expand=True)

        original_box = ttk.Frame(editors)
        translation_box = ttk.Frame(editors)
        editors.add(original_box, weight=1)
        editors.add(translation_box, weight=1)

        ttk.Label(original_box, text="ORIGINAL TRANSCRIPT",
                   font=("Segoe UI", 10, "bold")).pack(anchor="w")

        original_frame = ttk.Frame(original_box)
        original_frame.pack(fill="both", expand=True)

        self.original_text = tk.Text(
            original_frame, wrap="none", undo=False,
            font=("Consolas", 10)
        )
        oy = ttk.Scrollbar(original_frame, orient="vertical",
                           command=self.original_text.yview)
        ox = ttk.Scrollbar(original_frame, orient="horizontal",
                           command=self.original_text.xview)
        self.original_text.configure(yscrollcommand=oy.set,
                                     xscrollcommand=ox.set)
        self.original_text.grid(row=0, column=0, sticky="nsew")
        oy.grid(row=0, column=1, sticky="ns")
        ox.grid(row=1, column=0, sticky="ew")
        original_frame.rowconfigure(0, weight=1)
        original_frame.columnconfigure(0, weight=1)

        ttk.Label(translation_box, text="KHMER / NEW TRANSLATION",
                   font=("Segoe UI", 10, "bold")).pack(anchor="w")

        translation_frame = ttk.Frame(translation_box)
        translation_frame.pack(fill="both", expand=True)

        self.translation_text = tk.Text(
            translation_frame, wrap="none", undo=True,
            font=("Consolas", 10)
        )
        ty = ttk.Scrollbar(translation_frame, orient="vertical",
                           command=self.translation_text.yview)
        tx = ttk.Scrollbar(translation_frame, orient="horizontal",
                           command=self.translation_text.xview)
        self.translation_text.configure(yscrollcommand=ty.set,
                                        xscrollcommand=tx.set)
        self.translation_text.grid(row=0, column=0, sticky="nsew")
        ty.grid(row=0, column=1, sticky="ns")
        tx.grid(row=1, column=0, sticky="ew")
        translation_frame.rowconfigure(0, weight=1)
        translation_frame.columnconfigure(0, weight=1)

        # Buttons
        buttons = ttk.Frame(right, padding=(0, 8, 0, 0))
        buttons.pack(fill="x")

        ttk.Button(buttons, text="Copy Original",
                   command=self.copy_original).pack(side="left", padx=3)
        ttk.Button(buttons, text="Paste Translation",
                   command=self.paste_translation).pack(side="left", padx=3)
        ttk.Button(buttons, text="Save / Update",
                   command=self.save_translation).pack(side="left", padx=12)
        ttk.Button(buttons, text="Save As...",
                   command=self.save_as).pack(side="left", padx=3)
        ttk.Button(buttons, text="Open Original File",
                   command=self.open_original_file).pack(side="left", padx=12)
        ttk.Button(buttons, text="Create Translation",
                   command=self.create_translation).pack(side="left", padx=3)
        ttk.Button(buttons, text="Clear Translation",
                   command=lambda: self.translation_text.delete("1.0", "end")
                   ).pack(side="right", padx=3)

        self.statusbar = ttk.Label(self.root, text="Ready", relief="sunken",
                                   anchor="w", padding=4)
        self.statusbar.pack(fill="x", side="bottom")

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=str(ROOT))
        if folder:
            self.root_var.set(folder)
            self.scan()

    def scan(self):
        self.base = Path(self.root_var.get())
        self.tree.delete(*self.tree.get_children())

        if not self.base.exists():
            self.statusbar.config(
                text=f"Folder not found: {self.base}"
            )
            messagebox.showwarning(
                "Folder not found",
                f"The folder does not exist:\n\n{self.base}\n\n"
                "You can choose the correct folder with 'Choose Folder'."
            )
            return

        self.parts = []
        try:
            children = sorted(
                [p for p in self.base.iterdir() if p.is_dir()],
                key=lambda p: self.natural_key(p.name)
            )
        except Exception as e:
            messagebox.showerror("Scan error", str(e))
            return

        for part in children:
            # User specifically described "part 1, part 2..."
            # We also allow other folders so the UI does not hide content.
            part_id = self.tree.insert(
                "", "end", text=part.name, values=("PART", ""),
                open=False
            )

            files = []
            try:
                for f in part.rglob("*"):
                    if f.is_file() and f.suffix.lower() in TEXT_EXTENSIONS:
                        files.append(f)
            except Exception:
                pass

            for f in sorted(files, key=lambda x: self.natural_key(x.name)):
                kind = self.file_kind(f)
                has_translation = self.find_existing_translation(f) is not None
                trans_status = "YES" if has_translation else "NEW"

                iid = self.tree.insert(
                    part_id, "end",
                    text=f.name,
                    values=(kind, trans_status)
                )
                self.parts.append((iid, f))

        self.statusbar.config(
            text=f"Scanned: {self.base} | {len(self.parts)} transcript files"
        )

    @staticmethod
    def natural_key(value):
        import re
        return [
            int(x) if x.isdigit() else x.lower()
            for x in re.split(r"(\d+)", str(value))
        ]

    def file_kind(self, path):
        return path.suffix.lower().lstrip(".").upper()

    def refresh_tree(self):
        query = self.filter_var.get().strip().lower()
        for iid, path in self.parts:
            visible = not query or query in path.name.lower() or query in str(path.parent).lower()
            if visible:
                self.tree.reattach(iid, self.tree.parent(iid), self.tree.index(iid))
            else:
                try:
                    self.tree.detach(iid)
                except Exception:
                    pass

    def get_selected_file(self):
        selection = self.tree.selection()
        if not selection:
            return None

        iid = selection[0]
        for stored_iid, path in self.parts:
            if iid == stored_iid:
                return path
        return None

    def on_double_click(self, event=None):
        path = self.get_selected_file()
        if path:
            self.open_original_file()

    def on_select(self, event=None):
        path = self.get_selected_file()
        if not path:
            return

        try:
            source = self.read_text(path)
        except Exception as e:
            messagebox.showerror("Read error", f"{path}\n\n{e}")
            return

        self.current_original = path
        self.current_source_text = source

        translation = self.find_existing_translation(path)
        self.current_translation = translation

        self.file_label.config(text=str(path))
        self.original_text.delete("1.0", "end")
        self.original_text.insert("1.0", source)

        self.translation_text.delete("1.0", "end")
        if translation:
            try:
                self.translation_text.insert("1.0", self.read_text(translation))
                self.status_label.config(text="Existing translation loaded")
            except Exception as e:
                self.status_label.config(text=f"Translation read error: {e}")
        else:
            self.status_label.config(text="No translation yet — ready to create")

        self.statusbar.config(text=f"Selected: {path}")

    @staticmethod
    def read_text(path):
        # Try common encodings used by subtitle/transcript files.
        for enc in ("utf-8-sig", "utf-8", "utf-16", "cp936", "gb18030", "cp Khmer"):
            try:
                return path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def find_existing_translation(source):
        candidates = []

        stem = source.with_suffix("")
        suffix = source.suffix

        for ext in (".km.txt", ".kh.txt", ".khmer.txt", "_khmer.txt",
                    "_km.txt", "_translated.txt", "_translation.txt"):
            candidates.append(Path(str(stem) + ext))

        # Also check sibling files with translation-like names.
        try:
            for f in source.parent.iterdir():
                if f.is_file() and f != source:
                    low = f.name.lower()
                    if source.stem.lower() in low and any(
                        x in low for x in ("khmer", "translation", "translated")
                    ):
                        candidates.append(f)
        except Exception:
            pass

        for c in candidates:
            if c.exists() and c.is_file():
                return c
        return None

    def copy_original(self):
        text = self.original_text.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.statusbar.config(text="Original transcript copied to clipboard.")

    def paste_translation(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Clipboard", "No text available in clipboard.")
            return

        self.translation_text.delete("1.0", "end")
        self.translation_text.insert("1.0", text)
        self.statusbar.config(text="Translation pasted from clipboard.")

    def save_translation(self):
        if not self.current_original:
            messagebox.showwarning("No file", "Select a transcript first.")
            return

        translation = self.translation_text.get("1.0", "end-1c")
        if not translation.strip():
            messagebox.showwarning(
                "Empty translation",
                "The translation box is empty. Enter or paste your translation first."
            )
            return

        existing = self.current_translation

        if existing:
            target = existing
        else:
            target = self.default_translation_path(self.current_original)

        try:
            self.write_text(target, translation)
            self.current_translation = target
            self.status_label.config(text=f"Saved: {target.name}")
            self.statusbar.config(text=f"Translation saved: {target}")

            # Refresh translation status without losing current selection.
            self.scan()
            self.select_path(self.current_original)

            messagebox.showinfo("Saved", f"Translation saved successfully:\n\n{target}")
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    def create_translation(self):
        if not self.current_original:
            messagebox.showwarning("No file", "Select a transcript first.")
            return

        translation = self.translation_text.get("1.0", "end-1c")
        if not translation.strip():
            messagebox.showwarning("Empty translation", "Enter the translation first.")
            return

        target = self.default_translation_path(self.current_original)

        if target.exists():
            if not messagebox.askyesno(
                "Already exists",
                f"{target.name} already exists.\n\nUpdate it?"
            ):
                return

        try:
            self.write_text(target, translation)
            self.current_translation = target
            self.statusbar.config(text=f"Created/updated: {target}")
            self.scan()
            self.select_path(self.current_original)
        except Exception as e:
            messagebox.showerror("Create error", str(e))

    @staticmethod
    def default_translation_path(source):
        # Keeps the original file untouched.
        # Example: episode_01.srt -> episode_01.kh.srt
        return source.with_name(source.stem + ".kh" + source.suffix)

    @staticmethod
    def write_text(path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8-sig", newline="")

    def save_as(self):
        if not self.current_original:
            messagebox.showwarning("No file", "Select a transcript first.")
            return

        initial = self.default_translation_path(self.current_original)
        target = filedialog.asksaveasfilename(
            title="Save translation as",
            initialdir=str(initial.parent),
            initialfile=initial.name,
            defaultextension=initial.suffix,
            filetypes=[
                ("Text files", "*.txt"),
                ("SRT subtitles", "*.srt"),
                ("VTT subtitles", "*.vtt"),
                ("JSON", "*.json"),
                ("All files", "*.*"),
            ],
        )
        if not target:
            return

        try:
            text = self.translation_text.get("1.0", "end-1c")
            self.write_text(Path(target), text)
            self.current_translation = Path(target)
            self.statusbar.config(text=f"Saved as: {target}")
            self.scan()
            self.select_path(self.current_original)
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    def open_original_file(self):
        path = self.current_original or self.get_selected_file()
        if not path:
            messagebox.showwarning("No file", "Select a transcript first.")
            return

        try:
            os.startfile(str(path))
        except Exception:
            try:
                subprocess.Popen(["explorer", "/select,", str(path)])
            except Exception as e:
                messagebox.showerror("Open error", str(e))

    def select_path(self, target):
        target = Path(target)
        for iid, path in self.parts:
            if path.resolve() == target.resolve():
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                self.on_select()
                return

if __name__ == "__main__":
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.0)
    except Exception:
        pass
    app = TranscriptApp(root)
    root.mainloop()
