# Usage Guide

## 1. Prepare Your Audiobook Folders

Put each audiobook in its own folder. The folder name should be readable, ideally `Title by Author`.

The tool only works on audiobook folders that existed when it launched. You can add more audiobook folders while metadata or conversion is running, and those new folders wait until the next run.

Do not edit, rename, move, or delete a folder that is currently being processed.

For conversion-only mode, each folder must contain:

- Every numbered audio part for that book, such as `.mp3`, `.m4a`, `.aac`, `.wav`, `.flac`, or `.ogg`
- A cover image named `cover.enriched.jpg`, `cover.jpg`, `cover.jpeg`, `cover.png`, `folder.jpg`, `folder.jpeg`, or `folder.png`
- A `metadata.enriched.json` or `metadata.json` file

For metadata mode, the folder name should be readable, ideally `Title by Author`.

Example:

```text
My Audiobooks/
  Dune Messiah by Frank Herbert/
    Dune Messiah - Part 01.mp3
    Dune Messiah - Part 02.mp3
    cover.jpg
    metadata.json
```

The tool scans inside each audiobook folder recursively, so parts may be inside nested folders. Conversion skips a book if it is missing metadata, missing a supported cover image, or missing audio files.

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

Then choose what you want to do:

```text
1: Add metadata only
2: Convert to m4b only
3: Add metadata, then convert to m4b
4: Quit
```

Metadata mode uses Open Library, Google Books, and iTunes Audiobooks by default. It writes `metadata.enriched.json` and downloads missing cover art when a good match has a cover.

If you choose metadata only or metadata plus conversion, the tool then asks:

```text
1: Supplement missing metadata only (keeps existing values)
2: Fully overwrite and replace metadata
3: Quit
```

Supplement mode keeps existing fields that already have values and fills blanks from provider results. Use it when you trust the current filled-in metadata. Replace mode rewrites `metadata.enriched.json` from the best provider match and writes `cover.enriched.jpg` when provider cover art is available.

To also try Audible through an installed and authenticated Audible CLI:

```bash
python3 process_audiobooks_to_m4b.py "/path/to/My Audiobooks" --audible
```

The dashboard shows total completed audiobooks and current per-book percentages.

The tool only accepts a finished `.m4b` when the output duration matches the full source duration closely, the cover is embedded, and title metadata is present.

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
