# f0335 — Crossref Author Normaliser & Merger

Reads `Crossref_AuthorMetadata.json` (produced by [f0334](https://github.com/data-community-of-practice/f0334)) and deduplicates authors into a clean researcher list, outputting `Normalised_Authors.json`.

## What it does

**Auto-merges** author appearances when confident:

1. Same ORCID → always merge
2. Surname-first fix: `"Doe"` / `"Jane L."` → flip, then compare
3. No ORCID, names identical after stripping periods/spaces:
   - `"Jane L. Doe"` = `"Jane L Doe"`
   - `"J.L. Doe"` = `"J L Doe"` = `"JL Doe"`
   - `"John M. Smith"` = `"John M Smith"`
   - Full words must match exactly — initials only match other initials.

**Flags as similar** (for manual review):

4. Initial vs full name: `"J.L. Doe"` ~ `"Jane L. Doe"`
5. Missing name parts: `"Jane Doe"` ~ `"Jane L. Doe"`
6. Spelling variants: `"John Mathew Smith"` ~ `"John Matthew Smith"`
7. Possible surname-first: `"Doe Jane"` ~ `"Jane Doe"`

## Requirements

- Python 3.8+
- No third-party dependencies

## Usage

```bash
python f0335.py [Crossref_AuthorMetadata.json] [--output Normalised_Authors.json]
```

**Examples:**

```bash
# Use defaults (reads Crossref_AuthorMetadata.json, writes Normalised_Authors.json)
python f0335.py

# Explicit paths
python f0335.py my_metadata.json --output my_normalised.json
```

### Input

`Crossref_AuthorMetadata.json` — the JSON array produced by `f0334.py`. Each element must have a `"doi"`, `"title"`, and `"authors"` list where each author has `"given"`, `"family"`, optional `"orcid"`, and `"affiliations"`.

### Output

`Normalised_Authors.json` — a JSON array of deduplicated researcher objects:

```json
[
  {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "given": "Jane",
    "family": "Doe",
    "full_name": "Jane Doe",
    "orcid": "0000-0000-0000-0001",
    "name_variants": ["Jane Doe", "Jane L. Doe", "J. Doe"],
    "publications": [
      { "doi": "10.1234/example", "title": "Example Publication Title" }
    ],
    "affiliations": [
      {
        "name": "Example University",
        "ror_id": "https://ror.org/00000000",
        "place": "Melbourne, Australia"
      }
    ],
    "similar_to": [
      {
        "id": "661f9511-f30c-52e5-b827-557766551111",
        "full_name": "J. Doe",
        "orcid": null
      }
    ],
    "merge_confidence": "orcid"
  }
]
```

| Field | Description |
|---|---|
| `id` | Stable UUID for this researcher |
| `given` / `family` / `full_name` | Best available name |
| `orcid` | ORCID if any appearance had one |
| `name_variants` | All distinct name strings seen across appearances |
| `publications` | DOIs (and titles) this researcher is linked to |
| `affiliations` | Deduplicated affiliations across all appearances |
| `similar_to` | Researcher IDs flagged for manual review |
| `merge_confidence` | `"orcid"` if merged by ORCID, `"name"` otherwise |

## Processing steps

| Step | Action |
|---|---|
| A | Detect and fix surname-first entries (e.g. `"Doe" / "Jane L."` → swap) |
| B | Merge all appearances sharing the same ORCID |
| C | Merge remaining appearances by normalised name key |
| D | Build one researcher node per cluster |
| E | Detect similar pairs across clusters for manual review |

## Output statistics

At the end of each run the script prints a summary:

```
============================================================
NORMALISATION SUMMARY
============================================================
Input appearances:          704
Deduplicated researchers:   521
  With ORCID:               172
  Without ORCID:            349
  With affiliations:        210
  With ROR ID:              180
  Multi-publication:        85
  Merged name variants:     42
  Flagged similar:          18 (12 pairs)
```
