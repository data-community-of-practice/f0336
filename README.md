# Affiliation Fetch (Crossref → ORCID → LLM)

A Python script that fetches author affiliations (organisations/institutions) using a three-tier strategy, with early exit optimisation — it stops searching as soon as an affiliation is found.

## How it works

For each unique author, the script loops through their DOIs and name variants. As soon as an affiliation is found on any DOI for any name variant, it stops and moves to the next author.

### Tier 1 — Crossref (no key needed)
For each DOI, fetches the full author metadata from the Crossref API. Matches the author by name (using canonical name and all variants) and extracts their listed affiliation(s). Also collects ORCID IDs when publishers have included them.

**Early exit:** Once an affiliation is found on a DOI, the remaining DOIs for that author are skipped.

### Tier 2 — ORCID direct lookup (no key needed)
For authors where Crossref had no affiliation but did provide an ORCID ID, queries the author's public ORCID profile at `pub.orcid.org/v3.0/{id}/employments` to fetch their employment history. This is a direct public lookup — no credentials, no registration, no token needed.

Authors without an ORCID ID in Crossref skip straight to Tier 3.

### Tier 3 — LLM (optional, needs Anthropic API key)
For remaining authors, uses Claude with `temperature=0` to infer the most likely institutional affiliation from the author's name, variants, and DOIs. Returns a confidence level (high/medium/low). All results are cached for reproducibility.

## Requirements

- Python 3.7+
- Libraries: `openpyxl`, `requests`

```bash
pip install openpyxl requests
```

No API keys required for Tiers 1 and 2. Tier 3 (LLM) is optional and needs an Anthropic API key.

## Setup

Uses the same `config.ini` as the other scripts:

```ini
[crossref]
email = yourname@example.com
delay = 1
save_every = 50
max_retries = 3

[anthropic]
api_key = sk-ant-your-key-here
```

The `[anthropic]` section is optional. Without it, Tiers 1 and 2 still run.

## Input file format

The script expects the output of `extract_unique_authors.py`:

| Column | Required | Description |
|---|---|---|
| `Author_Name` | Yes | The canonical author name |
| `Name_Variants` | No | Semicolon-separated name variants (used for matching) |
| `DOI_Count` | No | Number of DOIs (carried through to output) |
| `DOIs` | Yes | Semicolon-separated DOIs to look up |

## Usage

### From a terminal

```bash
python f0336.py unique_authors.xlsx
```

### From Spyder

```python
!python "E:\your\folder\f0336.py" "E:\your\folder\unique_authors.xlsx"
```

## Output

The script produces `<input_name>_with_affiliations.xlsx` with six columns:

| Column | Description |
|---|---|
| `Author_Name` | Canonical author name |
| `Name_Variants` | Other name forms for this author |
| `Affiliations` | Semicolon-separated organisation/institution names |
| `Affiliation_Source` | Where the data came from: `Crossref`, `ORCID`, `LLM (high/medium/low)`, or `None` |
| `DOI_Count` | Number of DOIs associated with this author |
| `DOIs` | All associated DOIs |

## How matching works

For each author, the script builds a set of all name forms (canonical + variants). When checking a DOI's Crossref response, it compares each name form against the authors listed on that paper using initial-aware matching (e.g., "S" matches "Susan", "SL" matches "Susan L."). The first match with affiliations wins and the remaining DOIs are skipped.

## Cache files

| File | Purpose |
|---|---|
| `<input>_crossref_doi_cache.json` | Crossref author metadata per DOI |
| `<input>_orcid_cache.json` | ORCID employment data per author |
| `<input>_llm_aff_cache.json` | LLM-inferred affiliations per author |
| `<input>_affiliation_cache.json` | Final resolved affiliations per author |

Re-running skips all cached results. Delete a specific cache file to force re-processing for that tier.

## Full pipeline

This script is step 3 in the pipeline:

```
1. python crossref_author_fetch.py <input>.xlsx
     → adds Crossref_Authors column

2. python extract_unique_authors.py <step1_output>.xlsx
     → one row per unique author with DOIs

3. python fetch_affiliations.py <step2_output>.xlsx
     → adds Affiliations column (Crossref → ORCID → LLM)

4. python extract_unique_orgs.py <step3_output>.xlsx
     → one row per unique institution with ROR ID

5. python classify_orgs.py <step4_output>.xlsx
     → adds Classification (Research/Health/Government/Industry)

6. python geotag_orgs.py <step5_output>.xlsx
     → adds coordinates + city/country + generates interactive map
```

## Troubleshooting

| Problem | Solution |
|---|---|
| Many `[No affiliation found]` | Not all publishers submit affiliations to Crossref, and not all authors have ORCID profiles. Add an Anthropic API key to enable LLM inference for remaining authors. |
| `Affiliation_Source` shows `None` | No tier found affiliation data. These authors may need manual lookup. |
| `Affiliation_Source` shows `LLM (low)` | The LLM had low confidence in its inference. Verify manually. |
| Rate limit errors | Increase the `delay` value in `config.ini`. |
| Script interrupted mid-run | Just re-run. All four caches ensure no work is lost. |
| Wrong affiliation matched | Name matching can occasionally match the wrong co-author if two people on the same paper share a family name. Check the `Name_Variants` column. |

## Limitations

- **Affiliation coverage varies by publisher.** Some publishers consistently provide affiliations in Crossref; others do not.
- **Affiliations are point-in-time.** They reflect where the author was affiliated when the paper was published, not necessarily their current institution. ORCID employments may be more current.
- **ORCID coverage.** Only authors who have both registered an ORCID profile and had their ID included by the publisher in Crossref metadata will benefit from Tier 2.
- **LLM inference.** Claude's affiliation guesses are based on its training data and are not guaranteed to be correct. The confidence level helps gauge reliability.
- **Early exit trade-off.** The script takes the first affiliation found and stops. If an author changed institutions between papers, only the affiliation from the first matching DOI is captured.

## License

This script is provided as-is for research and data management purposes.
