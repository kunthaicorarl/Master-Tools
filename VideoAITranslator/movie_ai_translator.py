import sys
import json
import re
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QTreeWidget,
    QTreeWidgetItem, QTextEdit, QComboBox, QSplitter, QMessageBox,
    QLineEdit
)
from PySide6.QtWebEngineWidgets import QWebEngineView


AI_SITES = {
    "ChatGPT": "https://chatgpt.com/",
    "Grok": "https://grok.com/",
    "Gemini": "https://gemini.google.com/",
    "DeepSeek": "https://chat.deepseek.com/",
    "Claude": "https://claude.ai/",
}


class MovieTranslationApp(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle("AI Movie Subtitle Translator")
        self.resize(1600, 950)

        self.movie_folder = None
        self.current_file = None
        self.original_text = ""
        self.file_kind = None

        self.build_ui()

    # =========================================================
    # UI
    # =========================================================

    def build_ui(self):

        root = QWidget()
        main = QVBoxLayout(root)
        main.setContentsMargins(6, 6, 6, 6)

        # -----------------------------------------------------
        # TOP
        # -----------------------------------------------------

        top = QHBoxLayout()

        self.folder_label = QLabel(
            "Movie folder: Not selected"
        )
        self.folder_label.setMinimumWidth(500)

        browse = QPushButton("📁 Browse Movie Folder")
        browse.clicked.connect(
            self.choose_movie_folder
        )

        refresh = QPushButton("↻ Refresh")
        refresh.clicked.connect(
            self.scan_movie_folder
        )

        self.ai_combo = QComboBox()
        self.ai_combo.addItems(AI_SITES.keys())
        self.ai_combo.currentTextChanged.connect(
            self.change_ai
        )

        open_ai = QPushButton("Open AI")
        open_ai.clicked.connect(
            self.open_selected_ai
        )

        top.addWidget(self.folder_label, 1)
        top.addWidget(browse)
        top.addWidget(refresh)
        top.addWidget(QLabel("AI:"))
        top.addWidget(self.ai_combo)
        top.addWidget(open_ai)

        main.addLayout(top)

        # -----------------------------------------------------
        # MAIN SPLITTER
        # -----------------------------------------------------

        splitter = QSplitter(Qt.Horizontal)

        # =====================================================
        # LEFT
        # =====================================================

        left = QWidget()
        left_layout = QVBoxLayout(left)

        left_layout.addWidget(
            QLabel(
                "Movie → Part folders → Translation files"
            )
        )

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            ["Movie / Part / File"]
        )

        self.tree.itemDoubleClicked.connect(
            self.open_tree_item
        )

        left_layout.addWidget(self.tree)

        splitter.addWidget(left)

        # =====================================================
        # MIDDLE
        # =====================================================

        middle = QWidget()
        mid_layout = QVBoxLayout(middle)

        self.file_label = QLabel(
            "No file selected"
        )

        mid_layout.addWidget(
            self.file_label
        )

        self.editor = QTextEdit()

        self.editor.setPlaceholderText(
            "Open a translated JSON/SRT/TXT/VTT file here.\n"
            "Select text and click 'Copy Selected → AI'."
        )

        mid_layout.addWidget(
            self.editor,
            1
        )

        row1 = QHBoxLayout()

        copy_selected = QPushButton(
            "Copy Selected → AI"
        )
        copy_selected.clicked.connect(
            self.copy_selected_to_ai
        )

        copy_all = QPushButton(
            "Copy All → AI"
        )
        copy_all.clicked.connect(
            self.copy_all_to_ai
        )

        prompt = QPushButton(
            "Copy Khmer Translation Prompt"
        )
        prompt.clicked.connect(
            self.copy_translation_prompt
        )

        paste = QPushButton(
            "Paste AI Result"
        )
        paste.clicked.connect(
            self.paste_from_clipboard
        )

        save = QPushButton(
            "💾 Save JSON"
        )
        save.clicked.connect(
            self.save_file
        )

        row1.addWidget(copy_selected)
        row1.addWidget(copy_all)
        row1.addWidget(prompt)
        row1.addWidget(paste)
        row1.addWidget(save)

        mid_layout.addLayout(row1)

        self.status = QLabel(
            "Ready"
        )

        mid_layout.addWidget(
            self.status
        )

        splitter.addWidget(middle)

        # =====================================================
        # RIGHT - BROWSER
        # =====================================================

        right = QWidget()
        right_layout = QVBoxLayout(right)

        browser_bar = QHBoxLayout()

        back = QPushButton("←")
        back.clicked.connect(
            lambda: self.browser.back()
        )

        forward = QPushButton("→")
        forward.clicked.connect(
            lambda: self.browser.forward()
        )

        reload_btn = QPushButton("⟳")
        reload_btn.clicked.connect(
            lambda: self.browser.reload()
        )

        self.url = QLineEdit()
        self.url.setPlaceholderText(
            "AI website URL"
        )

        go = QPushButton("Go")
        go.clicked.connect(
            self.go_url
        )

        browser_bar.addWidget(back)
        browser_bar.addWidget(forward)
        browser_bar.addWidget(reload_btn)
        browser_bar.addWidget(self.url, 1)
        browser_bar.addWidget(go)

        right_layout.addLayout(
            browser_bar
        )

        self.browser = QWebEngineView()

        self.browser.urlChanged.connect(
            lambda url: self.url.setText(
                url.toString()
            )
        )

        right_layout.addWidget(
            self.browser,
            1
        )

        splitter.addWidget(right)

        splitter.setSizes(
            [360, 560, 680]
        )

        main.addWidget(
            splitter,
            1
        )

        self.setCentralWidget(root)

        # Open ChatGPT initially
        self.browser.setUrl(
            QUrl(AI_SITES["ChatGPT"])
        )

        # -----------------------------------------------------
        # Ctrl + S
        # -----------------------------------------------------

        save_action = QAction(
            self
        )

        save_action.setShortcut(
            QKeySequence("Ctrl+S")
        )

        save_action.triggered.connect(
            self.save_file
        )

        self.addAction(
            save_action
        )

    # =========================================================
    # MOVIE FOLDER
    # =========================================================

    def choose_movie_folder(self):

        folder = QFileDialog.getExistingDirectory(
            self,
            "Select Movie Folder",
            str(Path.home())
        )

        if folder:

            self.movie_folder = Path(folder)

            self.folder_label.setText(
                f"Movie folder: {self.movie_folder}"
            )

            self.scan_movie_folder()

    def scan_movie_folder(self):

        if not self.movie_folder:
            return

        self.tree.clear()

        movie_root = QTreeWidgetItem(
            [self.movie_folder.name]
        )

        movie_root.setData(
            0,
            Qt.UserRole,
            str(self.movie_folder)
        )

        self.tree.addTopLevelItem(
            movie_root
        )

        parts = sorted(
            [
                p
                for p in self.movie_folder.iterdir()
                if p.is_dir()
            ],
            key=lambda p: p.name.lower()
        )

        if not parts:

            self.add_files(
                movie_root,
                self.movie_folder
            )

        else:

            for part in parts:

                part_item = QTreeWidgetItem(
                    [part.name]
                )

                part_item.setData(
                    0,
                    Qt.UserRole,
                    str(part)
                )

                movie_root.addChild(
                    part_item
                )

                self.add_files(
                    part_item,
                    part
                )

        movie_root.setExpanded(
            True
        )

        self.status.setText(
            f"Found {len(parts)} part folders."
            if parts
            else
            "No Part folders found; showing files in movie folder."
        )

    def add_files(
        self,
        parent_item,
        folder
    ):

        allowed = {
            ".json",
            ".srt",
            ".txt",
            ".vtt"
        }

        files = sorted(
            [
                p
                for p in folder.iterdir()
                if (
                    p.is_file()
                    and p.suffix.lower() in allowed
                )
            ],
            key=lambda p: p.name.lower()
        )

        for f in files:

            item = QTreeWidgetItem(
                [f.name]
            )

            item.setData(
                0,
                Qt.UserRole,
                str(f)
            )

            parent_item.addChild(
                item
            )

    def open_tree_item(
        self,
        item,
        column=0
    ):

        value = item.data(
            0,
            Qt.UserRole
        )

        if not value:
            return

        path = Path(value)

        if path.is_file():

            self.load_translation_file(
                path
            )

    # =========================================================
    # LOAD FILE
    # =========================================================

    def load_translation_file(
        self,
        path: Path
    ):

        try:

            text = path.read_text(
                encoding="utf-8-sig"
            )

        except UnicodeDecodeError:

            text = path.read_text(
                encoding="utf-8",
                errors="replace"
            )

        self.current_file = path

        self.file_label.setText(
            f"Editing: {path}"
        )

        self.original_text = text

        self.file_kind = (
            path.suffix.lower()
        )

        # JSON
        if self.file_kind == ".json":

            try:

                obj = json.loads(text)

                text = json.dumps(
                    obj,
                    ensure_ascii=False,
                    indent=2
                )

            except Exception:
                pass

        # SRT
        elif self.file_kind == ".srt":

            try:

                obj = self.parse_srt(
                    text
                )

                text = json.dumps(
                    obj,
                    ensure_ascii=False,
                    indent=2
                )

            except Exception as e:

                QMessageBox.warning(
                    self,
                    "SRT Error",
                    str(e)
                )

        # VTT
        elif self.file_kind == ".vtt":

            try:

                obj = self.parse_vtt(
                    text
                )

                text = json.dumps(
                    obj,
                    ensure_ascii=False,
                    indent=2
                )

            except Exception as e:

                QMessageBox.warning(
                    self,
                    "VTT Error",
                    str(e)
                )

        self.editor.setPlainText(
            text
        )

        self.status.setText(
            f"Loaded: {path.name}"
        )

    # =========================================================
    # TIMESTAMP
    # =========================================================

    def format_timestamp(
        self,
        value
    ):
        """
        Convert timestamps to:

        HH:MM:SS,mmm
        """

        if value is None:
            return "00:00:00,000"

        # -----------------------------------------------------
        # String
        # -----------------------------------------------------

        if isinstance(
            value,
            str
        ):

            value = value.strip()

            # Already SRT format
            if re.fullmatch(
                r"\d{2}:\d{2}:\d{2},\d{3}",
                value
            ):
                return value

            # HH:MM:SS.mmm
            if re.fullmatch(
                r"\d{2}:\d{2}:\d{2}\.\d{3}",
                value
            ):

                return value.replace(
                    ".",
                    ","
                )

            # HH:MM:SS
            if re.fullmatch(
                r"\d{2}:\d{2}:\d{2}",
                value
            ):

                return value + ",000"

            # Numeric string
            try:
                value = float(value)

            except Exception:

                return value

        # -----------------------------------------------------
        # Numeric seconds
        # -----------------------------------------------------

        try:

            total_ms = round(
                float(value) * 1000
            )

        except Exception:

            return str(value)

        hours = (
            total_ms // 3600000
        )

        total_ms %= 3600000

        minutes = (
            total_ms // 60000
        )

        total_ms %= 60000

        seconds = (
            total_ms // 1000
        )

        milliseconds = (
            total_ms % 1000
        )

        return (
            f"{hours:02d}:"
            f"{minutes:02d}:"
            f"{seconds:02d},"
            f"{milliseconds:03d}"
        )

    # =========================================================
    # SRT PARSER
    # =========================================================

    def parse_srt(
        self,
        text
    ):

        result = []

        blocks = re.split(
            r"\n\s*\n",
            text.strip()
        )

        for block in blocks:

            lines = block.splitlines()

            if not lines:
                continue

            # Find timestamp line
            time_index = None

            for i, line in enumerate(lines):

                if "-->" in line:

                    time_index = i
                    break

            if time_index is None:
                continue

            timing = lines[
                time_index
            ]

            parts = re.split(
                r"\s+-->\s+",
                timing
            )

            if len(parts) != 2:
                continue

            start = self.format_timestamp(
                parts[0].strip()
            )

            end = self.format_timestamp(
                parts[1].strip()
            )

            subtitle_text = "\n".join(
                lines[time_index + 1:]
            ).strip()

            result.append(
                {
                    "start": start,
                    "end": end,
                    "text": subtitle_text,
                    "khmer": ""
                }
            )

        return result

    # =========================================================
    # VTT PARSER
    # =========================================================

    def parse_vtt(
        self,
        text
    ):

        result = []

        blocks = re.split(
            r"\n\s*\n",
            text.strip()
        )

        for block in blocks:

            lines = block.splitlines()

            if not lines:
                continue

            time_index = None

            for i, line in enumerate(lines):

                if "-->" in line:

                    time_index = i
                    break

            if time_index is None:
                continue

            timing = lines[
                time_index
            ]

            parts = re.split(
                r"\s+-->\s+",
                timing
            )

            if len(parts) != 2:
                continue

            start = self.format_timestamp(
                parts[0].strip()
            )

            end = self.format_timestamp(
                parts[1].strip()
            )

            subtitle_text = "\n".join(
                lines[time_index + 1:]
            ).strip()

            result.append(
                {
                    "start": start,
                    "end": end,
                    "text": subtitle_text,
                    "khmer": ""
                }
            )

        return result

    # =========================================================
    # CONVERT JSON TO REQUIRED FORMAT
    # =========================================================

    def convert_to_output_json(
        self,
        text
    ):

        data = json.loads(
            text
        )

        if not isinstance(
            data,
            list
        ):

            raise ValueError(
                "JSON root must be an array."
            )

        result = []

        for item in data:

            if not isinstance(
                item,
                dict
            ):
                continue

            start = item.get(
                "start",
                ""
            )

            end = item.get(
                "end",
                ""
            )

            chinese = item.get(
                "text",
                ""
            )

            khmer = item.get(
                "khmer",
                ""
            )

            result.append(
                {
                    "start": self.format_timestamp(
                        start
                    ),
                    "end": self.format_timestamp(
                        end
                    ),
                    "text": str(
                        chinese
                    ),
                    "khmer": str(
                        khmer
                    )
                }
            )

        return result

    # =========================================================
    # SAVE
    # =========================================================

    def save_file(self):

        if not self.current_file:

            QMessageBox.information(
                self,
                "Save",
                "Please select a file first."
            )

            return

        text = self.editor.toPlainText().strip()

        if not text:

            QMessageBox.warning(
                self,
                "Save",
                "There is no subtitle data to save."
            )

            return

        try:

            # -------------------------------------------------
            # Convert editor content to required JSON
            # -------------------------------------------------

            output_data = (
                self.convert_to_output_json(
                    text
                )
            )

            # -------------------------------------------------
            # ALWAYS SAVE AS JSON
            # -------------------------------------------------

            output_path = (
                self.current_file.with_suffix(
                    ".json"
                )
            )

            output_text = json.dumps(
                output_data,
                ensure_ascii=False,
                indent=2
            )

            output_path.write_text(
                output_text,
                encoding="utf-8"
            )

            # -------------------------------------------------
            # Update current file
            # -------------------------------------------------

            self.current_file = (
                output_path
            )

            self.file_kind = ".json"

            self.file_label.setText(
                f"Editing: {output_path}"
            )

            self.editor.setPlainText(
                output_text
            )

            self.status.setText(
                f"Saved JSON: {output_path.name}"
            )

            # Refresh tree so generated JSON appears
            self.scan_movie_folder()

        except json.JSONDecodeError as e:

            QMessageBox.warning(
                self,
                "Invalid JSON",
                (
                    "The subtitle data is not valid JSON:\n\n"
                    f"{e}"
                )
            )

        except Exception as e:

            QMessageBox.critical(
                self,
                "Save error",
                str(e)
            )

    # =========================================================
    # AI BROWSER
    # =========================================================

    def change_ai(
        self,
        name
    ):

        url = AI_SITES.get(
            name
        )

        if url:

            self.browser.setUrl(
                QUrl(url)
            )

    def open_selected_ai(self):

        self.change_ai(
            self.ai_combo.currentText()
        )

    def go_url(self):

        text = self.url.text().strip()

        if not text:
            return

        if not text.startswith(
            (
                "http://",
                "https://"
            )
        ):

            text = (
                "https://" + text
            )

        self.browser.setUrl(
            QUrl(text)
        )

    # =========================================================
    # COPY / PASTE
    # =========================================================

    def copy_selected_to_ai(self):

        text = (
            self.editor.textCursor()
            .selectedText()
        )

        if not text:

            QMessageBox.information(
                self,
                "Copy to AI",
                "Select the Chinese subtitle text you want to translate."
            )

            return

        QApplication.clipboard().setText(
            text
        )

        self.status.setText(
            "Selected text copied. "
            "Click the AI chat box and press Ctrl+V."
        )

        self.browser.setFocus()

    def copy_all_to_ai(self):

        text = (
            self.editor.toPlainText()
        )

        if not text.strip():
            return

        QApplication.clipboard().setText(
            text
        )

        self.status.setText(
            "All text copied. "
            "Click the AI chat box and press Ctrl+V."
        )

        self.browser.setFocus()

    def copy_translation_prompt(self):

        prompt = """Translate the following Chinese subtitle/dialogue into natural Khmer.

Requirements:
- Use natural, fluent Khmer suitable for a Chinese historical drama.
- Preserve the original meaning, emotion, and speaking style.
- Do not add explanations.
- Keep names and important historical terms consistent.
- If this is subtitle text, keep it concise enough for subtitles.
- Return only the Khmer translation.

Chinese text:
"""

        QApplication.clipboard().setText(
            prompt
        )

        self.status.setText(
            "Translation prompt copied to clipboard."
        )

    def paste_from_clipboard(self):

        text = (
            QApplication.clipboard().text()
        )

        if not text:
            return

        cursor = (
            self.editor.textCursor()
        )

        if cursor.hasSelection():

            cursor.insertText(
                text
            )

        else:

            self.editor.insertPlainText(
                text
            )

        self.status.setText(
            "AI result pasted into editor."
        )

    # =========================================================
    # KEYBOARD
    # =========================================================

    def keyPressEvent(
        self,
        event
    ):

        if (
            event.modifiers()
            == (
                Qt.ControlModifier
                | Qt.ShiftModifier
            )
            and
            event.key()
            == Qt.Key_V
        ):

            self.paste_from_clipboard()

            event.accept()

            return

        super().keyPressEvent(
            event
        )


# =============================================================
# MAIN
# =============================================================

def main():

    app = QApplication(
        sys.argv
    )

    app.setApplicationName(
        "AI Movie Subtitle Translator"
    )

    window = MovieTranslationApp()

    window.show()

    sys.exit(
        app.exec()
    )


if __name__ == "__main__":

    main()