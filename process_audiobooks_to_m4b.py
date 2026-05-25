#!/usr/bin/env python3
import argparse
import asyncio
import difflib
import hashlib
import json
import re
import shutil
import ssl
import subprocess
import sys
import termios
import tty
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
COVER_NAMES = (
    "cover.enriched.jpg",
    "cover.enriched.jpeg",
    "cover.enriched.png",
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
)
ENRICHED_METADATA_FILENAME = "metadata.enriched.json"
METADATA_CACHE_DIR = ".metadata_cache"
STATUS_FILENAME = ".m4b_batch_status.json"
MIN_OUTPUT_DURATION_RATIO = 0.98
CONCAT_LIST_PART_THRESHOLD = 200
USER_AGENT = "TerminalBulkAudiobookConverter/2.0 (metadata enrichment)"
PROVIDER_WARNINGS: set[str] = set()
_SSL_CONTEXT = None


def metadata_ssl_context():
    global _SSL_CONTEXT
    if _SSL_CONTEXT is not None:
        return _SSL_CONTEXT
    try:
        import certifi
    except ImportError:
        _SSL_CONTEXT = ssl.create_default_context()
    else:
        _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
    return _SSL_CONTEXT


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


def clean_list(values) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    cleaned = []
    for value in values:
        text = clean_text(value)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def first_text(*values) -> str | None:
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return None


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


def normalize_match_text(value: str | None) -> str:
    text = clean_text(value) or ""
    text = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    return re.sub(r"\s+", " ", text).strip()


def title_author_score(query_title: str, query_author: str | None, title: str | None, authors: list[str]) -> float:
    title_score = difflib.SequenceMatcher(
        None,
        normalize_match_text(query_title),
        normalize_match_text(title),
    ).ratio()
    author_score = 0.0
    if query_author and authors:
        query_author = normalize_match_text(query_author)
        author_score = max(
            difflib.SequenceMatcher(None, query_author, normalize_match_text(author)).ratio()
            for author in authors
        )
    elif not query_author:
        author_score = 0.65
    return min(1.0, (title_score * 0.75) + (author_score * 0.25))


def boosted_score(score: float, boost: float) -> float:
    return min(1.0, score + boost)


def short_text(text: str, max_length: int = 88) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def metadata_file_for_read(folder: Path) -> Path | None:
    enriched = folder / ENRICHED_METADATA_FILENAME
    if enriched.exists():
        return enriched
    metadata = folder / "metadata.json"
    if metadata.exists():
        return metadata
    return None


def original_metadata_file_for_read(folder: Path) -> Path | None:
    metadata = folder / "metadata.json"
    if metadata.exists():
        return metadata
    enriched = folder / ENRICHED_METADATA_FILENAME
    if enriched.exists():
        return enriched
    return None


def read_json_file(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_metadata(folder: Path) -> dict:
    metadata_path = metadata_file_for_read(folder)
    inferred_title, inferred_author = infer_title_author(folder)

    if metadata_path is None:
        tags = {
            "title": inferred_title,
            "album": inferred_title,
            "artist": inferred_author,
            "album_artist": inferred_author,
            "composer": inferred_author,
            "genre": "Audiobook",
        }
        return {key: value for key, value in tags.items() if value}

    raw = read_json_file(metadata_path)

    meta = raw.get("meta") or {}
    loan = raw.get("loan") or {}
    publisher = meta.get("publisher") or loan.get("publisherAccount") or raw.get("publisher") or {}
    detailed_series = loan.get("detailedSeries") or {}

    publish_date = (
        meta.get("publish_date")
        or meta.get("published_date")
        or raw.get("published_date")
        or loan.get("publishDateText")
        or loan.get("publishDate")
    )
    if publish_date and "T" in str(publish_date):
        publish_date = str(publish_date).split("T", 1)[0]

    subjects = loan.get("subjects") or raw.get("subjects") or raw.get("genres") or []
    genres = [
        item.get("name") if isinstance(item, dict) else item
        for item in subjects
        if (isinstance(item, dict) and item.get("name")) or isinstance(item, str)
    ]

    authors = clean_list(meta.get("authors") or raw.get("authors"))
    narrators = clean_list(meta.get("narrators") or raw.get("narrators"))

    title = first_text(meta.get("title"), loan.get("title"), raw.get("title"), inferred_title)
    author = first_text(meta.get("author"), loan.get("firstCreatorName"), ", ".join(authors), inferred_author)
    narrator = first_text(meta.get("narrator"), ", ".join(narrators))
    description = first_text(meta.get("description"), raw.get("description"))
    series = first_text(meta.get("series"), raw.get("series"), loan.get("series"), detailed_series.get("seriesName"))
    reading_order = first_text(meta.get("series_index"), raw.get("series_index"), detailed_series.get("readingOrder"))

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


def cache_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json"


def fetch_json(url: str, cache_root: Path, timeout: int = 20, write_cache: bool = True) -> dict:
    cache_dir = cache_root / METADATA_CACHE_DIR
    cache_path = cache_dir / cache_key(url)
    if cache_path.exists():
        cached = read_json_file(cache_path)
        if cached:
            return cached

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=metadata_ssl_context()) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            PROVIDER_WARNINGS.add(
                "Python could not verify HTTPS certificates for metadata providers. "
                "On macOS Python.org installs, run the bundled Install Certificates.command, then try again."
            )
        else:
            PROVIDER_WARNINGS.add(f"Could not reach metadata provider: {reason or error}")
        return {}
    except (TimeoutError, json.JSONDecodeError, OSError):
        return {}

    if isinstance(data, dict):
        if write_cache:
            cache_dir.mkdir(exist_ok=True)
            cache_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
    return {}


def search_open_library(title: str, author: str | None, cache_root: Path, write_cache: bool) -> list[dict]:
    query = " ".join(part for part in [title, author] if part)
    url = "https://openlibrary.org/search.json?" + urllib.parse.urlencode(
        {"q": query, "limit": 5, "fields": "title,author_name,first_publish_year,publisher,subject,isbn,cover_i,key"}
    )
    data = fetch_json(url, cache_root, write_cache=write_cache)
    results = []
    for item in data.get("docs") or []:
        authors = clean_list(item.get("author_name"))
        cover_id = item.get("cover_i")
        result = {
            "provider": "open_library",
            "title": clean_text(item.get("title")),
            "authors": authors,
            "published_date": clean_text(item.get("first_publish_year")),
            "publisher": clean_list(item.get("publisher"))[0] if clean_list(item.get("publisher")) else None,
            "genres": clean_list(item.get("subject"))[:12],
            "isbn_10": None,
            "isbn_13": None,
            "cover_url": f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg" if cover_id else None,
            "source_id": clean_text(item.get("key")),
        }
        isbns = clean_list(item.get("isbn"))
        for isbn in isbns:
            if len(isbn) == 10 and result["isbn_10"] is None:
                result["isbn_10"] = isbn
            if len(isbn) == 13 and result["isbn_13"] is None:
                result["isbn_13"] = isbn
        result["score"] = title_author_score(title, author, result["title"], authors)
        results.append(result)
    return results


def search_google_books(title: str, author: str | None, cache_root: Path, write_cache: bool) -> list[dict]:
    query = f'intitle:"{title}"'
    if author:
        query += f' inauthor:"{author}"'
    url = "https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode(
        {"q": query, "maxResults": 5, "printType": "books"}
    )
    data = fetch_json(url, cache_root, write_cache=write_cache)
    results = []
    for item in data.get("items") or []:
        info = item.get("volumeInfo") or {}
        authors = clean_list(info.get("authors"))
        identifiers = info.get("industryIdentifiers") or []
        image_links = info.get("imageLinks") or {}
        result = {
            "provider": "google_books",
            "title": clean_text(info.get("title")),
            "subtitle": clean_text(info.get("subtitle")),
            "authors": authors,
            "description": clean_text(info.get("description")),
            "publisher": clean_text(info.get("publisher")),
            "published_date": clean_text(info.get("publishedDate")),
            "genres": clean_list(info.get("categories")),
            "language": clean_text(info.get("language")),
            "page_count": info.get("pageCount"),
            "cover_url": clean_text(image_links.get("extraLarge") or image_links.get("large") or image_links.get("thumbnail")),
            "source_id": clean_text(item.get("id")),
        }
        for identifier in identifiers:
            kind = identifier.get("type")
            value = clean_text(identifier.get("identifier"))
            if kind == "ISBN_10":
                result["isbn_10"] = value
            if kind == "ISBN_13":
                result["isbn_13"] = value
        if result["cover_url"]:
            result["cover_url"] = result["cover_url"].replace("http://", "https://")
        result["score"] = title_author_score(title, author, result["title"], authors)
        results.append(result)
    return results


def search_itunes_audiobooks(title: str, author: str | None, cache_root: Path, write_cache: bool) -> list[dict]:
    term = " ".join(part for part in [title, author] if part)
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(
        {"term": term, "media": "audiobook", "entity": "audiobook", "limit": 5}
    )
    data = fetch_json(url, cache_root, write_cache=write_cache)
    results = []
    for item in data.get("results") or []:
        authors = clean_list(item.get("artistName"))
        cover = clean_text(item.get("artworkUrl100"))
        if cover:
            cover = re.sub(r"/\d+x\d+bb\.", "/1200x1200bb.", cover)
        result = {
            "provider": "itunes",
            "title": clean_text(item.get("collectionName") or item.get("trackName")),
            "authors": authors,
            "description": clean_text(item.get("description")),
            "published_date": clean_text(item.get("releaseDate")),
            "genres": clean_list(item.get("primaryGenreName")),
            "duration_ms": item.get("trackTimeMillis"),
            "cover_url": cover,
            "source_id": clean_text(item.get("collectionId") or item.get("trackId")),
        }
        result["score"] = boosted_score(title_author_score(title, author, result["title"], authors), 0.03)
        results.append(result)
    return results


def search_audible_cli(title: str, author: str | None) -> list[dict]:
    audible_bin = shutil.which("audible")
    if audible_bin is None:
        return []

    keywords = " ".join(part for part in [title, author] if part)
    query = "/1.0/catalog/products?" + urllib.parse.urlencode(
        {
            "keywords": keywords,
            "num_results": "5",
            "response_groups": "contributors,product_attrs,product_desc,media,product_extended_attrs,series",
        }
    )
    try:
        completed = subprocess.run(
            [audible_bin, "api", query],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if completed.returncode != 0:
        return []

    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []

    products = data.get("products") or data.get("items") or []
    results = []
    for product in products:
        authors = []
        narrators = []
        for contributor in product.get("authors") or product.get("contributors") or []:
            name = contributor.get("name") if isinstance(contributor, dict) else contributor
            role = normalize_match_text(contributor.get("role") if isinstance(contributor, dict) else "")
            if "narrator" in role:
                narrators.extend(clean_list(name))
            else:
                authors.extend(clean_list(name))
        for contributor in product.get("narrators") or []:
            name = contributor.get("name") if isinstance(contributor, dict) else contributor
            narrators.extend(clean_list(name))
        series = product.get("series") or []
        first_series = series[0] if isinstance(series, list) and series else {}
        runtime_minutes = product.get("runtime_length_min")
        try:
            duration_ms = int(runtime_minutes) * 60 * 1000 if runtime_minutes else None
        except (TypeError, ValueError):
            duration_ms = None
        result = {
            "provider": "audible_cli",
            "title": clean_text(product.get("title")),
            "subtitle": clean_text(product.get("subtitle")),
            "authors": authors,
            "narrators": narrators,
            "description": clean_text(product.get("publisher_summary") or product.get("merchandising_summary")),
            "publisher": clean_text(product.get("publisher_name")),
            "published_date": clean_text(product.get("release_date")),
            "genres": clean_list(product.get("categories")),
            "asin": clean_text(product.get("asin")),
            "duration_ms": duration_ms,
            "series": clean_text(first_series.get("title") if isinstance(first_series, dict) else None),
            "series_index": clean_text(first_series.get("sequence") if isinstance(first_series, dict) else None),
            "cover_url": clean_text(product.get("product_images", {}).get("500") if isinstance(product.get("product_images"), dict) else None),
            "source_id": clean_text(product.get("asin")),
        }
        result["score"] = boosted_score(title_author_score(title, author, result["title"], authors), 0.06)
        results.append(result)
    return results


def merged_metadata_result(title: str, author: str | None, results: list[dict]) -> dict:
    results = sorted(results, key=lambda item: item.get("score") or 0, reverse=True)
    best = results[0] if results else {}
    merged = {
        "title": clean_text(best.get("title")) or title,
        "subtitle": clean_text(best.get("subtitle")),
        "authors": clean_list(best.get("authors") or author),
        "narrators": clean_list(best.get("narrators")),
        "description": clean_text(best.get("description")),
        "publisher": clean_text(best.get("publisher")),
        "published_date": clean_text(best.get("published_date")),
        "genres": clean_list(best.get("genres")),
        "language": clean_text(best.get("language")),
        "isbn_10": clean_text(best.get("isbn_10")),
        "isbn_13": clean_text(best.get("isbn_13")),
        "asin": clean_text(best.get("asin")),
        "series": clean_text(best.get("series")),
        "series_index": clean_text(best.get("series_index")),
        "cover_url": clean_text(best.get("cover_url")),
        "source_provider": clean_text(best.get("provider")),
        "source_id": clean_text(best.get("source_id")),
        "confidence_score": round(float(best.get("score") or 0), 3),
    }

    for result in results[1:]:
        for key in ["subtitle", "description", "publisher", "published_date", "language", "isbn_10", "isbn_13", "asin", "series", "series_index", "cover_url"]:
            if not merged.get(key) and result.get(key):
                merged[key] = clean_text(result.get(key))
        for key in ["authors", "narrators", "genres"]:
            merged[key] = clean_list([*merged.get(key, []), *clean_list(result.get(key))])

    if merged.get("genres"):
        merged["genres"] = merged["genres"][:20]

    return {key: value for key, value in merged.items() if value not in (None, [], "")}


def enriched_to_sidecar(metadata: dict) -> dict:
    authors = clean_list(metadata.get("authors"))
    narrators = clean_list(metadata.get("narrators"))
    genres = clean_list(metadata.get("genres"))
    publisher = clean_text(metadata.get("publisher"))
    sidecar = {
        "meta": {
            "title": clean_text(metadata.get("title")),
            "subtitle": clean_text(metadata.get("subtitle")),
            "author": ", ".join(authors) if authors else None,
            "authors": authors,
            "narrator": ", ".join(narrators) if narrators else None,
            "narrators": narrators,
            "description": clean_text(metadata.get("description")),
            "publisher": {"name": publisher} if publisher else None,
            "published_date": clean_text(metadata.get("published_date")),
            "series": clean_text(metadata.get("series")),
            "series_index": clean_text(metadata.get("series_index")),
        },
        "loan": {
            "publishDate": clean_text(metadata.get("published_date")),
            "subjects": [{"name": genre} for genre in genres],
            "detailedSeries": {
                "seriesName": clean_text(metadata.get("series")),
                "readingOrder": clean_text(metadata.get("series_index")),
            },
        },
        "identifiers": {
            "isbn_10": clean_text(metadata.get("isbn_10")),
            "isbn_13": clean_text(metadata.get("isbn_13")),
            "asin": clean_text(metadata.get("asin")),
        },
        "metadata_source": {
            "provider": clean_text(metadata.get("source_provider")),
            "source_id": clean_text(metadata.get("source_id")),
            "confidence_score": metadata.get("confidence_score"),
            "cover_url": clean_text(metadata.get("cover_url")),
        },
    }
    return prune_empty(sidecar)


def existing_metadata_for_sidecar(folder: Path) -> dict:
    metadata_path = original_metadata_file_for_read(folder)
    inferred_title, inferred_author = infer_title_author(folder)
    if metadata_path is None:
        return {"title": inferred_title, "authors": clean_list(inferred_author)}

    raw = read_json_file(metadata_path)
    meta = raw.get("meta") or {}
    loan = raw.get("loan") or {}
    publisher = meta.get("publisher") or loan.get("publisherAccount") or raw.get("publisher") or {}
    detailed_series = loan.get("detailedSeries") or {}
    identifiers = raw.get("identifiers") or {}

    subjects = loan.get("subjects") or raw.get("subjects") or raw.get("genres") or []
    genres = [
        item.get("name") if isinstance(item, dict) else item
        for item in subjects
        if (isinstance(item, dict) and item.get("name")) or isinstance(item, str)
    ]

    authors = clean_list(meta.get("authors") or raw.get("authors"))
    author = first_text(meta.get("author"), loan.get("firstCreatorName"), ", ".join(authors), inferred_author)
    if author and not authors:
        authors = [author]

    narrators = clean_list(meta.get("narrators") or raw.get("narrators"))
    narrator = first_text(meta.get("narrator"), ", ".join(narrators))
    if narrator and not narrators:
        narrators = [narrator]

    existing = {
        "title": first_text(meta.get("title"), loan.get("title"), raw.get("title"), inferred_title),
        "subtitle": first_text(meta.get("subtitle"), raw.get("subtitle")),
        "authors": authors,
        "narrators": narrators,
        "description": first_text(meta.get("description"), raw.get("description")),
        "publisher": clean_text(publisher.get("name") if isinstance(publisher, dict) else publisher),
        "published_date": first_text(
            meta.get("publish_date"),
            meta.get("published_date"),
            raw.get("published_date"),
            loan.get("publishDateText"),
            loan.get("publishDate"),
        ),
        "genres": clean_list(genres),
        "language": first_text(meta.get("language"), raw.get("language")),
        "isbn_10": first_text(identifiers.get("isbn_10"), raw.get("isbn_10")),
        "isbn_13": first_text(identifiers.get("isbn_13"), raw.get("isbn_13")),
        "asin": first_text(identifiers.get("asin"), raw.get("asin")),
        "series": first_text(meta.get("series"), raw.get("series"), loan.get("series"), detailed_series.get("seriesName")),
        "series_index": first_text(meta.get("series_index"), raw.get("series_index"), detailed_series.get("readingOrder")),
    }
    return {key: value for key, value in existing.items() if value not in (None, [], "")}


def supplement_metadata(existing: dict, provider: dict) -> dict:
    merged = dict(existing)
    for key, value in provider.items():
        if key in {"source_provider", "source_id", "confidence_score", "cover_url"}:
            merged[key] = value
        elif key in {"authors", "narrators", "genres"}:
            existing_values = clean_list(merged.get(key))
            if existing_values:
                merged[key] = existing_values
            else:
                merged[key] = clean_list(value)
        elif merged.get(key) in (None, "", []):
            merged[key] = value
    return {key: value for key, value in merged.items() if value not in (None, [], "")}


def prune_empty(value):
    if isinstance(value, dict):
        cleaned = {key: prune_empty(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item not in (None, "", [], {})}
    if isinstance(value, list):
        return [prune_empty(item) for item in value if prune_empty(item) not in (None, "", [], {})]
    return value


def download_cover(url: str, folder: Path, dry_run: bool, replace_existing: bool = False) -> Path | None:
    if not url or (not replace_existing and find_cover(folder) is not None):
        return None
    suffix = ".jpg"
    parsed_path = urllib.parse.urlparse(url).path.casefold()
    if parsed_path.endswith(".png"):
        suffix = ".png"
    output = folder / f"cover.enriched{suffix}"
    if dry_run:
        return output
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30, context=metadata_ssl_context()) as response:
            data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None
    if len(data) < 1024:
        return None
    output.write_bytes(data)
    return output


def enrich_folder_metadata(folder: Path, root: Path, args: argparse.Namespace) -> tuple[bool, str]:
    existing = existing_metadata_for_sidecar(folder)
    inferred_title, inferred_author = infer_title_author(folder)
    title = inferred_title or existing.get("title")
    author = inferred_author or (clean_list(existing.get("authors"))[0] if clean_list(existing.get("authors")) else None)

    results = []
    results.extend(search_open_library(title, author, root, not args.dry_run))
    results.extend(search_google_books(title, author, root, not args.dry_run))
    results.extend(search_itunes_audiobooks(title, author, root, not args.dry_run))
    if args.audible:
        results.extend(search_audible_cli(title, author))

    useful = [result for result in results if (result.get("score") or 0) >= args.metadata_min_score]
    if not useful:
        return False, f"NO MATCH: {folder.name}"

    provider_metadata = merged_metadata_result(title, author, useful)
    if args.metadata_policy == "supplement":
        metadata = supplement_metadata(existing, provider_metadata)
    else:
        metadata = provider_metadata
    sidecar = enriched_to_sidecar(metadata)
    metadata_path = folder / ENRICHED_METADATA_FILENAME
    cover_path = download_cover(
        provider_metadata.get("cover_url"),
        folder,
        args.dry_run,
        replace_existing=args.metadata_policy == "replace",
    )

    if args.dry_run:
        action = "supplement" if args.metadata_policy == "supplement" else "replace"
        cover_note = " and cover" if cover_path else ""
        return True, f"DRY RUN: would {action} metadata{cover_note} for {folder.name}"

    metadata_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8")
    cover_note = f" + {cover_path.name}" if cover_path else ""
    provider = provider_metadata.get("source_provider") or "metadata provider"
    score = provider_metadata.get("confidence_score")
    action = "supplemented" if args.metadata_policy == "supplement" else "replaced"
    return True, f"METADATA {action}: {folder.name} <- {provider} ({score}){cover_note}"


def launch_audiobook_folders(root: Path) -> list[Path]:
    return sorted(
        [
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".") and path.name != "__pycache__"
        ],
        key=natural_key,
    )


def enrich_library_metadata(root: Path, folders: list[Path], args: argparse.Namespace) -> int:
    if not folders:
        print("No audiobook folders found.")
        return 0

    print()
    print(f"Checking metadata for {len(folders)} audiobook folder(s).")
    print("Providers: Open Library, Google Books, iTunes Audiobooks" + (", Audible CLI" if args.audible else ""))
    print(
        "Metadata policy: "
        + ("supplement missing fields only" if args.metadata_policy == "supplement" else "fully replace sidecar metadata")
    )
    if args.dry_run:
        print("Dry run mode: no metadata or cover files will be written.")
    print()

    updated = 0
    failed = 0
    for folder in folders:
        ok, message = enrich_folder_metadata(folder, root, args)
        print(message)
        if ok:
            updated += 1
        else:
            failed += 1

    print()
    print(f"Metadata complete: {updated} updated, {failed} unmatched.")
    if PROVIDER_WARNINGS:
        print()
        print("Provider warning(s):")
        for warning in sorted(PROVIDER_WARNINGS):
            print(f"  - {warning}")
    return 1 if failed and updated == 0 else 0


def audiobook_jobs(root: Path, audiobook_folders: list[Path]) -> tuple[list[dict], list[str], dict]:
    jobs = []
    skipped = []
    summary = {
        "total_folders": 0,
        "needs_conversion": 0,
        "already_converted": 0,
        "not_ready": 0,
    }

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

        metadata_path = metadata_file_for_read(folder)
        if metadata_path is None:
            summary["not_ready"] += 1
            skipped.append(f"{folder.name} - missing metadata.json or {ENRICHED_METADATA_FILENAME}")
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


def concat_file_line(path: Path) -> str:
    escaped = str(path).replace("'", "'\\''")
    return f"file '{escaped}'"


def write_concat_file(job: dict, concat_path: Path) -> None:
    lines = [concat_file_line(path) for path in job["audio_files"]]
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_concat_command(job: dict, concat_path: Path, partial_output: Path, audio_bitrate: str) -> list[str]:
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
    ]

    if job["cover"] is not None:
        command.extend(["-i", str(job["cover"])])

    command.extend(
        [
            "-map",
            "0:a:0",
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
        ]
    )

    if job["cover"] is not None:
        cover_codec = "copy" if job["cover"].suffix.casefold() in {".jpg", ".jpeg"} else "mjpeg"
        command.extend(
            [
                "-map",
                "1:v:0",
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
    if job["cover"] is not None:
        command[-5:-5] = [
            "-metadata:s:v",
            "title=Cover",
            "-metadata:s:v",
            "comment=Cover (front)",
        ]
    return command


def build_direct_command(job: dict, partial_output: Path, audio_bitrate: str) -> list[str]:
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
    concat_path = output.with_name(f"{output.stem}.concat.txt")

    if args.dry_run:
        return True, f"DRY RUN: would create {output.name}"

    if partial_output.exists():
        partial_output.unlink()
    if concat_path.exists():
        concat_path.unlink()
    use_concat_list = len(job["audio_files"]) > CONCAT_LIST_PART_THRESHOLD

    audio_durations = await job_audio_durations(job)
    unreadable = [path.name for path, duration in audio_durations if duration <= 0]
    if unreadable:
        await progress_queue.put((folder.name, "failed", 0.0, f"FAILED: {folder.name}"))
        return False, f"FAILED: {folder.name}\nCould not read duration for: {', '.join(unreadable)}"

    total_duration = sum(duration for _, duration in audio_durations)
    if use_concat_list:
        write_concat_file(job, concat_path)
        command = build_concat_command(job, concat_path, partial_output, args.audio_bitrate)
    else:
        command = build_direct_command(job, partial_output, args.audio_bitrate)
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
        if concat_path.exists():
            concat_path.unlink()
        await progress_queue.put((folder.name, "cancelled", 0.0, f"CANCELLED: {folder.name}"))
        raise

    if process.returncode != 0:
        if partial_output.exists():
            partial_output.unlink()
        if concat_path.exists():
            concat_path.unlink()
        message = stderr
        await progress_queue.put((folder.name, "failed", 0.0, f"FAILED: {folder.name}"))
        return False, f"FAILED: {folder.name}\n{message}"

    valid, validation_message = await validate_output(job, partial_output, total_duration)
    if not valid:
        if partial_output.exists():
            partial_output.unlink()
        if concat_path.exists():
            concat_path.unlink()
        await progress_queue.put((folder.name, "failed", 0.0, f"FAILED: {folder.name}"))
        return False, f"FAILED: {folder.name}\n{validation_message}"

    partial_output.replace(output)
    if concat_path.exists():
        concat_path.unlink()
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
    parser.add_argument(
        "--audible",
        action="store_true",
        help="Also try the optional authenticated Audible CLI provider if the audible command is installed.",
    )
    parser.add_argument(
        "--metadata-min-score",
        type=float,
        default=0.62,
        help="Minimum metadata match score from 0.0 to 1.0. Defaults to 0.62.",
    )
    parser.add_argument(
        "--metadata-policy",
        choices=("supplement", "replace"),
        help="Skip the metadata policy prompt. Use supplement to fill blanks only or replace to fully rewrite metadata.enriched.json.",
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


def print_welcome() -> None:
    print()
    print("Welcome to Terminal Bulk Audiobook Converter v2 by mp3li")
    print()
    print(
        "This tool can add audiobook metadata from free online providers, convert "
        "audiobook folders into m4b files, or do both in one run. Each audiobook must "
        "be in its own folder, named Title by Author when possible, and each part or "
        "chapter must be numbered in a clear order of some sort."
    )
    print()
    print(
        "Metadata mode checks Open Library, Google Books, and iTunes Audiobooks by "
        "default. If you pass --audible and have an authenticated Audible CLI set up, "
        "it will try Audible too."
    )
    print()
    print(
        "Conversion mode skips folders that already have m4b files, processes 3 "
        "conversions at a time by default, and validates duration, cover art, and "
        "title metadata before accepting the output."
    )


def choose_workflow_mode() -> str | None:
    print_welcome()
    print()
    print("What do you want to do?")
    print()
    print("1: Add metadata only")
    print("2: Convert to m4b only")
    print("3: Add metadata, then convert to m4b")
    print("4: Quit")
    print()

    while True:
        choice = read_menu_choice()
        if choice == "1":
            print()
            return "metadata"
        if choice == "2":
            print()
            return "convert"
        if choice == "3":
            print()
            return "both"
        if choice == "4":
            print()
            print("Quit. No audiobooks were changed.")
            return None
        print("Please enter 1, 2, 3, or 4.")


def choose_metadata_policy() -> str | None:
    print("How should metadata mode treat existing metadata?")
    print()
    print("1: Supplement missing metadata only (keeps existing values)")
    print("2: Fully overwrite and replace metadata")
    print("3: Quit")
    print()

    while True:
        choice = read_menu_choice()
        if choice == "1":
            print()
            return "supplement"
        if choice == "2":
            print()
            return "replace"
        if choice == "3":
            print()
            print("Quit. No audiobooks were changed.")
            return None
        print("Please enter 1, 2, or 3.")


def main() -> int:
    args = parse_args()
    root = Path(args.root).expanduser().resolve()

    if args.parallel < 1:
        print("--parallel must be at least 1", file=sys.stderr)
        return 2
    if not 0 <= args.metadata_min_score <= 1:
        print("--metadata-min-score must be between 0.0 and 1.0", file=sys.stderr)
        return 2

    if not root.exists() or not root.is_dir():
        print(f"Root folder not found: {root}", file=sys.stderr)
        return 2

    launch_folders = launch_audiobook_folders(root)

    mode = choose_workflow_mode()
    if mode is None:
        return 0

    if mode in {"metadata", "both"} and args.metadata_policy is None:
        args.metadata_policy = choose_metadata_policy()
        if args.metadata_policy is None:
            return 0
    elif args.metadata_policy is None:
        args.metadata_policy = "supplement"

    if mode in {"metadata", "both"}:
        metadata_result = enrich_library_metadata(root, launch_folders, args)
        if mode == "metadata":
            return metadata_result

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("ffmpeg and ffprobe must both be available on PATH.", file=sys.stderr)
        return 2

    jobs, skipped, summary = audiobook_jobs(root, launch_folders)
    print_scan_summary(summary, skipped)
    if not jobs:
        print("Nothing to process.")
        return 0

    print("Starting conversion.")
    print()

    try:
        return asyncio.run(process_jobs(jobs, args))
    except KeyboardInterrupt:
        print("\nCancelled. Completed .m4b files stay finished and will be skipped next time.")
        print("Any audiobook that was mid-conversion will restart on the next run.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
