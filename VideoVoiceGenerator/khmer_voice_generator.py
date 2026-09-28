import sys
import json
import re
import subprocess
import importlib.util
from pathlib import Path

# Auto-install required packages
def ensure_package(package_name, import_name=None):
    if import_name is None:
        import_name = package_name
    if importlib.util.find_spec(import_name) is None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])

ensure_package("PySide6")
ensure_package("edge-tts", "edge_tts")

import edge_tts
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QListWidget, QListWidgetItem,
    QProgressBar, QTextEdit, QComboBox, QMessageBox, QCheckBox
)


VOICE_LIST = {
    "Sreymom (Female)": "km-KH-SreymomNeural",
    "Piseth (Male)": "km-KH-PisethNeural",
}


def natural_part_number(name):
    m = re.search(r"\bpart\s*[-_ ]?\s*(\d+)\b", name, re.IGNORECASE)
    return int(m.group(1)) if m else 999999


def get_part_name(source_file: Path) -> str:
    """
    The Part name is the actual folder name when the JSON is inside a
    Part folder. If the JSON is still directly in the main folder, derive
    the Part name from the filename.
    """
    # Example: Series/Part 1/Part 1.json -> Part 1
    if source_file.parent.name.lower().startswith("part "):
        return source_file.parent.name

    stem = source_file.stem.strip()
    m = re.search(r"\bpart\s*[-_ ]?\s*(\d+)\b", stem, re.IGNORECASE)
    if m:
        return f"Part {int(m.group(1))}"
    return stem


def discover_parts(series_folder: Path):
    """
    Find Parts and use the existing Part folder name for the display.

    Preferred structure:
        Series/
          Part 1/Part 1.json
          Part 2/Part 2.json

    For compatibility, JSON files directly in Series/ are also accepted.
    """
    found = []

    # Prefer JSON files inside existing Part folders.
    for d in series_folder.iterdir():
        if d.is_dir():
            json_files = sorted(d.glob("*.json"))
            if json_files:
                # Use the first JSON as the source for that Part folder.
                found.append(json_files[0])

    # Also support JSON files directly in the main folder.
    for p in series_folder.glob("*.json"):
        found.append(p)

    unique = {}
    for p in found:
        unique[str(p.resolve())] = p

    parts = list(unique.values())

    def sort_key(p):
        name = get_part_name(p)
        num = natural_part_number(name)
        return (num, name.lower(), p.name.lower())

    parts.sort(key=sort_key)
    return parts


class GenerateWorker(QThread):
    progress = Signal(int)
    status = Signal(str)
    finished_part = Signal(str)
    error = Signal(str)
    all_done = Signal()

    def __init__(self, jobs, voice, output_root):
        super().__init__()
        self.jobs = jobs
        self.voice = voice
        self.output_root = Path(output_root)
        self.stop_requested = False

    def stop(self):
        self.stop_requested = True

    async def generate_one(self, item, index, total):
        json_file = Path(item["file"])
        part_name = item["part_name"]
        series_name = item["series_name"]

        # Output:
        # VoiceOutput/
        #   Series Name/
        #     Part 1/
        #       0001.mp3
        part_output = self.output_root / part_name / 'Voices'
        part_output.mkdir(parents=True, exist_ok=True)

        try:
            data = json.loads(json_file.read_text(encoding="utf-8-sig"))
        except Exception as e:
            raise RuntimeError(f"Cannot read JSON: {json_file.name}\n{e}")

        if not isinstance(data, list):
            raise RuntimeError(f"JSON must contain an array: {json_file.name}")

        # Copy the source JSON into the Part folder
        try:
            (part_output / json_file.name).write_text(
                json_file.read_text(encoding="utf-8-sig"),
                encoding="utf-8"
            )
        except Exception:
            pass

        entries = []
        for obj in data:
            if not isinstance(obj, dict):
                continue

            # IMPORTANT: TTS uses ONLY the Khmer field.
            khmer = str(obj.get("khmer", "") or "").strip()
            if khmer:
                entries.append(khmer)

        if not entries:
            raise RuntimeError(
                f"No Khmer text found in the 'khmer' field: {json_file.name}"
            )

        self.status.emit(
            f"{part_name}: generating {len(entries)} voice files..."
        )

        for i, text in enumerate(entries, start=1):
            if self.stop_requested:
                return

            out_file = part_output / f"{i:04d}.mp3"

            try:
                communicate = edge_tts.Communicate(text, self.voice)
                await communicate.save(str(out_file))
            except Exception as e:
                raise RuntimeError(
                    f"{part_name} - line {i}: {e}"
                )

            # Overall progress
            completed_parts = index
            percent = int(((completed_parts - 1) / total) * 100 +
                          (i / len(entries)) * (100 / total))
            self.progress.emit(min(100, percent))
            self.status.emit(
                f"{part_name}: {i}/{len(entries)} -> {out_file.name}"
            )

        self.finished_part.emit(
            f"✓ {part_name}: {len(entries)} files"
        )

    def run(self):
        import asyncio

        async def main():
            total = len(self.jobs)

            for index, job in enumerate(self.jobs, start=1):
                if self.stop_requested:
                    break

                try:
                    await self.generate_one(job, index, total)
                except Exception as e:
                    self.error.emit(str(e))
                    # Continue with the next selected part

            if self.stop_requested:
                self.status.emit("Stopped.")
            else:
                self.progress.emit(100)
                self.status.emit("All selected parts completed.")

            self.all_done.emit()

        try:
            asyncio.run(main())
        except Exception as e:
            self.error.emit(str(e))
            self.all_done.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Khmer Voice Generator - Parts")
        self.resize(900, 700)

        self.series_folder = None
        self.parts = []
        self.worker = None

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        title = QLabel("Khmer Voice Generator")
        title.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(title)

        # Series folder
        row = QHBoxLayout()
        self.folder_label = QLabel("Series Folder: Not selected")
        self.browse_btn = QPushButton("Browse Series Folder")
        self.browse_btn.clicked.connect(self.browse_series_folder)
        row.addWidget(self.folder_label, 1)
        row.addWidget(self.browse_btn)
        layout.addLayout(row)

        self.series_label = QLabel("Series: -")
        self.series_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; margin-top: 6px;"
        )
        layout.addWidget(self.series_label)

        # Part list
        layout.addWidget(QLabel("Parts:"))

        self.part_list = QListWidget()
        layout.addWidget(self.part_list, 1)

        # Selection buttons
        row = QHBoxLayout()

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.clicked.connect(self.select_all)

        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear_selection)

        row.addWidget(self.select_all_btn)
        row.addWidget(self.clear_btn)
        row.addStretch()

        layout.addLayout(row)

        # Voice
        voice_row = QHBoxLayout()
        voice_row.addWidget(QLabel("Khmer Voice:"))

        self.voice_combo = QComboBox()
        self.voice_combo.addItems(VOICE_LIST.keys())
        voice_row.addWidget(self.voice_combo, 1)

        layout.addLayout(voice_row)

        # Output folder
        output_row = QHBoxLayout()

        self.output_label = QLabel("Output: Same as selected Series Folder")
        self.output_btn = QPushButton("Browse Output Folder")
        self.output_btn.clicked.connect(self.browse_output_folder)

        output_row.addWidget(self.output_label, 1)
        output_row.addWidget(self.output_btn)

        layout.addLayout(output_row)

        # Generate buttons
        button_row = QHBoxLayout()

        self.generate_selected_btn = QPushButton("Generate Selected Parts")
        self.generate_selected_btn.clicked.connect(self.generate_selected)

        self.generate_all_btn = QPushButton("Generate All Parts")
        self.generate_all_btn.clicked.connect(self.generate_all)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop_generation)
        self.stop_btn.setEnabled(False)

        button_row.addWidget(self.generate_selected_btn)
        button_row.addWidget(self.generate_all_btn)
        button_row.addWidget(self.stop_btn)

        layout.addLayout(button_row)

        # Progress
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        # Log
        layout.addWidget(QLabel("Status:"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)

        self.output_folder = None

    def log_message(self, message):
        self.log.append(message)

    def browse_series_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Series Folder"
        )

        if not folder:
            return

        self.load_series_folder(Path(folder))

    def load_series_folder(self, folder: Path):
        self.series_folder = folder
        self.parts = discover_parts(folder)

        # Save each Part directly inside the selected main/series folder.
        self.output_folder = folder
        self.output_label.setText(f"Output: {folder}\\Part 1, Part 2, ...")

        self.folder_label.setText(f"Series Folder: {folder}")
        self.series_label.setText(f"Series: {folder.name}")

        self.part_list.clear()

        if not self.parts:
            self.log_message("No JSON parts found.")
            return

        for p in self.parts:
            part_name = get_part_name(p)

            item = QListWidgetItem()
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled |
                Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(Qt.CheckState.Unchecked)

            # Display ONLY the actual Part folder name.
            # Example: Part 1, Part 2, Part 3
            item.setText(part_name)
            item.setToolTip(f"Source JSON: {p}")
            item.setData(Qt.ItemDataRole.UserRole, str(p))

            self.part_list.addItem(item)

        self.log_message(
            f"Loaded {len(self.parts)} parts from: {folder.name}"
        )

    def browse_output_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Output Folder"
        )

        if folder:
            self.output_folder = Path(folder)
            self.output_label.setText(f"Output: {folder}\\Part 1, Part 2, ...")

    def select_all(self):
        for i in range(self.part_list.count()):
            self.part_list.item(i).setCheckState(Qt.CheckState.Checked)

    def clear_selection(self):
        for i in range(self.part_list.count()):
            self.part_list.item(i).setCheckState(Qt.CheckState.Unchecked)

    def get_selected_jobs(self, all_parts=False):
        if not self.series_folder:
            QMessageBox.warning(
                self,
                "No Series Folder",
                "Please select a series folder first."
            )
            return []

        jobs = []

        for i, p in enumerate(self.parts):
            item = self.part_list.item(i)

            if all_parts or item.checkState() == Qt.CheckState.Checked:
                jobs.append({
                    "file": str(p),
                    "part_name": get_part_name(p),
                    "series_name": self.series_folder.name,
                })

        if not jobs:
            QMessageBox.warning(
                self,
                "No Parts Selected",
                "Please select at least one part."
            )

        return jobs

    def start_generation(self, jobs):
        if self.worker and self.worker.isRunning():
            return

        voice = VOICE_LIST[self.voice_combo.currentText()]

        output_root = self.output_folder or self.series_folder

        self.worker = GenerateWorker(
            jobs,
            voice,
            output_root
        )

        self.worker.progress.connect(self.progress_bar.setValue)
        self.worker.status.connect(self.log_message)
        self.worker.finished_part.connect(self.log_message)
        self.worker.error.connect(
            lambda msg: self.log_message(f"ERROR: {msg}")
        )
        self.worker.all_done.connect(self.generation_finished)

        self.generate_selected_btn.setEnabled(False)
        self.generate_all_btn.setEnabled(False)
        self.browse_btn.setEnabled(False)
        self.output_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.progress_bar.setValue(0)
        self.log_message(
            f"Starting {len(jobs)} part(s)..."
        )

        self.worker.start()

    def generate_selected(self):
        jobs = self.get_selected_jobs(False)
        if jobs:
            self.start_generation(jobs)

    def generate_all(self):
        jobs = self.get_selected_jobs(True)
        if jobs:
            self.start_generation(jobs)

    def stop_generation(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.log_message("Stopping after the current operation...")

    def generation_finished(self):
        self.generate_selected_btn.setEnabled(True)
        self.generate_all_btn.setEnabled(True)
        self.browse_btn.setEnabled(True)
        self.output_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

        self.progress_bar.setValue(100)

        QMessageBox.information(
            self,
            "Finished",
            "Voice generation has finished."
        )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
