import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import ttk, messagebox
from urllib.parse import urlparse

import requests


# ============================================================
# DOWNLOAD SETTINGS
# ============================================================

PARTS = 8
MAX_RETRIES = 8
CHUNK_SIZE = 1024 * 1024
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 60

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


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


def get_file_size(url):
    headers = {"Range": "bytes=0-0", "User-Agent": "Mozilla/5.0"}
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

    raise RuntimeError("Server did not provide the file size.")


def create_parts(total_size, output):
    parts = []
    part_size = total_size // PARTS

    for i in range(PARTS):
        start = i * part_size
        end = total_size - 1 if i == PARTS - 1 else start + part_size - 1
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
    expected = part["end"] - part["start"] + 1

    if size > expected:
        try:
            os.remove(filename)
        except OSError:
            pass
        return 0

    return size


def download_part(url, part, progress_callback):
    start = part["start"]
    end = part["end"]
    filename = part["file"]

    existing = existing_part_size(part)

    if existing >= (end - start + 1):
        progress_callback(existing)
        return part["index"], True

    current_start = start + existing

    for attempt in range(1, MAX_RETRIES + 1):
        response = None
        try:
            headers = {
                "Range": f"bytes={current_start}-{end}",
                "User-Agent": "Mozilla/5.0",
            }

            response = requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=True,
            )

            if response.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {response.status_code}")

            mode = "ab" if existing > 0 else "wb"

            with open(filename, mode) as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue

                    f.write(chunk)
                    amount = len(chunk)
                    existing += amount
                    current_start += amount
                    progress_callback(amount)

            expected = end - start + 1
            if existing >= expected:
                return part["index"], True

            raise RuntimeError(f"Incomplete part {existing}/{expected}")

        except Exception as e:
            if attempt == MAX_RETRIES:
                break
            time.sleep(min(attempt * 2, 10))

            existing = existing_part_size(part)
            current_start = start + existing

        finally:
            if response is not None:
                response.close()

    return part["index"], False


def merge_parts(parts, output):
    temp_output = output + ".merging"

    with open(temp_output, "wb") as final_file:
        for part in parts:
            with open(part["file"], "rb") as source:
                while True:
                    data = source.read(CHUNK_SIZE)
                    if not data:
                        break
                    final_file.write(data)

    os.replace(temp_output, output)


def cleanup(parts):
    for part in parts:
        try:
            if os.path.exists(part["file"]):
                os.remove(part["file"])
        except OSError:
            pass


def guess_filename(url):
    path = urlparse(url).path
    name = os.path.basename(path)

    if name and "." in name:
        # Strip characters that are unsafe on Windows.
        name = "".join(c for c in name if c not in '<>:"/\\|?*')
        if name:
            return name

    return "video.mp4"


def download_url(url, progress_callback, status_callback):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    status_callback("Checking URL and file size...")
    total_size = get_file_size(url)

    filename = guess_filename(url)
    if not filename.lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".avi", ".ts", ".m4v")):
        filename += ".mp4"

    output = os.path.join(DOWNLOAD_DIR, filename)

    # Avoid overwriting an existing completed file.
    if os.path.exists(output):
        base, ext = os.path.splitext(filename)
        n = 1
        while os.path.exists(os.path.join(DOWNLOAD_DIR, f"{base}_{n}{ext}")):
            n += 1
        output = os.path.join(DOWNLOAD_DIR, f"{base}_{n}{ext}")

    parts = create_parts(total_size, output)

    downloaded_total = 0
    for part in parts:
        downloaded_total += existing_part_size(part)

    progress_callback(downloaded_total, total_size)

    status_callback(
        f"Downloading {format_bytes(total_size)} with {PARTS} parallel parts..."
    )

    progress_lock = threading.Lock()
    start_time = time.time()

    def on_progress(amount):
        nonlocal downloaded_total
        with progress_lock:
            downloaded_total += amount
            current = downloaded_total

        elapsed = max(time.time() - start_time, 0.001)
        speed = current / elapsed
        remaining = max(total_size - current, 0)
        eta = remaining / speed if speed > 0 else 0

        progress_callback(
            current,
            total_size,
            f"{format_bytes(current)} / {format_bytes(total_size)}  •  "
            f"{format_bytes(speed)}/s  •  ETA {format_time(eta)}",
        )

    failed = []

    with ThreadPoolExecutor(max_workers=PARTS) as executor:
        futures = [
            executor.submit(download_part, url, part, on_progress)
            for part in parts
        ]

        for future in as_completed(futures):
            index, success = future.result()
            if not success:
                failed.append(index)

    if failed:
        raise RuntimeError(
            "Download failed for part(s): " +
            ", ".join(str(i + 1) for i in failed)
        )

    status_callback("Merging downloaded parts...")
    merge_parts(parts, output)

    final_size = os.path.getsize(output)
    if final_size != total_size:
        raise RuntimeError(
            f"Final file size mismatch: expected {total_size}, got {final_size}"
        )

    cleanup(parts)

    elapsed = max(time.time() - start_time, 0.001)
    progress_callback(
        total_size,
        total_size,
        f"{format_bytes(total_size)} / {format_bytes(total_size)}  •  "
        f"Average {format_bytes(total_size / elapsed)}/s",
    )

    return output




def detect_source(url):
    host = urlparse(url).netloc.lower().split(':')[0]
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be', 'www.youtu.be'):
        return 'YouTube'
    if host in ('vimeo.com', 'www.vimeo.com'):
        return 'Vimeo'
    if host in ('facebook.com', 'www.facebook.com', 'm.facebook.com', 'fb.watch'):
        return 'Facebook'
    if host in ('tiktok.com', 'www.tiktok.com', 'vm.tiktok.com'):
        return 'TikTok'
    return 'Direct URL'


def download_youtube(url, progress_callback, status_callback):
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            'YouTube support requires yt-dlp. Install it with: pip install -U yt-dlp'
        )

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    last = {'downloaded': 0, 'total': 0, 'time': time.time()}

    def hook(d):
        status = d.get('status')
        if status == 'downloading':
            downloaded = d.get('downloaded_bytes', 0) or 0
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            speed = d.get('speed') or 0
            eta = d.get('eta') or 0
            last.update(downloaded=downloaded, total=total)
            detail = f"{format_bytes(downloaded)}"
            if total:
                detail += f" / {format_bytes(total)}"
            if speed:
                detail += f"  •  {format_bytes(speed)}/s"
            if eta:
                detail += f"  •  ETA {format_time(eta)}"
            progress_callback(downloaded, total or 1, detail)
            status_callback('Downloading YouTube video...')
        elif status == 'finished':
            progress_callback(1, 1, 'Download stream finished. Processing file...')
            status_callback('Processing downloaded video...')

    opts = {
        'format': 'bestvideo*+bestaudio/best',
        'format_sort': ['res:1080', 'fps', 'codec:avc:m4a', 'br'],
        'merge_output_format': 'mp4',
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(title)s.%(ext)s'),
        'progress_hooks': [hook],
        'noplaylist': True,
        'retries': 8,
        'fragment_retries': 8,
        'continuedl': True,
        'concurrent_fragment_downloads': 8,
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        if info.get('requested_downloads') and opts.get('merge_output_format') == 'mp4':
            filename = os.path.splitext(filename)[0] + '.mp4'
        return filename


def download_source(url, progress_callback, status_callback):
    source = detect_source(url)
    status_callback(f'Source detected: {source}')

    if source == 'YouTube':
        return download_youtube(url, progress_callback, status_callback)

    return download_url(url, progress_callback, status_callback)

# ============================================================
# GUI
# ============================================================

class DownloaderUI:
    def __init__(self, root):
        self.root = root
        self.root.title('Video Downloader')
        self.root.geometry('780x390')
        self.root.minsize(650, 350)

        self.url_var = tk.StringVar()
        self.source_var = tk.StringVar(value='Source: —')
        self.status_var = tk.StringVar(value='Paste a video URL.')
        self.detail_var = tk.StringVar(value='')
        self.progress_var = tk.DoubleVar(value=0)

        self.build_ui()

    def build_ui(self):
        main = ttk.Frame(self.root, padding=24)
        main.pack(fill='both', expand=True)

        ttk.Label(main, text='Video Downloader', font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(main, text='Paste a URL — the source is detected automatically.').pack(anchor='w', pady=(4, 20))

        ttk.Label(main, text='Video URL').pack(anchor='w')
        url_frame = ttk.Frame(main)
        url_frame.pack(fill='x', pady=(6, 8))

        self.url_entry = ttk.Entry(url_frame, textvariable=self.url_var, font=('Segoe UI', 11))
        self.url_entry.pack(side='left', fill='x', expand=True)
        self.url_entry.bind('<KeyRelease>', self.on_url_change)

        self.download_button = ttk.Button(url_frame, text='Download', command=self.start_download)
        self.download_button.pack(side='left', padx=(10, 0))

        ttk.Label(main, textvariable=self.source_var, font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(0, 14))

        self.progress = ttk.Progressbar(main, variable=self.progress_var, maximum=100, mode='determinate')
        self.progress.pack(fill='x', pady=(5, 10))

        ttk.Label(main, textvariable=self.status_var, font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        ttk.Label(main, textvariable=self.detail_var).pack(anchor='w', pady=(4, 16))

        output_box = ttk.LabelFrame(main, text='Output folder')
        output_box.pack(fill='x')
        ttk.Label(output_box, text=DOWNLOAD_DIR).pack(anchor='w', padx=12, pady=10)

        self.root.bind('<Return>', lambda event: self.start_download())

    def on_url_change(self, event=None):
        url = self.url_var.get().strip()
        if not url:
            self.source_var.set('Source: —')
            return
        self.source_var.set(f'Source: {detect_source(url)}')

    def start_download(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning('Missing URL', 'Please paste a URL first.')
            return
        if not (url.startswith('http://') or url.startswith('https://')):
            messagebox.showwarning('Invalid URL', 'Please enter a URL starting with http:// or https://')
            return

        self.download_button.config(state='disabled')
        self.url_entry.config(state='disabled')
        self.progress_var.set(0)
        self.detail_var.set('')
        self.source_var.set(f'Source: {detect_source(url)}')
        self.status_var.set('Starting...')

        threading.Thread(target=self.download_worker, args=(url,), daemon=True).start()

    def download_worker(self, url):
        try:
            output = download_source(url, self.update_progress, self.update_status)
            self.root.after(0, self.download_finished, output)
        except Exception as e:
            self.root.after(0, self.download_failed, str(e))

    def update_status(self, message):
        self.root.after(0, self.status_var.set, message)

    def update_progress(self, downloaded, total, detail=''):
        percent = min(downloaded / total * 100, 100) if total else 0
        def update():
            self.progress_var.set(percent)
            if detail:
                self.detail_var.set(f'{percent:.1f}%  •  {detail}')
        self.root.after(0, update)

    def download_finished(self, output):
        self.progress_var.set(100)
        self.status_var.set('DOWNLOAD COMPLETE')
        self.detail_var.set(output)
        self.download_button.config(state='normal')
        self.url_entry.config(state='normal')
        messagebox.showinfo('Download complete', f'Saved to:\n{output}')

    def download_failed(self, error):
        self.status_var.set('DOWNLOAD FAILED')
        self.detail_var.set(error)
        self.download_button.config(state='normal')
        self.url_entry.config(state='normal')
        messagebox.showerror('Download failed', error)


def main():
    root = tk.Tk()
    DownloaderUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
