#!/usr/bin/env python3
import argparse
import asyncio
import json
import re
import shutil
import sys
import termios
import tty
from pathlib import Path


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png")
STATUS_FILENAME = ".m4b_batch_status.json"
MIN_OUTPUT_DURATION_RATIO = 0.98


def natural_key(path: Path) -> list:
    parts = re.split(r"(\d+)", str(path).casefold())
    return [int(part) if part.isdigit() else part for part in parts]


def audio_sort_key(path: Path, root: Path) -> tuple:
    relative = path.relative_to(root)
    numbers = tuple(int(part) for part in re.findall(r"\d+", str(relative)))
    return numbers, natural_key(relative)


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return re.sub(r"<[^>]+>", "", text).strip() or None


def safe_filename(name: str) -> str:
    name = re.sub(r'[/:\\?%"<>|]', "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name or "Audiobook"


def infer_title_author(folder: Path) -> tuple[str, str | None]:
    name = folder.name.replace(" (Audiobook)", "").strip()
    match = re.match(r"(.+?)\s+by\s+(.+)", name, flags=re.IGNORECASE)
    if match:
        return clean_text(match.group(1)) or name, clean_text(match.group(2))
    return name or "Audiobook", None


def short_text(text: str, max_length: int = 88) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def load_metadata(folder: Path) -> dict:
    metadata_path = folder / "metadata.json"
    inferred_title, inferred_author = infer_title_author(folder)

    if not metadata_path.exists():
        tags = {
            "title": inferred_title,
            "album": inferred_title,
            "artist": inferred_author,
            "album_artist": inferred_author,
            "composer": inferred_author,
            "genre": "Audiobook",
        }
        return {key: value for key, value in tags.items() if value}

    with metadata_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    meta = raw.get("meta") or {}
    loan = raw.get("loan") or {}
    publisher = meta.get("publisher") or loan.get("publisherAccount") or {}
    detailed_series = loan.get("detailedSeries") or {}

    publish_date = meta.get("publish_date") or loan.get("publishDateText") or loan.get("publishDate")
    if publish_date and "T" in str(publish_date):
        publish_date = str(publish_date).split("T", 1)[0]

    subjects = loan.get("subjects") or []
    genres = [item.get("name") for item in subjects if isinstance(item, dict) and item.get("name")]

    title = clean_text(meta.get("title") or loan.get("title") or inferred_title)
    author = clean_text(meta.get("author") or loan.get("firstCreatorName") or inferred_author)
    narrator = clean_text(meta.get("narrator"))
    description = clean_text(meta.get("description"))
    series = clean_text(meta.get("series") or loan.get("series") or detailed_series.get("seriesName"))
    reading_order = clean_text(detailed_series.get("readingOrder"))

    tags = {
        "title": title,
        "album": title,
        "artist": author,
        "album_artist": author,
        "composer": author,
        "genre": "Audiobook",
        "date": publish_date,
        "publisher": clean_text(publisher.get("name") if isinstance(publisher, dict) else publisher),
        "description": description,
        "synopsis": description,
        "comment": f"Narrated by {narrator}" if narrator else None,
        "narrator": narrator,
        "series": series,
        "track": reading_order,
    }

    if genres:
        tags["grouping"] = ", ".join(genres)

    return {key: value for key, value in tags.items() if value}


def find_cover(folder: Path) -> Path | None:
    for name in COVER_NAMES:
        candidate = folder / name
        if candidate.exists():
            return candidate
    for candidate in sorted(folder.rglob("*"), key=lambda path: natural_key(path.relative_to(folder))):
        if candidate.is_file() and candidate.suffix.casefold() in {".jpg", ".jpeg", ".png"}:
            return candidate
    return None


def find_audio_files(folder: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in folder.rglob("*")
            if path.is_file()
            and path.suffix.casefold() in AUDIO_EXTENSIONS
            and not any(part.startswith(".") for part in path.relative_to(folder).parts)
        ],
        key=lambda path: audio_sort_key(path, folder),
    )


def audiobook_jobs(root: Path) -> tuple[list[dict], list[str], dict]:
    jobs = []
    skipped = []
    summary = {
        "total_folders": 0,
        "needs_conversion": 0,
        "already_converted": 0,
        "not_ready": 0,
    }

    audiobook_folders = [
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name != "__pycache__"
    ]
    summary["total_folders"] = len(audiobook_folders)

    for folder in sorted(audiobook_folders, key=natural_key):
        existing_m4b = sorted(
            [path for path in folder.glob("*.m4b") if not path.name.endswith(".partial.m4b")],
            key=natural_key,
        )
        if existing_m4b:
            summary["already_converted"] += 1
            skipped.append(f"{folder.name} - already has {existing_m4b[0].name}")
            continue

        metadata_path = folder / "metadata.json"
        if not metadata_path.exists():
            summary["not_ready"] += 1
            skipped.append(f"{folder.name} - missing metadata.json")
            continue

        audio_files = find_audio_files(folder)
        if not audio_files:
            summary["not_ready"] += 1
            skipped.append(f"{folder.name} - no audio files found")
            continue

        cover = find_cover(folder)
        if cover is None:
            summary["not_ready"] += 1
            skipped.append(f"{folder.name} - missing cover image")
            continue

        metadata = load_metadata(folder)
        output_name = safe_filename(metadata.get("title") or folder.name.replace(" (Audiobook)", "")) + ".m4b"
        jobs.append(
            {
                "folder": folder,
                "audio_files": audio_files,
                "cover": cover,
                "metadata": metadata,
                "output": folder / output_name,
                "label": f"{metadata.get('title') or output_name[:-4]} by {metadata.get('artist') or 'Unknown Author'}",
            }
        )

    summary["needs_conversion"] = len(jobs)
    return jobs, skipped, summary


async def media_duration_seconds(path: Path) -> float:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return 0.0

    try:
        return float(stdout.decode("utf-8", errors="replace").strip())
    except ValueError:
        return 0.0


async def job_audio_durations(job: dict) -> list[tuple[Path, float]]:
    durations = []
    for path in job["audio_files"]:
        durations.append((path, await media_duration_seconds(path)))
    return durations


async def media_probe(path: Path) -> dict:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return {}

    try:
        return json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}


async def validate_output(job: dict, output: Path, expected_duration: float) -> tuple[bool, str]:
    probe = await media_probe(output)
    output_duration = 0.0
    try:
        output_duration = float((probe.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        output_duration = 0.0

    if expected_duration > 0:
        required_duration = expected_duration * MIN_OUTPUT_DURATION_RATIO
        if output_duration < required_duration:
            return (
                False,
                f"output is too short ({output_duration:.1f}s created, {expected_duration:.1f}s expected)",
            )

    if job["cover"] is not None:
        streams = probe.get("streams") or []
        has_cover = any(
            stream.get("codec_type") == "video"
            and (stream.get("disposition") or {}).get("attached_pic") == 1
            for stream in streams
        )
        if not has_cover:
            return False, "cover image was not embedded"

    expected_title = job["metadata"].get("title")
    if expected_title:
        tags = (probe.get("format") or {}).get("tags") or {}
        found_title = tags.get("title") or tags.get("TITLE")
        if found_title != expected_title:
            return False, "title metadata was not embedded"

    return True, ""


def write_status_file(root: Path, jobs: list[dict], completed: int, failed: int, active: dict) -> None:
    status = {
        "completed_count": completed,
        "failed_count": failed,
        "remaining_count": len(jobs) - completed,
        "active": active,
        "done": [str(job["output"]) for job in jobs if job["output"].exists()],
    }
    (root / STATUS_FILENAME).write_text(json.dumps(status, indent=2), encoding="utf-8")


def maybe_write_status_file(root: Path, jobs: list[dict], completed: int, failed: int, active: dict, args) -> None:
    if not args.dry_run:
        write_status_file(root, jobs, completed, failed, active)


def build_command(job: dict, partial_output: Path, audio_bitrate: str) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-y",
    ]

    for path in job["audio_files"]:
        command.extend(["-i", str(path)])

    cover_index = None
    if job["cover"] is not None:
        cover_index = len(job["audio_files"])
        command.extend(["-i", str(job["cover"])])

    audio_inputs = "".join(f"[{index}:a:0]" for index in range(len(job["audio_files"])))
    command.extend(
        [
            "-filter_complex",
            f"{audio_inputs}concat=n={len(job['audio_files'])}:v=0:a=1[aout]",
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
        ]
    )

    if cover_index is not None:
        cover_codec = "copy" if job["cover"].suffix.casefold() in {".jpg", ".jpeg"} else "mjpeg"
        command.extend(
            [
                "-map",
                f"{cover_index}:v:0",
                "-c:v",
                cover_codec,
                "-disposition:v:0",
                "attached_pic",
            ]
        )

    command.extend(
        [
            "-movflags",
            "+faststart",
        ]
    )

    for key, value in job["metadata"].items():
        command.extend(["-metadata", f"{key}={value}"])

    command.extend(
        [
            "-f",
            "mp4",
            "-progress",
            "pipe:1",
            str(partial_output),
        ]
    )
    if cover_index is not None:
        command[-5:-5] = [
            "-metadata:s:v",
            "title=Cover",
            "-metadata:s:v",
            "comment=Cover (front)",
        ]
    return command


async def read_stderr(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    chunks = []
    while True:
        chunk = await process.stderr.read(8192)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace").strip()


async def run_ffmpeg(job: dict, args: argparse.Namespace, progress_queue: asyncio.Queue) -> tuple[bool, str]:
    folder = job["folder"]
    output = job["output"]
    partial_output = output.with_name(f"{output.stem}.partial{output.suffix}")

    if args.dry_run:
        return True, f"DRY RUN: would create {output.name}"

    if partial_output.exists():
        partial_output.unlink()

    command = build_command(job, partial_output, args.audio_bitrate)

    audio_durations = await job_audio_durations(job)
    unreadable = [path.name for path, duration in audio_durations if duration <= 0]
    if unreadable:
        await progress_queue.put((folder.name, "failed", 0.0, f"FAILED: {folder.name}"))
        return False, f"FAILED: {folder.name}\nCould not read duration for: {', '.join(unreadable)}"

    total_duration = sum(duration for _, duration in audio_durations)
    await progress_queue.put((folder.name, "started", 0.0, ""))
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_task = asyncio.create_task(read_stderr(process))

    try:
        if process.stdout is not None:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if text.startswith("out_time_ms=") and total_duration > 0:
                    try:
                        out_time_seconds = int(text.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    percent = min(99.9, (out_time_seconds / total_duration) * 100)
                    await progress_queue.put((folder.name, "progress", percent, ""))
                elif text == "progress=end":
                    await progress_queue.put((folder.name, "progress", 100.0, ""))

        await process.wait()
        stderr = await stderr_task
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        stderr_task.cancel()
        if partial_output.exists():
            partial_output.unlink()
        await progress_queue.put((folder.name, "cancelled", 0.0, f"CANCELLED: {folder.name}"))
        raise

    if process.returncode != 0:
        if partial_output.exists():
            partial_output.unlink()
        message = stderr
        await progress_queue.put((folder.name, "failed", 0.0, f"FAILED: {folder.name}"))
        return False, f"FAILED: {folder.name}\n{message}"

    valid, validation_message = await validate_output(job, partial_output, total_duration)
    if not valid:
        if partial_output.exists():
            partial_output.unlink()
        await progress_queue.put((folder.name, "failed", 0.0, f"FAILED: {folder.name}"))
        return False, f"FAILED: {folder.name}\n{validation_message}"

    partial_output.replace(output)
    await progress_queue.put((folder.name, "done", 100.0, f"DONE: {folder.name} -> {output.name}"))
    return True, f"DONE: {folder.name} -> {output.name}"


def clear_dashboard(line_count: int) -> None:
    if line_count <= 0:
        return
    sys.stdout.write(f"\033[{line_count}F")
    for _ in range(line_count):
        sys.stdout.write("\033[2K\n")
    sys.stdout.write(f"\033[{line_count}F")
    sys.stdout.flush()


def render_dashboard(
    progress: dict,
    states: dict,
    labels: dict,
    completed: int,
    failed: int,
    total: int,
) -> int:
    overall_done = completed + sum(1 for state in states.values() if state in {"done", "failed"})
    overall_percent = (overall_done / total) * 100 if total else 100
    lines = [
        f"Progress: {overall_done}/{total} finished ({overall_percent:.1f}%)",
        "-" * 72,
    ]

    for name in progress:
        state = states[name]
        state_text = "" if state in {"waiting", "started", "progress"} else f" {state}"
        lines.append(f"{short_text(labels[name])}: {progress[name]:5.1f}%{state_text}")

    for line in lines:
        print(line)
    sys.stdout.flush()
    return len(lines)


async def show_live_progress(
    progress_queue: asyncio.Queue,
    active_jobs: list[dict],
    root: Path,
    jobs: list[dict],
    completed: int,
    failed: int,
    args: argparse.Namespace,
) -> None:
    active_names = [job["folder"].name for job in active_jobs]
    labels = {job["folder"].name: job["label"] for job in active_jobs}
    progress = {name: 0.0 for name in active_names}
    states = {name: "waiting" for name in active_names}
    dashboard_lines = 0

    if not sys.stdout.isatty():
        while progress:
            name, state, percent, _ = await progress_queue.get()
            if name in progress:
                states[name] = state
                progress[name] = percent
                if state in {"done", "failed", "cancelled"}:
                    del progress[name]
                    del states[name]
        return

    dashboard_lines = render_dashboard(progress, states, labels, completed, failed, len(jobs))

    while progress:
        try:
            name, state, percent, message = await asyncio.wait_for(progress_queue.get(), timeout=2)
            if name in progress:
                states[name] = state
                progress[name] = percent
                if state in {"done", "failed", "cancelled"}:
                    clear_dashboard(dashboard_lines)
                    print(message or f"{state.upper()}: {name}", flush=True)
                    if state in {"done", "failed"}:
                        completed += 1
                    if state == "failed":
                        failed += 1
                    del progress[name]
                    del states[name]
                    dashboard_lines = 0
        except asyncio.TimeoutError:
            pass

        if progress:
            clear_dashboard(dashboard_lines)
            dashboard_lines = render_dashboard(progress, states, labels, completed, failed, len(jobs))
            active = {
                name: {
                    "percent": round(progress[name], 1),
                    "state": states[name],
                    "label": labels[name],
                }
                for name in progress
            }
            maybe_write_status_file(root, jobs, completed, failed, active, args)

    clear_dashboard(dashboard_lines)


async def process_jobs(jobs: list[dict], args: argparse.Namespace) -> int:
    total = len(jobs)
    completed = 0
    failed = 0
    root = Path(args.root).expanduser().resolve()
    next_job_index = 0
    tasks = {}
    progress_queue = asyncio.Queue()
    progress_waiter = None
    dashboard_lines = 0
    labels = {}
    progress = {}
    states = {}

    def active_status() -> dict:
        return {
            name: {
                "percent": round(progress[name], 1),
                "state": states[name],
                "label": labels[name],
            }
            for name in progress
        }

    def start_next_job() -> bool:
        nonlocal next_job_index
        if next_job_index >= total:
            return False

        job = jobs[next_job_index]
        next_job_index += 1
        name = job["folder"].name
        labels[name] = job["label"]
        progress[name] = 0.0
        states[name] = "waiting"
        if args.dry_run or not sys.stdout.isatty():
            print(f"\nNow processing:\n  - {job['label']}", flush=True)
        task = asyncio.create_task(run_ffmpeg(job, args, progress_queue))
        tasks[task] = job
        return True

    for _ in range(min(args.parallel, total)):
        start_next_job()

    maybe_write_status_file(root, jobs, completed, failed, active_status(), args)

    try:
        while tasks:
            if not args.dry_run and sys.stdout.isatty() and dashboard_lines == 0 and progress:
                dashboard_lines = render_dashboard(progress, states, labels, completed, failed, total)

            if progress_waiter is None:
                progress_waiter = asyncio.create_task(progress_queue.get())

            done, _ = await asyncio.wait(
                [*tasks.keys(), progress_waiter],
                return_when=asyncio.FIRST_COMPLETED,
                timeout=2,
            )

            if not done:
                if not args.dry_run and sys.stdout.isatty() and progress:
                    clear_dashboard(dashboard_lines)
                    dashboard_lines = render_dashboard(progress, states, labels, completed, failed, total)
                    maybe_write_status_file(root, jobs, completed, failed, active_status(), args)
                continue

            if progress_waiter in done:
                name, state, percent, _ = progress_waiter.result()
                progress_waiter = None
                if name in progress:
                    states[name] = state
                    progress[name] = percent

            finished_tasks = [task for task in done if task in tasks]
            for task in finished_tasks:
                job = tasks.pop(task)
                name = job["folder"].name
                ok, message = task.result()
                completed += 1
                if not ok:
                    failed += 1

                if name in progress:
                    del progress[name]
                    del states[name]
                    del labels[name]

                if not args.dry_run and sys.stdout.isatty():
                    clear_dashboard(dashboard_lines)
                    dashboard_lines = 0
                    print(message, flush=True)
                else:
                    percent = (completed / total) * 100 if total else 100
                    print(message)
                    print(f"Progress: {completed}/{total} finished ({percent:.1f}%)", flush=True)

                start_next_job()
                maybe_write_status_file(root, jobs, completed, failed, active_status(), args)

            if not args.dry_run and sys.stdout.isatty() and progress:
                clear_dashboard(dashboard_lines)
                dashboard_lines = render_dashboard(progress, states, labels, completed, failed, total)
                maybe_write_status_file(root, jobs, completed, failed, active_status(), args)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        if progress_waiter is not None:
            progress_waiter.cancel()
        await asyncio.gather(*tasks.keys(), return_exceptions=True)
        maybe_write_status_file(root, jobs, completed, failed, {}, args)
        raise
    finally:
        if progress_waiter is not None:
            progress_waiter.cancel()
            await asyncio.gather(progress_waiter, return_exceptions=True)
        if dashboard_lines:
            clear_dashboard(dashboard_lines)

    if failed:
        print(f"\nFinished with {failed} failed audiobook(s).")
        return 1

    print("\nAll eligible audiobooks are done.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create m4b files for audiobook folders that do not already have one."
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Audiobook archive folder. Defaults to the current folder.",
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=3,
        help="How many audiobooks to process at once. Defaults to 3.",
    )
    parser.add_argument(
        "--audio-bitrate",
        default="128k",
        help="AAC audio bitrate for the m4b files. Defaults to 128k.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without creating files.",
    )
    return parser.parse_args()


def print_scan_summary(summary: dict, skipped: list[str]) -> None:
    print()
    print(f"Found {summary['total_folders']} audiobook folder(s) total.")
    print(f"{summary['needs_conversion']} need m4b conversion.")
    print(f"{summary['already_converted']} already have m4b files.")
    if summary["not_ready"]:
        print(f"{summary['not_ready']} are missing something needed for conversion.")
    print()

    if skipped:
        print(f"Skipped {len(skipped)} folder(s):")
        for item in skipped:
            print(f"  - {item}")
        print()


def read_menu_choice() -> str:
    if not sys.stdin.isatty():
        return input("> ").strip()

    print("> ", end="", flush=True)
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    typed = []
    try:
        tty.setcbreak(fd)
        while True:
            char = sys.stdin.read(1)
            if char == "\x03":
                raise KeyboardInterrupt
            if char in {"\n", "\r"}:
                print()
                return "".join(typed).strip()
            if char in {"\x7f", "\b"}:
                if typed:
                    typed.pop()
                    print("\b \b", end="", flush=True)
                continue
            if char.isprintable():
                typed.append(char)
                print(char, end="", flush=True)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def confirm_start(summary: dict, skipped: list[str]) -> bool:
    print()
    print("Welcome to Terminal Bulk Audiobook m4b Tool by mp3li")
    print()
    print(
        "This tool converts all audiobooks inside a chosen folder into m4b files. "
        "Each audiobook must be in its own folder, named Title by Author, and each "
        "part or chapter must be numbered in a clear order of some sort. Cover images "
        "named cover.jpg and a metadata.json file located in audiobook folders will be "
        "auto applied as metadata."
    )
    print()
    print(
        "This tool skips any folders that currently have m4b files, and processes 3 "
        "conversions at a time. Completed .m4b files stay finished and will be skipped "
        "next time."
    )
    print()
    print("Any audiobook that was mid-conversion will restart on the next run.")
    print_scan_summary(summary, skipped)
    print()
    print("Do you wish to start converting?")
    print()
    print("1: Yes, start")
    print("2: No, quit")
    print()

    while True:
        choice = read_menu_choice()
        if choice == "1":
            print()
            return True
        if choice == "2":
            print()
            print("Quit. No audiobooks were converted.")
            return False
        print("Please enter 1 to start or 2 to quit.")


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()

    if args.parallel < 1:
        print("--parallel must be at least 1", file=sys.stderr)
        return 2

    if not root.exists() or not root.is_dir():
        print(f"Root folder not found: {root}", file=sys.stderr)
        return 2

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe must both be available on PATH.", file=sys.stderr)
        return 2

    jobs, skipped, summary = audiobook_jobs(root)
    if not jobs:
        print_scan_summary(summary, skipped)
        print("Nothing to process.")
        return 0

    if not confirm_start(summary, skipped):
        return 0

    try:
        return asyncio.run(process_jobs(jobs, args))
    except KeyboardInterrupt:
        print("\nCancelled. Completed .m4b files stay finished and will be skipped next time.")
        print("Any audiobook that was mid-conversion will restart on the next run.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
