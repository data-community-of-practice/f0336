# f0335a — LLM-Assisted Author Resolution

Reads `Normalised_Authors.json` (produced by [f0335](https://github.com/data-community-of-practice/f0335)), uses **Claude Haiku** to adjudicate every `similar_to` pair, merges confirmed duplicates, and writes `Resolved_Authors.json` and `LLM_Verdicts.json`.

## What it does

1. Extracts all unique `similar_to` pairs from the normalised author list.
2. Sends each pair to Claude Haiku with a structured prompt covering names, ORCID, affiliations, and shared publications.
3. Parses the verdict (`same_person`, `confidence`, `reasoning`).
4. Merges pairs where the LLM says `same_person: true` and confidence meets the threshold.
5. Saves all verdicts to a cache file (`LLM_Verdicts.json`) so re-runs only call the API for new pairs.

## Requirements

- Python 3.8+ (standard library only — no third-party packages)
- Anthropic API key

## API key

Set via environment variable:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Or via `config.ini`:

```ini
[anthropic]
api_key = sk-ant-...
```

> **Note:** `config.ini` is listed in `.gitignore` and must never be committed.

## Usage

```bash
python f0335a.py [Normalised_Authors.json] [--output Resolved_Authors.json] [options]
```

### Options

| Flag | Default | Description |
|---|---|---|
| `input_json` | `Normalised_Authors.json` | Input from f0335 |
| `--output`, `-o` | `Resolved_Authors.json` | Output file |
| `--config`, `-c` | — | Path to `config.ini` for API key |
| `--merge-threshold` | `medium` | Minimum confidence to merge: `low`, `medium`, `high` |
| `--dry-run` | — | List pairs without calling the API |
| `--clear-cache` | — | Delete `LLM_Verdicts.json` and re-evaluate all pairs |

### Examples

```bash
# Default run
python f0335a.py

# Explicit paths
python f0335a.py my_normalised.json --output my_resolved.json

# Only merge high-confidence verdicts
python f0335a.py --merge-threshold high

# Preview pairs without API calls
python f0335a.py --dry-run

# Force re-evaluation of all pairs
python f0335a.py --clear-cache
```

## Output

### `Resolved_Authors.json`

Same schema as `Normalised_Authors.json` but with confirmed duplicates merged. Merged researchers get `"merge_confidence": "llm_verified"`. The `similar_to` field is cleaned up to remove references to merged-away records.

### `LLM_Verdicts.json`

One entry per evaluated pair:

```json
[
  {
    "id1": "550e8400-...",
    "id2": "661f9511-...",
    "name1": "Philip J. Sumner",
    "name2": "Philip Sumner",
    "same_person": true,
    "confidence": "high",
    "reasoning": "Shared affiliation and publication; name difference is middle initial only.",
    "raw_response": "..."
  }
]
```

## Merge logic

- **Primary record selection:** prefers the record with an ORCID; on a tie, the one with more publications.
- **Merge behaviour:** longer given name wins; publications, affiliations, and name variants are unioned (deduplicated).
- **Transitive chains:** if A→B and B→C are both confirmed, all three are merged into one record.
- **Threshold:** pairs below the `--merge-threshold` confidence level are kept as `similar_to` references, not merged.

## Output statistics

```
============================================================
  SUMMARY
============================================================
  Pairs reviewed:        18
  Same person verdicts:  11
  Different verdicts:    7
  Merges applied:        11
  Researchers before:    521
  Researchers after:     510
============================================================

  Output: Resolved_Authors.json
  Verdicts: LLM_Verdicts.json
```
