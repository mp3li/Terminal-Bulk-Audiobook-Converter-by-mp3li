# Changelog

## v2.0.0

- Added startup workflow choices for metadata only, m4b conversion only, or metadata plus conversion.
- Added metadata enrichment from Open Library, Google Books, and iTunes Audiobooks.
- Added optional Audible CLI metadata lookup with `--audible`.
- Added supplement-missing vs full-replace metadata policy choices.
- Added `metadata.enriched.json` sidecar output.
- Added missing cover download from provider results.
- Added local metadata response caching in `.metadata_cache/`.
- Added and documented launch-time folder snapshot behavior so newly added audiobook folders wait until the next run.

## v1.0.0

- Added batch audiobook folder conversion to `.m4b`.
- Added metadata and cover art embedding.
- Added skip behavior for folders with existing `.m4b` files.
- Added 3-at-a-time conversion by default.
- Added live terminal dashboard with per-audiobook progress.
- Added cancellation-safe partial output handling.
- Added welcome/start prompt.
