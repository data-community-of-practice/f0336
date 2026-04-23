# f0336
# Fetch Missing Affiliations from ORCID

A Python script that enriches the researcher list produced by [f0335a](https://github.com/data-community-of-practice/f0335a) by fetching employment and education history from the ORCID public API for researchers who have an ORCID identifier but no affiliation data.

No API key is required — ORCID's public API is freely accessible.

## Pipeline context

```
f0334  →  Crossref_AuthorMetadata.json
f0335  →  Normalised_Authors.json
f0335a →  Resolved_Authors.json
f0336  →  Authors_With_Affiliations.json    (affiliations filled in via ORCID)
```

Crossref metadata does not always include author affiliations, even when an ORCID is present. This step uses the ORCID ID itself — where one exists — to look up the researcher's public profile directly and extract their employment and education records.

## How it works

For each researcher in the input who has an ORCID but no affiliations (or all researchers if `--overwrite` is set), the script:

1. Calls `https://pub.orcid.org/v3.0/{orcid}/record` to fetch the full public profile.
2. Extracts affiliations from both the **employments** and **educations** sections of the profile.
3. Deduplicates by normalised organisation name and merges the results into the researcher's existing `affiliations` list.
4. Caches the raw ORCID response in `orcid_affiliation_cache.json` — re-runs skip already-fetched ORCIDs.

Researchers without an ORCID ID are not processed and pass through unchanged.

## Output

One file: **`Authors_With_Affiliations.json`** — the same structure as `Resolved_Authors.json`, with the `affiliations` list enriched for eligible researchers.

### Affiliation fields

Affiliations sourced from ORCID may include fields not present in Crossref-sourced affiliations:

| Field       | Source | Description |
|-------------|--------|-------------|
| `name`      | Always | Organisation name. |
| `place`     | When available | City and/or country (e.g. `"Melbourne, AU"`). |
| `ror`       | When available | ROR identifier, if the organisation is disambiguated as ROR in ORCID. |
| `grid`      | When available | GRID identifier, if disambiguated as GRID. |
| `ringgold`  | When available | Ringgold identifier, if disambiguated as Ringgold. |
| `department`| When available | Department or faculty name within the organisation. |
| `role`      | When available | Job title or role at the organisation. |

### Example affiliation object

```json
{
  "name": "University of Melbourne",
  "place": "Melbourne, AU",
  "ror": "https://ror.org/01ej9dk98",
  "department": "School of Computing and Information Systems",
  "role": "Associate Professor"
}
```

## Requirements

- Python 3.7+
- Library: `requests`

```bash
pip install requests
```

No API key or registration required.

## Usage

```bash
python f0336.py
```

By default, reads `Resolved_Authors.json` from the current directory (then the script's directory) and writes `Authors_With_Affiliations.json` alongside it.

Specify paths explicitly:

```bash
python f0336.py path/to/Resolved_Authors.json --output path/to/Authors_With_Affiliations.json
```

### Options

| Option | Description |
|--------|-------------|
| `input_json` | Path to input JSON (default: `Resolved_Authors.json`). |
| `--output`, `-o` | Path for the output file (default: `Authors_With_Affiliations.json`). |
| `--overwrite` | Fetch ORCID affiliations even for researchers who already have affiliations. Useful to enrich existing data with department/role information. |

### From Spyder or Jupyter

```python
!python "E:\your\folder\f0336.py"
```

## Cache

Results are cached in `orcid_affiliation_cache.json` in the same folder as the input. Each entry maps an ORCID identifier to the list of affiliations extracted from that profile. Re-running the script skips all previously fetched ORCIDs.

To force a fresh fetch for all researchers, delete `orcid_affiliation_cache.json` before running.

## Console output

```
Input:  /path/to/Resolved_Authors.json
Output: /path/to/Authors_With_Affiliations.json
Cache:  /path/to/orcid_affiliation_cache.json

ORCID cache: 143 entries

Total researchers:              2075
  With ORCID:                   891
  With affiliations:            612
  Need lookup (ORCID, no aff):  279

Fetching affiliations from ORCID API...
  [1/279] Jane Louise Doe (0000-0001-2345-6789) -> 2 affiliations
  [2/279] John M. Smith (0000-0002-3456-7890) -> no affiliations in ORCID profile
  [3/279] Wei Zhang (0000-0003-4567-8901) -> ORCID record not found
  ...

=======================================================
AFFILIATION FETCH SUMMARY
=======================================================
Researchers processed:       279
  From cache:                143
  API calls:                 136
  Affiliations found:        198
Before: 612 researchers with affiliations
After:  810 researchers with affiliations
Gained: 198
=======================================================

Saved: /path/to/Authors_With_Affiliations.json
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ERROR: pip install requests` | Run `pip install requests` and retry. |
| `no affiliations in ORCID profile` | The researcher has an ORCID but has not added employment or education records to their public profile. |
| `ORCID record not found` | The ORCID ID may be invalid, deactivated, or set to private. |
| Rate limit errors | The script waits 0.3 s between requests. If errors persist, the ORCID API may be temporarily overloaded — wait and re-run (cached results are preserved). |
| Want to add department/role to already-affiliated researchers | Use `--overwrite` to re-fetch ORCID data for all researchers with an ORCID, not just those missing affiliations. |

## Limitations

- Only researchers with an ORCID identifier are eligible for lookup.
- ORCID profiles are self-reported and may be incomplete, out of date, or set to private by the researcher.
- The script fetches all employments and educations without date filtering — researchers who have changed institutions over their career will have multiple affiliations listed.

## License

This script is provided as-is for research and data management purposes.
