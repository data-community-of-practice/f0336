# f0336 — ORCID Enrichment via ORCID Public API

Reads a researcher JSON (e.g., `Normalised_Authors.json` from [f0335](https://github.com/data-community-of-practice/f0335) or `Resolved_Authors.json` from [f0335a](https://github.com/data-community-of-practice/f0335a)) and:

1. Searches the ORCID public API to find ORCID IDs for researchers that don't have one.
2. Fetches full affiliation details (employment + education) from each newly found ORCID profile.
3. Backfills affiliations for researchers who already had an ORCID but no affiliation data.

No API key required — uses the ORCID public API only.

## Requirements

- Python 3.8+
- [requests](https://pypi.org/project/requests/)

```bash
pip install requests
```

## Usage

```bash
python f0336.py [input.json] [--output output.json] [--dry-run]
```

**Examples:**

```bash
# Uses Normalised_Authors.json by default, overwrites in place
python f0336.py

# Explicit input, write to a new file
python f0336.py Resolved_Authors.json --output ORCID_Enriched_Authors.json

# Preview which researchers will be searched (no API calls)
python f0336.py Resolved_Authors.json --dry-run
```

## Search strategy

For each researcher without an ORCID, the following strategies are tried in order:

| Step | Query | Accepted when |
|---|---|---|
| 1 | `given-names + family-name + affiliation-org-name` | Exactly 1 result, OR 2–5 results with an affiliation match |
| 2 | `given-names + family-name` | Exactly 1 result, OR 2–5 results with an affiliation match |
| 3 | Each name variant (given + family) | Exactly 1 result, OR 2–5 results with an affiliation match |

When multiple results are returned (2–5), the script fetches each candidate's ORCID employment record and checks for affiliation overlap (Jaccard token similarity ≥ 0.4). Results with 6 or more candidates are rejected as too ambiguous.

## Affiliation enrichment

Whenever an ORCID is found (or already existed) and the researcher has no affiliations, the script fetches the full ORCID profile (employments + educations) and merges the structured affiliation data into the record:

```json
{
  "name": "Swinburne University of Technology",
  "source": "orcid",
  "place": "Hawthorn, AU",
  "ror": "https://ror.org/031rekg67",
  "department": "Centre for Astrophysics and Supercomputing",
  "role": "Research Fellow"
}
```

Affiliations are deduplicated by name before merging.

## Caching

Search results and fetched affiliation records are saved to `orcid_search_cache.json` alongside the input file. Re-runs skip any query already in the cache, making interrupted runs safely resumable.

## Output

The output file has the same schema as the input, with two fields updated where applicable:

- `orcid` — filled with the found ORCID iD
- `affiliations` — extended with structured data from the ORCID profile

## Output statistics

```
=======================================================
ORCID LOOKUP SUMMARY
=======================================================
ORCID IDs found:         23/182
    via name+affiliation:  14
    via name_only:         7
    via name_variant:      2
  Not found:             159
Affiliations filled:     31
ORCIDs: 81 -> 104 / 263 researchers
Affiliations: 198 / 263 researchers
=======================================================
```
