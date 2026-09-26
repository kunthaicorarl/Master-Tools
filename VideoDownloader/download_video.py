
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# CONFIGURATION
# ============================================================

URL = r"""https://rr8---sn-v2xooxuj5hcvqp5-2ois.googlevideo.com/videoplayback?expire=1790448480&ei=AL-3aoGPMd2y8uMP3siKoQs&ip=2a06%3Ac701%3Ac004%3Aba00%3A9578%3A6dc2%3A3068%3Ada13&id=o-ACqW08bqVtpOIgU3uWWjsSpDYrLScbRjdAzeQ8i0kXlm&itag=396&source=youtube&requiressl=yes&xpc=EgVo2aDSNQ%3D%3D&cps=388&bui=AWzQHwrdVzq02V4mEpsRnVK_Egyl6jvWRfjI8IjmcNj-l9dct7RD5mzNrpSQTT0kqOdVJTZF3G46NwWA&spc=I-rgIbMJYFW3sovWsAGZJ9cjkHn-bpQsxKnsw7JzWX1xiRdbFuemurqNbA&vprv=1&svpuc=1&mime=video%2Fmp4&rqh=1&gir=yes&clen=338035735&dur=10344.366&lmt=1782518972942335&keepalive=yes&fexp=51565115,52178456&c=VISIONOS&txp=5532534&sparams=expire%2Cei%2Cip%2Cid%2Citag%2Csource%2Crequiressl%2Cxpc%2Cbui%2Cspc%2Cvprv%2Csvpuc%2Cmime%2Crqh%2Cgir%2Cclen%2Cdur%2Clmt&sig=AE0s2JYwRAIgRSW6QI8J7_96BkRqzVz4mG1g-r2F6P1lYFnfqY5wZ3cCIBpjKWGU3CPP2Ox9Lf0N6vekBNraIFYSoJCSpa9ZyXOw&rm=sn-pujapa-ua8l7e,sn-ua8d7s&rrc=79,104&req_id=71897475a498a3ee&cmsv=e&rms=rdu,au&redirect_counter=2&cms_redirect=yes&ipbypass=yes&met=1790427203,&mh=aV&mip=58.97.225.150&mm=29&mn=sn-v2xooxuj5hcvqp5-2ois&ms=rdu&mt=1790426715&mv=m&mvi=8&pl=24&lsparams=cps,ipbypass,met,mh,mip,mm,mn,ms,mv,mvi,pl,rms&lsig=APaTxxMwRAIgHphNfjIraVPO-Y-ueHKxOTaUGc1mhT37VnvChYQDWPACICsB3gM2PU5kPM1nBn1U_LFHZ0oFWayQAibMDpbCBWzK"""

OUTPUT = "video.mp4"

PARTS = 8

# Number of automatic retries for each part
MAX_RETRIES = 8

# Download chunk size per request
CHUNK_SIZE = 1024 * 1024  # 1 MB

# Timeout
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 60


# ============================================================
# GLOBAL PROGRESS
# ============================================================

progress_lock = threading.Lock()

downloaded_total = 0
start_time = 0


# ============================================================
# FORMAT
# ============================================================

def format_bytes(value):
    units = ["B", "KB", "MB", "GB"]

    for unit in units:
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024

    return f"{value:.1f} TB"


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


# ============================================================
# GET FILE SIZE
# ============================================================

def get_file_size():
    print("Checking remote file...")

    headers = {
        "Range": "bytes=0-0",
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(
        URL,
        headers=headers,
        stream=True,
        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT)
    )

    response.raise_for_status()

    content_range = response.headers.get("Content-Range")

    if content_range:
        # Example:
        # bytes 0-0/338035735

        total = int(content_range.split("/")[-1])

        response.close()

        return total

    content_length = response.headers.get("Content-Length")

    response.close()

    if content_length:
        return int(content_length)

    raise RuntimeError(
        "Server did not provide file size."
    )


# ============================================================
# CREATE PART INFORMATION
# ============================================================

def create_parts(total_size):

    parts = []

    part_size = total_size // PARTS

    for i in range(PARTS):

        start = i * part_size

        if i == PARTS - 1:
            end = total_size - 1
        else:
            end = start + part_size - 1

        part_file = f"{OUTPUT}.part{i}"

        parts.append({
            "index": i,
            "start": start,
            "end": end,
            "file": part_file
        })

    return parts


# ============================================================
# CHECK EXISTING PART
# ============================================================

def existing_part_size(part):

    filename = part["file"]

    if not os.path.exists(filename):
        return 0

    size = os.path.getsize(filename)

    expected = part["end"] - part["start"] + 1

    if size > expected:
        print(
            f"Invalid part {part['index']}. "
            f"Restarting."
        )

        os.remove(filename)

        return 0

    return size


# ============================================================
# DOWNLOAD ONE PART
# ============================================================

def download_part(part):

    global downloaded_total

    index = part["index"]

    start = part["start"]
    end = part["end"]

    filename = part["file"]

    existing = existing_part_size(part)

    if existing >= (end - start + 1):

        with progress_lock:
            downloaded_total += existing

        return index, True

    current_start = start + existing

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            headers = {
                "Range":
                    f"bytes={current_start}-{end}",

                "User-Agent":
                    "Mozilla/5.0"
            }

            response = requests.get(
                URL,
                headers=headers,
                stream=True,
                timeout=(
                    CONNECT_TIMEOUT,
                    READ_TIMEOUT
                )
            )

            if response.status_code not in (200, 206):
                raise RuntimeError(
                    f"HTTP {response.status_code}"
                )

            mode = "ab" if existing > 0 else "wb"

            with open(filename, mode) as f:

                for chunk in response.iter_content(
                    chunk_size=CHUNK_SIZE
                ):

                    if not chunk:
                        continue

                    f.write(chunk)

                    amount = len(chunk)

                    with progress_lock:
                        downloaded_total += amount

                    current_start += amount
                    existing += amount

            response.close()

            expected = end - start + 1

            if existing >= expected:

                return index, True

            raise RuntimeError(
                f"Incomplete part "
                f"{existing}/{expected}"
            )

        except Exception as e:

            print(
                f"\nPart {index + 1}: "
                f"retry {attempt}/{MAX_RETRIES} "
                f"- {e}"
            )

            time.sleep(min(attempt * 2, 10))

            # Re-check actual file size.
            existing = existing_part_size(part)

            current_start = start + existing

    return index, False


# ============================================================
# PROGRESS DISPLAY
# ============================================================

def progress_display(total_size):

    global downloaded_total

    while True:

        with progress_lock:
            downloaded = downloaded_total

        elapsed = time.time() - start_time

        if elapsed <= 0:
            elapsed = 0.001

        speed = downloaded / elapsed

        percent = (
            downloaded / total_size * 100
        )

        remaining = total_size - downloaded

        eta = (
            remaining / speed
            if speed > 0
            else 0
        )

        bar_length = 30

        filled = int(
            bar_length * downloaded / total_size
        )

        bar = (
            "=" * filled +
            ">" +
            " " * max(
                0,
                bar_length - filled - 1
            )
        )

        print(
            f"\r[{bar}] "
            f"{percent:6.2f}% | "
            f"{format_bytes(downloaded)} / "
            f"{format_bytes(total_size)} | "
            f"{format_bytes(speed)}/s | "
            f"ETA {format_time(eta)}",
            end="",
            flush=True
        )

        if downloaded >= total_size:
            break

        time.sleep(0.5)

    print()


# ============================================================
# MERGE PARTS
# ============================================================

def merge_parts(parts):

    print("\nMerging parts...")

    temp_output = OUTPUT + ".merging"

    with open(temp_output, "wb") as output:

        for part in parts:

            filename = part["file"]

            print(
                f"  Adding part "
                f"{part['index'] + 1}/{len(parts)}"
            )

            with open(filename, "rb") as source:

                while True:

                    data = source.read(CHUNK_SIZE)

                    if not data:
                        break

                    output.write(data)

    os.replace(temp_output, OUTPUT)


# ============================================================
# CLEANUP
# ============================================================

def cleanup(parts):

    print("\nCleaning temporary parts...")

    for part in parts:

        filename = part["file"]

        try:
            if os.path.exists(filename):
                os.remove(filename)
        except Exception:
            pass


# ============================================================
# MAIN
# ============================================================

def main():

    global start_time
    global downloaded_total

    print("=" * 70)
    print("        8-PART PARALLEL VIDEO DOWNLOADER")
    print("=" * 70)
    print()

    try:

        total_size = get_file_size()

        print(
            f"File size: {format_bytes(total_size)}"
        )

        print(
            f"Parallel parts: {PARTS}"
        )

        print()

        parts = create_parts(total_size)

        # Calculate existing downloaded bytes.
        downloaded_total = 0

        for part in parts:
            downloaded_total += existing_part_size(part)

        if downloaded_total > 0:

            print(
                f"Resuming: "
                f"{format_bytes(downloaded_total)} "
                f"already downloaded"
            )

        print()
        print("Starting download...")
        print()

        start_time = time.time()

        # Progress thread
        progress_thread = threading.Thread(
            target=progress_display,
            args=(total_size,),
            daemon=True
        )

        progress_thread.start()

        failed = []

        with ThreadPoolExecutor(
            max_workers=PARTS
        ) as executor:

            futures = [
                executor.submit(
                    download_part,
                    part
                )
                for part in parts
            ]

            for future in as_completed(futures):

                index, success = future.result()

                if not success:
                    failed.append(index)

        progress_thread.join()

        if failed:

            print()
            print(
                "Some parts failed:"
            )

            for index in failed:
                print(
                    f"  Part {index + 1}"
                )

            print(
                "\nRun the BAT file again "
                "to resume failed parts."
            )

            return 1

        # Verify parts
        for part in parts:

            expected = (
                part["end"] -
                part["start"] +
                1
            )

            actual = os.path.getsize(
                part["file"]
            )

            if actual != expected:

                print(
                    f"Part {part['index'] + 1} "
                    f"is incomplete."
                )

                return 1

        merge_parts(parts)

        # Verify final size.
        final_size = os.path.getsize(
            OUTPUT
        )

        if final_size != total_size:

            print(
                f"\nERROR: Final file size mismatch."
            )

            print(
                f"Expected: "
                f"{total_size}"
            )

            print(
                f"Actual: "
                f"{final_size}"
            )

            return 1

        cleanup(parts)

        elapsed = time.time() - start_time

        print()
        print("=" * 70)
        print("DOWNLOAD COMPLETE")
        print("=" * 70)

        print(
            f"File : {OUTPUT}"
        )

        print(
            f"Size : {format_bytes(final_size)}"
        )

        print(
            f"Time : {format_time(elapsed)}"
        )

        if elapsed > 0:

            print(
                f"Avg  : "
                f"{format_bytes(final_size / elapsed)}/s"
            )

        print("=" * 70)

        return 0

    except KeyboardInterrupt:

        print(
            "\n\nDownload interrupted."
        )

        print(
            "Run the BAT file again "
            "to resume."
        )

        return 1

    except Exception as e:

        print(
            f"\nERROR: {e}"
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
