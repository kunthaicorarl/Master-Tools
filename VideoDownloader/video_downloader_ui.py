import os
import time
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

import requests
import tkinter as tk
from tkinter import ttk, messagebox


# ============================================================
# CONFIGURATION
# ============================================================

# Number of parallel connections for direct downloads
PARTS = 8

# Retry count
MAX_RETRIES = 8

# Download chunk size
CHUNK_SIZE = 1024 * 1024  # 1 MB

# HTTP timeout
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 60

# ============================================================
# YOUTUBE SETTINGS
# ============================================================

# Browser options:
#   chrome
#   edge
#   firefox
#   brave
#   opera
#
# Only used when USE_BROWSER_COOKIES = True
YOUTUBE_BROWSER = "chrome"

# IMPORTANT:
# False = Do NOT access Chrome cookies.
#
# This avoids:
#   "Could not copy Chrome cookie database"
#
# Set to True only if a YouTube video actually requires
# your logged-in browser session.
USE_BROWSER_COOKIES = False


# ============================================================
# PATHS
# ============================================================

# VideoDownloader/
BASE_DIR = Path(__file__).resolve().parent

# VideoDownloader/downloads/
DOWNLOAD_DIR = BASE_DIR / "downloads"

# VideoDownloader/temp/
TEMP_DIR = BASE_DIR / "temp"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def format_bytes(value):
    units = ["B", "KB", "MB", "GB", "TB"]

    for unit in units:
        if value < 1024:
            return f"{value:.1f} {unit}"

        value /= 1024

    return f"{value:.1f} PB"


def format_time(seconds):
    if seconds <= 0:
        return "--:--"

    seconds = int(seconds)

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    return f"{minutes:02d}:{secs:02d}"


def safe_filename(name):
    """
    Remove characters that are invalid on Windows.
    """

    if not name:
        return "video"

    invalid = '<>:"/\\|?*'

    name = "".join(
        c for c in name
        if c not in invalid
    )

    name = name.strip().rstrip(".")

    if not name:
        return "video"

    return name


# ============================================================
# DIRECT URL DOWNLOADER
# ============================================================

def get_file_size(url):
    headers = {
        "Range": "bytes=0-0",
        "User-Agent": "Mozilla/5.0",
    }

    response = requests.get(
        url,
        headers=headers,
        stream=True,
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        allow_redirects=True,
    )

    response.raise_for_status()

    content_range = response.headers.get("Content-Range")

    if content_range:
        total = int(content_range.split("/")[-1])

        response.close()

        return total

    content_length = response.headers.get("Content-Length")

    response.close()

    if content_length:
        return int(content_length)

    raise RuntimeError(
        "Server did not provide the file size."
    )


def create_parts(total_size, output):
    parts = []

    part_size = total_size // PARTS

    for i in range(PARTS):

        start = i * part_size

        end = (
            total_size - 1
            if i == PARTS - 1
            else start + part_size - 1
        )

        parts.append({
            "index": i,
            "start": start,
            "end": end,
            "file": f"{output}.part{i}",
        })

    return parts


def existing_part_size(part):

    filename = part["file"]

    if not os.path.exists(filename):
        return 0

    size = os.path.getsize(filename)

    expected = (
        part["end"]
        - part["start"]
        + 1
    )

    if size > expected:

        try:
            os.remove(filename)
        except OSError:
            pass

        return 0

    return size


def download_part(
    url,
    part,
    progress_callback,
):

    start = part["start"]
    end = part["end"]
    filename = part["file"]

    existing = existing_part_size(part)

    expected = end - start + 1

    if existing >= expected:

        progress_callback(existing)

        return part["index"], True

    current_start = start + existing

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        response = None

        try:

            headers = {
                "Range":
                    f"bytes={current_start}-{end}",

                "User-Agent":
                    "Mozilla/5.0",
            }

            response = requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT,
                ),
                allow_redirects=True,
            )

            if response.status_code not in (
                200,
                206,
            ):
                raise RuntimeError(
                    f"HTTP {response.status_code}"
                )

            mode = (
                "ab"
                if existing > 0
                else "wb"
            )

            with open(
                filename,
                mode
            ) as f:

                for chunk in response.iter_content(
                    chunk_size=CHUNK_SIZE
                ):

                    if not chunk:
                        continue

                    f.write(chunk)

                    amount = len(chunk)

                    existing += amount
                    current_start += amount

                    progress_callback(amount)

            if existing >= expected:

                return part["index"], True

            raise RuntimeError(
                f"Incomplete part "
                f"{existing}/{expected}"
            )

        except Exception:

            if attempt == MAX_RETRIES:
                break

            time.sleep(
                min(
                    attempt * 2,
                    10,
                )
            )

            existing = existing_part_size(part)

            current_start = (
                start + existing
            )

        finally:

            if response is not None:
                response.close()

    return part["index"], False


def merge_parts(parts, output):

    temp_output = (
        f"{output}.merging"
    )

    with open(
        temp_output,
        "wb"
    ) as final_file:

        for part in parts:

            filename = part["file"]

            if not os.path.exists(filename):
                raise RuntimeError(
                    f"Missing part: {filename}"
                )

            with open(
                filename,
                "rb"
            ) as source:

                while True:

                    data = source.read(
                        CHUNK_SIZE
                    )

                    if not data:
                        break

                    final_file.write(data)

    os.replace(
        temp_output,
        output
    )


def cleanup(parts):

    for part in parts:

        try:

            filename = part["file"]

            if os.path.exists(filename):
                os.remove(filename)

        except OSError:
            pass


def guess_filename(url):

    path = urlparse(url).path

    name = os.path.basename(path)

    if name and "." in name:

        name = safe_filename(name)

        if name:
            return name

    return "video.mp4"


def get_unique_output_path(filename):

    filename = safe_filename(filename)

    output = DOWNLOAD_DIR / filename

    if not output.exists():
        return output

    base = output.stem
    ext = output.suffix

    n = 1

    while True:

        candidate = (
            DOWNLOAD_DIR
            / f"{base}_{n}{ext}"
        )

        if not candidate.exists():
            return candidate

        n += 1


def download_url(
    url,
    progress_callback,
    status_callback,
):

    DOWNLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    status_callback(
        "Checking URL and file size..."
    )

    total_size = get_file_size(url)

    filename = guess_filename(url)

    video_extensions = (
        ".mp4",
        ".mkv",
        ".webm",
        ".mov",
        ".avi",
        ".ts",
        ".m4v",
    )

    if not filename.lower().endswith(
        video_extensions
    ):
        filename += ".mp4"

    output = get_unique_output_path(
        filename
    )

    parts = create_parts(
        total_size,
        str(output),
    )

    downloaded_total = 0

    for part in parts:

        downloaded_total += (
            existing_part_size(part)
        )

    progress_callback(
        downloaded_total,
        total_size,
    )

    status_callback(
        f"Downloading "
        f"{format_bytes(total_size)} "
        f"with {PARTS} parallel parts..."
    )

    progress_lock = threading.Lock()

    start_time = time.time()

    def on_progress(amount):

        nonlocal downloaded_total

        with progress_lock:

            downloaded_total += amount

            current = downloaded_total

        elapsed = max(
            time.time() - start_time,
            0.001,
        )

        speed = current / elapsed

        remaining = max(
            total_size - current,
            0,
        )

        eta = (
            remaining / speed
            if speed > 0
            else 0
        )

        progress_callback(
            current,
            total_size,
            f"{format_bytes(current)} / "
            f"{format_bytes(total_size)}"
            f"  •  "
            f"{format_bytes(speed)}/s"
            f"  •  ETA "
            f"{format_time(eta)}",
        )

    failed = []

    with ThreadPoolExecutor(
        max_workers=PARTS
    ) as executor:

        futures = [
            executor.submit(
                download_part,
                url,
                part,
                on_progress,
            )
            for part in parts
        ]

        for future in as_completed(
            futures
        ):

            index, success = (
                future.result()
            )

            if not success:
                failed.append(index)

    if failed:

        raise RuntimeError(
            "Download failed for part(s): "
            + ", ".join(
                str(i + 1)
                for i in failed
            )
        )

    status_callback(
        "Merging downloaded parts..."
    )

    merge_parts(
        parts,
        str(output),
    )

    final_size = output.stat().st_size

    if final_size != total_size:

        raise RuntimeError(
            "Final file size mismatch: "
            f"expected {total_size}, "
            f"got {final_size}"
        )

    cleanup(parts)

    elapsed = max(
        time.time() - start_time,
        0.001,
    )

    progress_callback(
        total_size,
        total_size,
        f"{format_bytes(total_size)} / "
        f"{format_bytes(total_size)}"
        f"  •  Average "
        f"{format_bytes(total_size / elapsed)}/s",
    )

    return str(output)


# ============================================================
# SOURCE DETECTION
# ============================================================

def detect_source(url):

    host = (
        urlparse(url)
        .netloc
        .lower()
        .split(":")[0]
    )

    youtube_hosts = (
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "youtu.be",
        "www.youtu.be",
    )

    vimeo_hosts = (
        "vimeo.com",
        "www.vimeo.com",
    )

    facebook_hosts = (
        "facebook.com",
        "www.facebook.com",
        "m.facebook.com",
        "fb.watch",
    )

    tiktok_hosts = (
        "tiktok.com",
        "www.tiktok.com",
        "vm.tiktok.com",
    )

    if host in youtube_hosts:
        return "YouTube"

    if host in vimeo_hosts:
        return "Vimeo"

    if host in facebook_hosts:
        return "Facebook"

    if host in tiktok_hosts:
        return "TikTok"

    return "Direct URL"


# ============================================================
# YOUTUBE DOWNLOADER
# ============================================================

def download_youtube(
    url,
    progress_callback,
    status_callback,
):

    try:

        import yt_dlp

    except ImportError:

        raise RuntimeError(
            "YouTube support requires yt-dlp.\n\n"
            "Install with:\n"
            "pip install -U yt-dlp"
        )

    DOWNLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    def hook(data):

        status = data.get("status")

        if status == "downloading":

            downloaded = (
                data.get(
                    "downloaded_bytes",
                    0,
                )
                or 0
            )

            total = (
                data.get(
                    "total_bytes",
                    0,
                )
                or data.get(
                    "total_bytes_estimate",
                    0,
                )
                or 0
            )

            speed = (
                data.get("speed", 0)
                or 0
            )

            eta = (
                data.get("eta", 0)
                or 0
            )

            detail = format_bytes(
                downloaded
            )

            if total:

                detail += (
                    f" / "
                    f"{format_bytes(total)}"
                )

            if speed:

                detail += (
                    f"  •  "
                    f"{format_bytes(speed)}/s"
                )

            if eta:

                detail += (
                    f"  •  ETA "
                    f"{format_time(eta)}"
                )

            progress_callback(
                downloaded,
                total or 1,
                detail,
            )

            status_callback(
                "Downloading YouTube video..."
            )

        elif status == "finished":

            progress_callback(
                1,
                1,
                "Download stream finished. "
                "Processing file...",
            )

            status_callback(
                "Processing downloaded video..."
            )

    # ========================================================
    # BASE OPTIONS
    # ========================================================

    opts = {

        # ----------------------------------------------------
        # FORMAT
        # ----------------------------------------------------

        "format":
            "bestvideo*+bestaudio/best",

        "format_sort": [
            "res:1080",
            "fps",
            "codec:avc:m4a",
            "br",
        ],

        # ----------------------------------------------------
        # OUTPUT
        # ----------------------------------------------------

        "merge_output_format":
            "mp4",

        "outtmpl":
            str(
                DOWNLOAD_DIR
                / "%(title)s.%(ext)s"
            ),

        # ----------------------------------------------------
        # DOWNLOAD
        # ----------------------------------------------------

        "progress_hooks": [
            hook
        ],

        "noplaylist": True,

        "retries": 10,

        "fragment_retries": 10,

        "continuedl": True,

        "concurrent_fragment_downloads": 8,

        "socket_timeout": 30,

        # ----------------------------------------------------
        # UI / LOGGING
        # ----------------------------------------------------

        "quiet": True,

        "no_warnings": True,
    }

    # ========================================================
    # OPTIONAL BROWSER COOKIES
    # ========================================================
    #
    # By default this is FALSE.
    #
    # This prevents yt-dlp from touching Chrome's cookie DB.
    #
    if USE_BROWSER_COOKIES:

        opts["cookiesfrombrowser"] = (
            YOUTUBE_BROWSER,
        )

        status_callback(
            f"Using {YOUTUBE_BROWSER} browser cookies..."
        )

    else:

        status_callback(
            "Downloading without browser cookies..."
        )

    # ========================================================
    # DOWNLOAD
    # ========================================================

    try:

        with yt_dlp.YoutubeDL(
            opts
        ) as ydl:

            info = ydl.extract_info(
                url,
                download=True,
            )

            filename = (
                ydl.prepare_filename(
                    info
                )
            )

            if (
                info.get(
                    "requested_downloads"
                )
                and opts.get(
                    "merge_output_format"
                ) == "mp4"
            ):

                filename = (
                    os.path.splitext(
                        filename
                    )[0]
                    + ".mp4"
                )

            return filename

    except Exception as e:

        error = str(e)

        error_lower = error.lower()

        # ----------------------------------------------------
        # Chrome cookie database error
        # ----------------------------------------------------

        if (
            "could not copy" in error_lower
            and "cookie" in error_lower
        ):

            raise RuntimeError(
                "Could not access the browser cookie database.\n\n"
                "The downloader is currently configured to use "
                f"{YOUTUBE_BROWSER} cookies.\n\n"
                "Try one of these:\n\n"
                "1. Close the browser completely and retry.\n"
                "2. Set USE_BROWSER_COOKIES = False.\n"
                "3. Use Firefox instead of Chrome.\n"
                "4. Update yt-dlp:\n"
                "   python -m pip install -U yt-dlp"
            )

        # ----------------------------------------------------
        # YouTube authentication
        # ----------------------------------------------------

        if (
            "sign in to confirm" in error_lower
            or "not a bot" in error_lower
            or "cookies" in error_lower
        ):

            raise RuntimeError(
                "YouTube requires authentication.\n\n"
                "The current downloader is NOT using browser "
                "cookies.\n\n"
                "If you are logged into YouTube and need "
                "authentication, set:\n\n"
                "USE_BROWSER_COOKIES = True\n\n"
                f"and use:\n"
                f'YOUTUBE_BROWSER = "{YOUTUBE_BROWSER}"'
            )

        raise


# ============================================================
# DOWNLOAD ROUTER
# ============================================================

def download_source(
    url,
    progress_callback,
    status_callback,
):

    source = detect_source(url)

    status_callback(
        f"Source detected: {source}"
    )

    if source == "YouTube":

        return download_youtube(
            url,
            progress_callback,
            status_callback,
        )

    return download_url(
        url,
        progress_callback,
        status_callback,
    )


# ============================================================
# GUI
# ============================================================

class DownloaderUI:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "MASTER-TOOLS - Video Downloader"
        )

        self.root.geometry(
            "820x430"
        )

        self.root.minsize(
            680,
            380,
        )

        self.url_var = (
            tk.StringVar()
        )

        self.source_var = (
            tk.StringVar(
                value="Source: —"
            )
        )

        self.status_var = (
            tk.StringVar(
                value="Paste a video URL."
            )
        )

        self.detail_var = (
            tk.StringVar(
                value=""
            )
        )

        self.progress_var = (
            tk.DoubleVar(
                value=0
            )
        )

        self.build_ui()

    # ========================================================
    # UI
    # ========================================================

    def build_ui(self):

        main = ttk.Frame(
            self.root,
            padding=24,
        )

        main.pack(
            fill="both",
            expand=True,
        )

        ttk.Label(
            main,
            text="MASTER-TOOLS",
            font=(
                "Segoe UI",
                22,
                "bold",
            ),
        ).pack(
            anchor="w"
        )

        ttk.Label(
            main,
            text="Video Downloader",
            font=(
                "Segoe UI",
                14,
            ),
        ).pack(
            anchor="w",
            pady=(2, 4),
        )

        ttk.Label(
            main,
            text=(
                "Paste a URL — the source "
                "is detected automatically."
            ),
        ).pack(
            anchor="w",
            pady=(0, 20),
        )

        # ----------------------------------------------------
        # URL
        # ----------------------------------------------------

        ttk.Label(
            main,
            text="Video URL",
        ).pack(
            anchor="w"
        )

        url_frame = ttk.Frame(
            main
        )

        url_frame.pack(
            fill="x",
            pady=(6, 8),
        )

        self.url_entry = ttk.Entry(
            url_frame,
            textvariable=self.url_var,
            font=(
                "Segoe UI",
                11,
            ),
        )

        self.url_entry.pack(
            side="left",
            fill="x",
            expand=True,
        )

        self.url_entry.bind(
            "<KeyRelease>",
            self.on_url_change,
        )

        self.download_button = (
            ttk.Button(
                url_frame,
                text="Download",
                command=self.start_download,
            )
        )

        self.download_button.pack(
            side="left",
            padx=(10, 0),
        )

        # ----------------------------------------------------
        # SOURCE
        # ----------------------------------------------------

        ttk.Label(
            main,
            textvariable=self.source_var,
            font=(
                "Segoe UI",
                10,
                "bold",
            ),
        ).pack(
            anchor="w",
            pady=(0, 14),
        )

        # ----------------------------------------------------
        # PROGRESS
        # ----------------------------------------------------

        self.progress = (
            ttk.Progressbar(
                main,
                variable=self.progress_var,
                maximum=100,
                mode="determinate",
            )
        )

        self.progress.pack(
            fill="x",
            pady=(5, 10),
        )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        ttk.Label(
            main,
            textvariable=self.status_var,
            font=(
                "Segoe UI",
                10,
                "bold",
            ),
        ).pack(
            anchor="w"
        )

        ttk.Label(
            main,
            textvariable=self.detail_var,
        ).pack(
            anchor="w",
            pady=(4, 16),
        )

        # ----------------------------------------------------
        # SETTINGS
        # ----------------------------------------------------

        settings_box = ttk.LabelFrame(
            main,
            text="YouTube",
        )

        settings_box.pack(
            fill="x",
            pady=(0, 12),
        )

        ttk.Label(
            settings_box,
            text="Browser cookies:",
        ).pack(
            side="left",
            padx=(12, 6),
            pady=8,
        )

        cookie_status = (
            "OFF"
            if not USE_BROWSER_COOKIES
            else YOUTUBE_BROWSER
        )

        ttk.Label(
            settings_box,
            text=cookie_status,
            font=(
                "Segoe UI",
                9,
                "bold",
            ),
        ).pack(
            side="left",
            pady=8,
        )

        if USE_BROWSER_COOKIES:

            cookie_message = (
                "  •  Browser login is used"
            )

        else:

            cookie_message = (
                "  •  Chrome cookies are NOT accessed"
            )

        ttk.Label(
            settings_box,
            text=cookie_message,
        ).pack(
            side="left",
            pady=8,
        )

        # ----------------------------------------------------
        # OUTPUT
        # ----------------------------------------------------

        output_box = ttk.LabelFrame(
            main,
            text="Output folder",
        )

        output_box.pack(
            fill="x"
        )

        ttk.Label(
            output_box,
            text=str(
                DOWNLOAD_DIR
            ),
        ).pack(
            anchor="w",
            padx=12,
            pady=10,
        )

        # ----------------------------------------------------
        # ENTER KEY
        # ----------------------------------------------------

        self.root.bind(
            "<Return>",
            lambda event:
                self.start_download()
        )

    # ========================================================
    # URL CHANGE
    # ========================================================

    def on_url_change(
        self,
        event=None,
    ):

        url = (
            self.url_var
            .get()
            .strip()
        )

        if not url:

            self.source_var.set(
                "Source: —"
            )

            return

        self.source_var.set(
            f"Source: "
            f"{detect_source(url)}"
        )

    # ========================================================
    # START DOWNLOAD
    # ========================================================

    def start_download(self):

        url = (
            self.url_var
            .get()
            .strip()
        )

        if not url:

            messagebox.showwarning(
                "Missing URL",
                "Please paste a URL first.",
            )

            return

        if not (
            url.startswith("http://")
            or url.startswith("https://")
        ):

            messagebox.showwarning(
                "Invalid URL",
                (
                    "Please enter a URL "
                    "starting with "
                    "http:// or https://"
                ),
            )

            return

        self.download_button.config(
            state="disabled"
        )

        self.url_entry.config(
            state="disabled"
        )

        self.progress_var.set(
            0
        )

        self.detail_var.set(
            ""
        )

        self.source_var.set(
            f"Source: "
            f"{detect_source(url)}"
        )

        self.status_var.set(
            "Starting..."
        )

        threading.Thread(
            target=self.download_worker,
            args=(url,),
            daemon=True,
        ).start()

    # ========================================================
    # WORKER
    # ========================================================

    def download_worker(
        self,
        url,
    ):

        try:

            output = download_source(
                url,
                self.update_progress,
                self.update_status,
            )

            self.root.after(
                0,
                self.download_finished,
                output,
            )

        except Exception as e:

            self.root.after(
                0,
                self.download_failed,
                str(e),
            )

    # ========================================================
    # STATUS
    # ========================================================

    def update_status(
        self,
        message,
    ):

        self.root.after(
            0,
            self.status_var.set,
            message,
        )

    # ========================================================
    # PROGRESS
    # ========================================================

    def update_progress(
        self,
        downloaded,
        total,
        detail="",
    ):

        percent = (
            min(
                downloaded / total * 100,
                100,
            )
            if total
            else 0
        )

        def update():

            self.progress_var.set(
                percent
            )

            if detail:

                self.detail_var.set(
                    f"{percent:.1f}%  •  "
                    f"{detail}"
                )

        self.root.after(
            0,
            update,
        )

    # ========================================================
    # COMPLETE
    # ========================================================

    def download_finished(
        self,
        output,
    ):

        self.progress_var.set(
            100
        )

        self.status_var.set(
            "DOWNLOAD COMPLETE"
        )

        self.detail_var.set(
            output
        )

        self.download_button.config(
            state="normal"
        )

        self.url_entry.config(
            state="normal"
        )

        messagebox.showinfo(
            "Download complete",
            f"Saved to:\n{output}",
        )

    # ========================================================
    # FAILED
    # ========================================================

    def download_failed(
        self,
        error,
    ):

        self.status_var.set(
            "DOWNLOAD FAILED"
        )

        self.detail_var.set(
            error
        )

        self.download_button.config(
            state="normal"
        )

        self.url_entry.config(
            state="normal"
        )

        messagebox.showerror(
            "Download failed",
            error,
        )


# ============================================================
# MAIN
# ============================================================

def main():

    root = tk.Tk()

    DownloaderUI(root)

    root.mainloop()


if __name__ == "__main__":
    main()