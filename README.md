# f0334 — Crossref Author Metadata Fetcher

Reads an Excel file with a `Publication_DOI` column, queries the [Crossref Works API](https://api.crossref.org) for each unique DOI, and produces a structured JSON file containing author metadata (names, ORCID IDs, and affiliations with ROR identifiers and place).

## Reproducibility guarantees

- No inference, correction, or invention of author names.
- No sources other than Crossref.
- No paraphrasing of metadata — values are taken verbatim.
- Crossref author order preserved exactly as returned.
- Unicode characters preserved.
- Deterministic processing: same input always produces the same output.
- JSON cache prevents redundant API calls across runs.

## Requirements

- Python 3.8+
- [requests](https://pypi.org/project/requests/)
- [openpyxl](https://pypi.org/project/openpyxl/)

Install dependencies:

```bash
pip install requests openpyxl
```

## Configuration

Copy `config.ini.example` to `config.ini` and fill in your details:

```ini
[crossref]
email = your_email@example.com   # Required — used in the Crossref polite pool
delay = 1                         # Seconds between API requests
save_every = 50                   # Save cache every N fetches
max_retries = 3                   # Retries on transient errors
```

> **Note:** `config.ini` is listed in `.gitignore` and must never be committed — it may contain credentials.

## Usage

```bash
python f0334.py <input.xlsx> <output.json> [config.ini]
```

**Example:**

```bash
python f0334.py Publications_with_high_confidence.xlsx Crossref_AuthorMetadata.json config.ini
```

### Input

An `.xlsx` file with at least one column named `Publication_DOI`. Duplicate DOIs (case-insensitive) are deduplicated automatically, preserving first-occurrence order.

### Output

A JSON array where each element corresponds to one DOI:

```json
[
  {
    "doi": "10.1234/example",
    "title": "Example Publication Title",
    "authors": [
      {
        "given": "Jane",
        "family": "Smith",
        "orcid": "0000-0000-0000-0001",
        "affiliations": [
          {
            "name": "Example University",
            "ror_id": "https://ror.org/00000000",
            "place": "Melbourne, Australia"
          }
        ]
      }
    ]
  }
]
```

If a DOI is not found (HTTP 404) or a network error occurs, the entry will contain an `"error"` key instead of author data.

### Caching

A cache file (`<output>_cache.json`) is written alongside the output. On subsequent runs, already-fetched DOIs are read from cache, avoiding redundant API calls. The cache is also saved periodically (every `save_every` fetches) and on `Ctrl+C` interruption, allowing safe resumption.

## Output statistics

At the end of each run the script prints a summary:

```
==================================================
SUMMARY
==================================================
  DOIs processed:           128
  DOIs with errors/404:     1
  Total authors:            704
  Authors with ORCID:       172
  Authors without ORCID:    532
  Authors with affiliation: 270
==================================================
```
