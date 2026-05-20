# Usage Guide

## 1. Prepare Your Audiobook Folders

Put each audiobook in its own folder. The folder name should be readable, ideally `Title by Author`.

Each folder should contain:

- Numbered audio parts, such as `.mp3`, `.m4a`, `.aac`, `.wav`, `.flac`, or `.ogg`
- A cover image named `cover.jpg`, `cover.jpeg`, `cover.png`, `folder.jpg`, `folder.jpeg`, or `folder.png`
- A `metadata.json` file

Example:

```text
My Audiobooks/
  Dune Messiah by Frank Herbert/
    Dune Messiah - Part 01.mp3
    Dune Messiah - Part 02.mp3
    cover.jpg
    metadata.json
```

## 2. Run a Dry Run First

Dry run mode lists what the tool would convert without creating files:

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks" --dry-run
```

## 3. Start Conversion

Run:

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks"
```

Then choose:

```text
1: Yes, start
```

The dashboard shows total completed audiobooks and current per-book percentages.

## 4. Stop and Resume

Press `Control+C` to stop.

On the next run:

- Completed `.m4b` files are skipped.
- Audiobooks that were mid-conversion restart.
- Original source files remain untouched.

## 5. Change Batch Size

The default is 3 conversions at a time:

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks" --parallel 3
```

Use a lower number if your computer slows down:

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks" --parallel 1
```

## 6. Change Audio Bitrate

Default bitrate is `128k`:

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks" --audio-bitrate 128k
```

For smaller files:

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks" --audio-bitrate 96k
```
