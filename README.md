# f0336
BDBSF Fetch co-author affiliations from Crossref and OpenAlex
# Crossref + OpenAlex Affiliation Fetch

A Python script that fetches author affiliations (organisations/institutions) from scholarly metadata APIs, using the unique authors Excel file produced by `extract_unique_authors.py`.

## How it works

The script uses a two-phase lookup strategy to maximise affiliation coverage:

1. **Phase 1 — Crossref**: For each unique DOI, fetch the full author metadata from the Crossref API. Match each author by name and extract their listed affiliation(s).

2. **Phase 2 — OpenAlex fallback**: For authors where Crossref had no affiliation data, query the OpenAlex API. OpenAlex aggregates data from Crossref, ORCID, PubMed, Microsoft Academic Graph, and publisher websites, so it often has affiliations that Crossref lacks. It also resolves raw affiliation strings to standardised institution names.

The output includes an `Affiliation_Source` column so you can see where each affiliation came from.

## Requirements

- Python 3.7+
- Libraries: `openpyxl`, `requests`

```bash
pip install openpyxl requests
```

## Setup

Uses the same `config.ini` as `crossref_author_fetch.py`. Place it in the same folder as the script:

```ini
[crossref]
email = yourname@example.com
delay = 1
save_every = 50
max_retries = 3
```

The `email` is used for both Crossref's polite pool and OpenAlex's courtesy rate limit pool.

## Input file format

The script expects the output of `extract_unique_authors.py` — an Excel file with these columns:

| Column | Required | Description |
|---|---|---|
| `Author_Name` | Yes | The canonical author name |
| `Name_Variants` | No | Semicolon-separated name variants (used for matching) |
| `DOI_Count` | No | Number of DOIs (carried through to output) |
| `DOIs` | Yes | Semicolon-separated DOIs to look up |

## Usage

### From a terminal / command prompt

```bash
python fetch_affiliations.py unique_authors.xlsx
```

Optionally specify output file and config path:

```bash
python fetch_affiliations.py unique_authors.xlsx output.xlsx /path/to/config.ini
```

### From Spyder (IPython console)

```python
!python "E:\your\folder\fetch_affiliations.py" "E:\your\folder\unique_authors.xlsx"
```

## Output

The script produces an Excel file named `<input_name>_with_affiliations.xlsx` with six columns:

| Column | Description |
|---|---|
| `Author_Name` | Canonical author name |
| `Name_Variants` | Other name forms for this author |
| `Affiliations` | Semicolon-separated organisation/institution names |
| `Affiliation_Source` | Where the data came from: `Crossref`, `OpenAlex`, or `None` |
| `DOI_Count` | Number of DOIs associated with this author |
| `DOIs` | All associated DOIs |

### Example output

| Author_Name | Affiliations | Affiliation_Source |
|---|---|---|
| Jane Doe | XYZ University; ABC Hospital  | Crossref |
| John Smith | [No affiliation found] | None |

## Cache files

The script creates three JSON cache files for resumability:

| File | Purpose |
|---|---|
| `<input>_crossref_doi_cache.json` | Crossref author metadata per DOI |
| `<input>_openalex_doi_cache.json` | OpenAlex authorship metadata per DOI |
| `<input>_affiliation_cache.json` | Resolved affiliations per author |

Re-running the script skips all previously fetched DOIs and previously resolved authors. To force a full re-fetch, delete the relevant cache file.

## Full pipeline

This script is step 3 in the pipeline:

```
1. Start with:  Publications_with_high_confidence.xlsx

2. Run:         python crossref_author_fetch.py <input>.xlsx
   Produces:    <input>_with_crossref_authors.xlsx

3. Run:         python extract_unique_authors.py <step2_output>.xlsx
   Produces:    <step2_output>_unique_authors.xlsx

4. Run:         python fetch_affiliations.py <step3_output>.xlsx
   Produces:    <step3_output>_with_affiliations.xlsx
```

## Troubleshooting

| Problem | Solution |
|---|---|
| Many `[No affiliation found]` results | This is expected — not all publishers submit affiliation data to Crossref, and OpenAlex coverage varies. These authors may need manual lookup. |
| `Affiliation_Source` shows `None` | Neither Crossref nor OpenAlex had affiliation data for this author on any of their DOIs. |
| Rate limit errors | Increase the `delay` value in `config.ini`. Crossref polite pool allows ~50 req/s; OpenAlex allows ~10 req/s. |
| Script interrupted mid-run | Just re-run. Caches ensure no work is lost. |
| Wrong affiliation matched | The script matches by name, which can occasionally match the wrong person if two co-authors on the same paper share a family name. Check the `Name_Variants` column. |

## Limitations

- **Affiliation coverage varies by publisher.** Some publishers (e.g., Elsevier, Springer) consistently provide affiliations; others do not.
- **Affiliations are point-in-time.** They reflect where the author was affiliated when the paper was published, not necessarily their current institution.
- **Multiple affiliations.** If an author has different affiliations across different papers, all are collected and deduplicated. The output does not indicate which affiliation goes with which DOI.
- **Name matching.** Matching is initial-aware and handles common formatting differences, but cannot distinguish two different people with the same name on the same paper.
- **No affiliation found** - Manually search for the affiliations that could not be found
## License

This script is provided as-is for research and data management purposes.
