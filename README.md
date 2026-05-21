![Version](https://img.shields.io/badge/Version-v1-8A2BE2?labelColor=2E2E2E)
![Development](https://img.shields.io/badge/Development-Active-8A2BE2?labelColor=2E2E2E)
![Runs](https://img.shields.io/badge/Runs-Locally%20%26%20Offline%20Only-8A2BE2?labelColor=2E2E2E)
![Type](https://img.shields.io/badge/Type-Terminal%20Tool-8A2BE2?labelColor=2E2E2E)
![Built-In](https://img.shields.io/badge/Built--In-Bulk%20Processing-8A2BE2?labelColor=2E2E2E)
![Built-In](https://img.shields.io/badge/Built--In-Live%20Progress%20Dashboard-8A2BE2?labelColor=2E2E2E)
![Output](https://img.shields.io/badge/Output-m4b%20Audiobooks-8A2BE2?labelColor=2E2E2E)
![Metadata](https://img.shields.io/badge/Metadata-Cover%20%2B%20JSON%20Applied-8A2BE2?labelColor=2E2E2E)

# Terminal Bulk Audiobook Converter v1 by mp3li

Terminal Bulk Audiobook Converter is a local terminal tool that converts folder-based audiobook collections into `.m4b` audiobook files.

- Runs **locally** on your computer.
- Does **not** use Wi-Fi.
- Processes **3 audiobooks at a time** by default.
- Skips audiobooks that already have `.m4b` files.
- Uses `metadata.json` and `cover.jpg` when available in each audiobook folder.
- Shows a live terminal progress dashboard while converting.

### What This Tool Does:

- Converts each audiobook folder into one `.m4b` file.
- Keeps the original audio parts, cover image, and metadata file untouched.
- Embeds cover art into the `.m4b`.
- Applies metadata from `metadata.json`.
- Verifies the finished `.m4b` before accepting it as done.
- Sorts numbered audiobook parts in natural order, so filenames like `Part 1`, `Part 02`, and `Part 003` work correctly.
- Lets you stop and run again later.
  - Completed `.m4b` files stay finished.
  - Completed `.m4b` files are skipped next time.
  - Any audiobook that was mid-conversion restarts on the next run.

### What This Repo Contains:

<details>
<summary><em>Open What This Repo Contains</em></summary>
<br>

- `process_audiobooks_to_m4b.py`, the terminal converter tool.
- This `README.md` with beginner-friendly documentation.
- `docs/USAGE.md` with a shorter step-by-step usage guide.
- `examples/metadata.example.json` with a small metadata example.
- `CHANGELOG.md` with version notes.
- `.gitignore` for Python cache files, generated `.m4b` files, and temporary conversion files.

</details>

--------------------------------------------------

### Table of Contents:

<details>
<summary><em>Open Table of Contents</em></summary>
<br>

- [What This Tool Does](#what-this-tool-does)
- [What This Repo Contains](#what-this-repo-contains)
- [Requirements](#requirements)
  - [What beginners should expect](#what-beginners-should-expect)
  - [Runtime requirements list](#runtime-requirements-list)
  - [Beginner-friendly requirements download links](#beginner-friendly-requirements-download-links)
  - [Terminal requirements install instructions](#terminal-requirements-install-instructions)
    - [macOS](#macos)
    - [Windows](#windows)
- [How to Set Up Your Audiobook Folders](#how-to-set-up-your-audiobook-folders)
- [How to Run Terminal Bulk Audiobook Converter](#how-to-run-terminal-bulk-audiobook-converter)
  - [Option 1: Run from inside your audiobook library folder](#option-1-run-from-inside-your-audiobook-library-folder)
  - [Option 2: Run from anywhere and point to your audiobook library folder](#option-2-run-from-anywhere-and-point-to-your-audiobook-library-folder)
  - [Dry run first](#dry-run-first)
- [Welcome Screen](#welcome-screen)
- [Live Progress Dashboard](#live-progress-dashboard)
- [Options](#options)
- [Resume and Cancel Behavior](#resume-and-cancel-behavior)
- [Metadata and Cover Art](#metadata-and-cover-art)
- [Output](#output)
- [Troubleshooting](#troubleshooting)
  - [The tool says ffmpeg and ffprobe are missing](#the-tool-says-ffmpeg-and-ffprobe-are-missing)
  - [A folder was skipped](#a-folder-was-skipped)
  - [The dashboard looks strange](#the-dashboard-looks-strange)
- [Version Notes](#version-notes)
- [Source Availability and License](#source-availability-and-license)

</details>

--------------------------------------------------

### Requirements:

<details>
<summary><em>Open Requirements</em></summary>
<br>

#### What beginners should expect:

- This is a terminal tool, not a double-click desktop app.
- You run it from Terminal, Command Prompt, PowerShell, or another terminal app.
- No Python package install is needed.
- You do need Python 3 and ffmpeg installed.
- Conversion runs locally on your computer.
- Your audiobook files are not uploaded anywhere.

#### Runtime requirements list:

- Python 3.10+
- `ffmpeg`
- `ffprobe`

#### Beginner-friendly requirements download links:

- Python 3: https://www.python.org/downloads/
- ffmpeg: https://ffmpeg.org/download.html
- ffprobe: included with ffmpeg installs.

#### Terminal requirements install instructions:

##### macOS

If you use Homebrew:

- Install Python:
  - `brew install python`
- Install ffmpeg and ffprobe:
  - `brew install ffmpeg`
- Confirm they work:
  - `python3 --version`
  - `ffmpeg -version`
  - `ffprobe -version`

##### Windows

If you use winget:

- Install Python:
  - `winget install --id Python.Python.3.12 -e`
- Install ffmpeg:
  - `winget install Gyan.FFmpeg`
- Confirm they work:
  - `py --version`
  - `ffmpeg -version`
  - `ffprobe -version`

</details>

--------------------------------------------------

### How to Set Up Your Audiobook Folders:

Each audiobook must be in its own folder.

Each audiobook folder must include:

- every numbered audio part for that book
- a cover image named `cover.jpg`, `cover.jpeg`, `cover.png`, `folder.jpg`, `folder.jpeg`, or `folder.png`
- a `metadata.json` file

Example:

```text
My Audiobooks/
  Dune Messiah by Frank Herbert/
    Dune Messiah - Part 01.mp3
    Dune Messiah - Part 02.mp3
    Dune Messiah - Part 03.mp3
    cover.jpg
    metadata.json
```

Supported audio input file types:

- `.mp3`
- `.m4a`
- `.aac`
- `.wav`
- `.flac`
- `.ogg`

The tool scans inside each audiobook folder recursively, so audio parts may be inside nested folders. It still treats one top-level folder as one book.

--------------------------------------------------

### How to Run Terminal Bulk Audiobook Converter:

#### Option 1: Run from inside your audiobook library folder

Put `process_audiobooks_to_m4b.py` inside the folder that contains all your audiobook folders.

Then run:

```bash
./process_audiobooks_to_m4b.py
```

#### Option 2: Run from anywhere and point to your audiobook library folder

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks"
```

#### Dry run first:

Dry run mode shows what would be converted without creating `.m4b` files.

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks" --dry-run
```

--------------------------------------------------

### Welcome Screen:

When you start the tool, it shows a welcome message and asks:

```text
Do you wish to start converting?

1: Yes, start
2: No, quit
```

Choose `1` to start.

Choose `2` to quit without converting anything.

--------------------------------------------------

### Live Progress Dashboard:

While converting, the tool keeps one small progress area near the bottom of the terminal.

Example:

```text
Progress: 2/63 finished (3.2%)
------------------------------------------------------------------------
Dune Messiah by Frank Herbert:  18.4%
1984 by George Orwell:           7.9%
Animal Farm by George Orwell:   42.1%
```

Above the dashboard, the tool only prints feedback when something finishes or fails.

Example:

```text
DONE: Animal Farm by George Orwell (Audiobook) -> Animal Farm.m4b
```

--------------------------------------------------

### Options:

```bash
python3 process_audiobooks_to_m4b.py [audiobook-library-folder] [options]
```

Available options:

- Number of audiobooks to process at once:
  - `--parallel 3`
- Audio bitrate for created `.m4b` files:
  - `--audio-bitrate 128k`
- Show what would happen without creating files:
  - `--dry-run`

Examples:

```bash
python3 process_audiobooks_to_m4b.py --dry-run
python3 process_audiobooks_to_m4b.py --parallel 2
python3 process_audiobooks_to_m4b.py --audio-bitrate 96k
```

--------------------------------------------------

### Resume and Cancel Behavior:

Completed `.m4b` files stay finished and are skipped on the next run.

If you cancel the tool with `Control+C`, any audiobook that was mid-conversion will restart from the beginning next time.

This is intentional because a half-written `.m4b` file cannot be safely resumed. The tool uses temporary files named like:

```text
Book Title.partial.m4b
```

Those partial files are temporary and are not treated as completed audiobooks.

--------------------------------------------------

### Metadata and Cover Art:

The tool expects a `metadata.json` file in each audiobook folder.

It supports metadata commonly exported from library/archive tools, including nested `meta` and `loan` objects.

If a folder is missing `metadata.json` or a supported cover image, it is skipped instead of being converted. This prevents creating `.m4b` files without required metadata or cover art.

Common fields used:

- `meta.title`
- `meta.author`
- `meta.narrator`
- `meta.description`
- `meta.publisher.name`
- `loan.publishDate`
- `loan.publishDateText`
- `loan.subjects`
- `loan.detailedSeries`

See:

- [examples/metadata.example.json](examples/metadata.example.json)

--------------------------------------------------

### Output:

Each audiobook folder receives one `.m4b` file named from the book title.

Example:

```text
Dune Messiah by Frank Herbert/
  Dune Messiah.m4b
```

The tool does not delete your original source files.

Before a new `.m4b` is accepted, the tool checks that:

- every discovered audio part was readable before conversion
- the output duration is at least 98% of the full source duration
- cover art is embedded when a cover image was provided
- title metadata is present

--------------------------------------------------

### Troubleshooting:

#### The tool says ffmpeg and ffprobe are missing:

Install ffmpeg and make sure both `ffmpeg` and `ffprobe` work in the same terminal where you run the script.

#### A folder was skipped:

Check the skipped reason printed at startup.

Common reasons:

- The folder already has an `.m4b`.
- The folder is missing `metadata.json`.
- The folder is missing a supported cover image.
- The folder has no supported audio files.

#### The dashboard looks strange:

The live dashboard is designed for a normal interactive terminal. If output is redirected to a text file or run inside a non-interactive console, the tool falls back to regular line output.

--------------------------------------------------

### Version Notes:

Current version: `v1`

This is the first repo-ready version of Terminal Bulk Audiobook Converter by mp3li.

--------------------------------------------------

### Source Availability and License:

This project is currently source-available.

No open-source license has been selected yet. Add a `LICENSE` file before publishing this as a public repository if you want other people to use, copy, modify, or redistribute it under specific terms.
