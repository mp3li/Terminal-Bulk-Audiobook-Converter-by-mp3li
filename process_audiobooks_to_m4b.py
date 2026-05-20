#!/usr/bin/env python3
import argparse
import asyncio
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
COVER_NAMES = ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png")
STATUS_FILENAME = ".m4b_batch_status.json"


def natural_key(path: Path) -> list:
    parts = re.split(r"(\d+)", path.name.casefold())
    return [int(part) if part.isdigit() else part for part in parts]


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


def short_text(text: str, max_length: int = 88) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def load_metadata(folder: Path) -> dict:
    metadata_path = folder / "metadata.json"
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

    title = clean_text(meta.get("title") or loan.get("title") or folder.name.replace(" (Audiobook)", ""))
    author = clean_text(meta.get("author") or loan.get("firstCreatorName"))
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
    for candidate in sorted(folder.iterdir(), key=natural_key):
        if candidate.suffix.casefold() in {".jpg", ".jpeg", ".png"}:
            return candidate
    return None


def audiobook_jobs(root: Path) -> tuple[list[dict], list[str]]:
    jobs = []
    skipped = []

    audiobook_folders = [
        path
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".") and path.name != "__pycache__"
    ]

    for folder in sorted(audiobook_folders, key=natural_key):
        existing_m4b = sorted(
            [path for path in folder.glob("*.m4b") if not path.name.endswith(".partial.m4b")],
            key=natural_key,
        )
        if existing_m4b:
            skipped.append(f"{folder.name} - already has {existing_m4b[0].name}")
            continue

        metadata_path = folder / "metadata.json"
        if not metadata_path.exists():
            skipped.append(f"{folder.name} - missing metadata.json")
            continue

        audio_files = sorted(
            [path for path in folder.iterdir() if path.is_file() and path.suffix.casefold() in AUDIO_EXTENSIONS],
            key=natural_key,
        )
        if not audio_files:
            skipped.append(f"{folder.name} - no audio files found")
            continue

        cover = find_cover(folder)
        if cover is None:
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

    return jobs, skipped


def ffmpeg_escape(path: Path) -> str:
    return str(path).replace("'", "'\\''")


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


async def job_duration_seconds(job: dict) -> float:
    total = 0.0
    for path in job["audio_files"]:
        duration = await media_duration_seconds(path)
        if duration > 0:
            total += duration
    return total


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


def build_command(job: dict, concat_path: Path, partial_output: Path, audio_bitrate: str) -> list[str]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(job["cover"]),
        "-map",
        "0:a",
        "-map",
        "1:v",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-c:v",
        "copy",
        "-disposition:v:0",
        "attached_pic",
        "-movflags",
        "+faststart+use_metadata_tags",
    ]

    for key, value in job["metadata"].items():
        command.extend(["-metadata", f"{key}={value}"])

    command.extend(
        [
            "-metadata:s:v",
            "title=Cover",
            "-metadata:s:v",
            "comment=Cover (front)",
            "-f",
            "mp4",
            "-progress",
            "pipe:1",
            str(partial_output),
        ]
    )
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

    if partial_output.exists():
        partial_output.unlink()

    with tempfile.TemporaryDirectory(prefix="m4b_", dir=folder) as tmp_dir:
        concat_path = Path(tmp_dir) / "inputs.txt"
        concat_path.write_text(
            "".join(f"file '{ffmpeg_escape(path.resolve())}'\n" for path in job["audio_files"]),
            encoding="utf-8",
        )

        command = build_command(job, concat_path, partial_output, args.audio_bitrate)
        if args.dry_run:
            return True, f"DRY RUN: would create {output.name}"

        total_duration = await job_duration_seconds(job)
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

    for batch_start in range(0, total, args.parallel):
        batch = jobs[batch_start : batch_start + args.parallel]
        if args.dry_run:
            print("\nNow processing:")
            for job in batch:
                print(f"  - {job['label']}")
            print(flush=True)

        active = {
            job["folder"].name: {
                "percent": 0.0,
                "output": str(job["output"]),
                "label": job["label"],
            }
            for job in batch
        }
        maybe_write_status_file(root, jobs, completed, failed, active, args)

        progress_queue = asyncio.Queue()
        tasks = [asyncio.create_task(run_ffmpeg(job, args, progress_queue)) for job in batch]
        progress_task = None
        if not args.dry_run:
            progress_task = asyncio.create_task(
                show_live_progress(
                    progress_queue,
                    batch,
                    root,
                    jobs,
                    completed,
                    failed,
                    args,
                )
            )

        try:
            results = await asyncio.gather(*tasks)
            if progress_task is not None:
                await progress_task
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if progress_task is not None:
                progress_task.cancel()
                await asyncio.gather(progress_task, return_exceptions=True)
            maybe_write_status_file(root, jobs, completed, failed, {}, args)
            raise

        for ok, message in results:
            completed += 1
            if not ok:
                failed += 1
            percent = (completed / total) * 100 if total else 100
            if args.dry_run or not sys.stdout.isatty():
                print(message)
                print(f"Progress: {completed}/{total} finished ({percent:.1f}%)", flush=True)
            maybe_write_status_file(root, jobs, completed, failed, {}, args)

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


def confirm_start() -> bool:
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
    print()
    print("Do you wish to start converting?")
    print()
    print("1: Yes, start")
    print("2: No, quit")
    print()

    while True:
        choice = input("> ").strip()
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

    if not confirm_start():
        return 0

    jobs, skipped = audiobook_jobs(root)

    print(f"Found {len(jobs)} audiobook folder(s) needing m4b files.")
    if skipped:
        print(f"Skipped {len(skipped)} folder(s):")
        for item in skipped:
            print(f"  - {item}")

    if not jobs:
        print("Nothing to process.")
        return 0

    try:
        return asyncio.run(process_jobs(jobs, args))
    except KeyboardInterrupt:
        print("\nCancelled. Completed .m4b files stay finished and will be skipped next time.")
        print("Any audiobook that was mid-conversion will restart on the next run.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
