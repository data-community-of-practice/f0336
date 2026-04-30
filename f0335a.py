#!/usr/bin/env python3
"""
resolve_similar_authors.py

Reads Normalised_Authors.json, finds all "similar_to" pairs, and uses
Claude Haiku via the Anthropic Messages API to determine if each pair
refers to the same person. Merges confirmed duplicates and writes
Resolved_Authors.json and LLM_Verdicts.json.
"""

import argparse
import configparser
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL = "claude-haiku-4-5-20251001"
API_URL = "https://api.anthropic.com/v1/messages"
VERDICTS_FILE = "LLM_Verdicts.json"
CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
RATE_LIMIT_DELAY = 0.5
MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

def get_api_key(config_path=None):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    if config_path and os.path.isfile(config_path):
        cfg = configparser.ConfigParser()
        cfg.read(config_path)
        if cfg.has_option("anthropic", "api_key"):
            return cfg.get("anthropic", "api_key")
    return None


# ---------------------------------------------------------------------------
# Pair extraction
# ---------------------------------------------------------------------------

def _resolve_similar_id(entry):
    """Extract a plain ID string from a similar_to entry.
    Handles both plain strings and dicts like {"id": "...", ...}."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("id") or entry.get("ID") or entry.get("author_id")
    return None


def extract_pairs(authors):
    """Extract unique (id1, id2) pairs from similar_to arrays, sorted."""
    pairs = set()
    author_map = {a["id"]: a for a in authors}
    for author in authors:
        for entry in author.get("similar_to", []):
            other_id = _resolve_similar_id(entry)
            if other_id and other_id in author_map:
                pair = tuple(sorted([author["id"], other_id]))
                pairs.add(pair)
    return sorted(pairs)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_prompt(a, b):
    """Build the comparison prompt for two researcher records."""

    def researcher_block(r):
        lines = []
        lines.append(f"  Name: {r.get('full_name', 'N/A')}")
        variants = r.get("name_variants", [])
        if variants:
            # Filter out variants identical to full_name
            unique_variants = [v for v in variants if v != r.get("full_name")]
            if unique_variants:
                lines.append(f"  Name variants: {', '.join(unique_variants)}")
        orcid = r.get("orcid")
        if orcid:
            lines.append(f"  ORCID: {orcid}")
        affiliations = r.get("affiliations", [])
        if affiliations:
            aff_strs = []
            for aff in affiliations:
                if isinstance(aff, dict):
                    aff_strs.append(aff.get("name", str(aff)))
                else:
                    aff_strs.append(str(aff))
            lines.append(f"  Affiliations: {'; '.join(aff_strs)}")
        return lines, affiliations

    lines_a, affs_a = researcher_block(a)
    lines_b, affs_b = researcher_block(b)

    # Only include publication titles if BOTH researchers have no affiliations
    if not affs_a and not affs_b:
        for label, r, lines in [("A", a, lines_a), ("B", b, lines_b)]:
            pubs = r.get("publications", [])
            if pubs:
                titles = []
                for p in pubs[:10]:
                    if isinstance(p, dict):
                        t = p.get("title", "")
                    else:
                        t = str(p)
                    if t:
                        titles.append(t)
                if titles:
                    lines.append(f"  Publication titles: {'; '.join(titles)}")

    # Detect shared DOIs to highlight in prompt
    dois_a = set()
    for p in a.get("publications", []):
        if isinstance(p, dict) and (p.get("doi") or p.get("DOI")):
            dois_a.add((p.get("doi") or p.get("DOI")).lower())
    dois_b = set()
    for p in b.get("publications", []):
        if isinstance(p, dict) and (p.get("doi") or p.get("DOI")):
            dois_b.add((p.get("doi") or p.get("DOI")).lower())
    shared_dois = dois_a & dois_b

    shared_note = ""
    if shared_dois:
        shared_note = (
            f"\nNote: These two researchers share {len(shared_dois)} "
            f"publication(s) with identical DOIs. "
            "Co-authored papers with the same DOI are very strong evidence "
            "they are listed as the same contributor under variant names.\n"
        )

    prompt = (
        "You are an expert at researcher identity resolution. "
        "Determine whether the two researchers below are the same person.\n\n"
        "Key principles:\n"
        "1. A shorter name is very often the same person dropping a middle name "
        "or using initials (e.g. 'Philip J. Sumner' and 'Philip Sumner' and "
        "'PJ Sumner' are likely the same person).\n"
        "2. Shared affiliation (same department, same university) is very strong "
        "evidence of being the same person.\n"
        "3. If one has an ORCID and the other does not, and names plus other "
        "signals are compatible, they are very likely the same person.\n"
        "4. Shared publications (same DOI appearing under both names) is very "
        "strong evidence that the two names refer to the same contributor.\n\n"
        "Researcher A:\n" + "\n".join(lines_a) + "\n\n"
        "Researcher B:\n" + "\n".join(lines_b) + "\n"
        + shared_note + "\n"
        "Respond with ONLY a JSON object (no other text):\n"
        '{"same_person": true/false, "confidence": "high"/"medium"/"low", '
        '"reasoning": "brief explanation"}'
    )
    return prompt


# ---------------------------------------------------------------------------
# API call with retries
# ---------------------------------------------------------------------------

def call_haiku(api_key, prompt):
    """Call Claude Haiku and return the raw text response."""
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}]
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    for attempt in range(MAX_RETRIES):
        try:
            req = urllib.request.Request(API_URL, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text_parts = [
                    block.get("text", "")
                    for block in data.get("content", [])
                    if block.get("type") == "text"
                ]
                return "".join(text_parts)
        except urllib.error.HTTPError as e:
            status = e.code
            if status in (429, 529):
                wait = (2 ** attempt) * 5
                print(f"    Rate limited ({status}), waiting {wait}s...")
                time.sleep(wait)
            elif status >= 500:
                wait = 2 ** attempt
                print(f"    Server error ({status}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                body = e.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"API error {status}: {body}")
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** attempt
                print(f"    Error: {e}, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise

    raise RuntimeError("Max retries exceeded for API call")


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_response(text):
    """Parse the LLM response into a verdict dict."""
    # Try 1: direct JSON parse
    try:
        obj = json.loads(text.strip())
        if "same_person" in obj:
            return _normalise_verdict(obj)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try 2: extract from markdown code block
    md_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if md_match:
        try:
            obj = json.loads(md_match.group(1))
            if "same_person" in obj:
                return _normalise_verdict(obj)
        except (json.JSONDecodeError, TypeError):
            pass

    # Try 3: regex for JSON object containing same_person
    json_match = re.search(r"\{[^{}]*\"same_person\"[^{}]*\}", text, re.DOTALL)
    if json_match:
        try:
            obj = json.loads(json_match.group(0))
            return _normalise_verdict(obj)
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: infer from text
    lower = text.lower()
    same = None
    if "same person" in lower and ("not the same" not in lower and "different" not in lower):
        same = True
    elif "not the same" in lower or "different person" in lower or "different people" in lower:
        same = False
    elif "same_person\": true" in lower or "same_person\":true" in lower:
        same = True
    elif "same_person\": false" in lower or "same_person\":false" in lower:
        same = False

    if same is not None:
        return {
            "same_person": same,
            "confidence": "low",
            "reasoning": "Inferred from unstructured response",
        }

    return {
        "same_person": False,
        "confidence": "low",
        "reasoning": "Could not parse response; defaulting to different",
    }


def _normalise_verdict(obj):
    """Ensure verdict has correct types."""
    sp = obj.get("same_person")
    if isinstance(sp, str):
        sp = sp.lower() in ("true", "yes", "1")
    obj["same_person"] = bool(sp)
    conf = str(obj.get("confidence", "low")).lower()
    if conf not in CONFIDENCE_ORDER:
        conf = "low"
    obj["confidence"] = conf
    obj["reasoning"] = str(obj.get("reasoning", ""))
    return obj


# ---------------------------------------------------------------------------
# Verdict caching
# ---------------------------------------------------------------------------

def load_verdicts_cache():
    if os.path.isfile(VERDICTS_FILE):
        with open(VERDICTS_FILE, "r", encoding="utf-8") as f:
            verdicts_list = json.load(f)
        cache = {}
        for v in verdicts_list:
            key = tuple(sorted([v["id1"], v["id2"]]))
            cache[key] = v
        return cache
    return {}


def save_verdicts(cache):
    verdicts_list = sorted(cache.values(), key=lambda v: (v["id1"], v["id2"]))
    with open(VERDICTS_FILE, "w", encoding="utf-8") as f:
        json.dump(verdicts_list, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Merging logic
# ---------------------------------------------------------------------------

def meets_threshold(confidence, threshold):
    return CONFIDENCE_ORDER.get(confidence, 0) >= CONFIDENCE_ORDER.get(threshold, 1)


def resolve_merge_chains(merge_map):
    """Given {loser_id: winner_id}, resolve transitive chains so every
    loser points to the final winner."""
    def find_root(node):
        visited = set()
        while node in merge_map and node not in visited:
            visited.add(node)
            node = merge_map[node]
        return node

    resolved = {}
    for loser in list(merge_map.keys()):
        resolved[loser] = find_root(loser)
    return resolved


def pick_primary(a, b):
    """Decide which record is the primary (keeper). Returns (primary, secondary)."""
    a_has_orcid = bool(a.get("orcid"))
    b_has_orcid = bool(b.get("orcid"))
    if a_has_orcid and not b_has_orcid:
        return a, b
    if b_has_orcid and not a_has_orcid:
        return b, a
    # Both or neither have ORCID: keep the one with more publications
    a_pubs = len(a.get("publications", []))
    b_pubs = len(b.get("publications", []))
    if a_pubs >= b_pubs:
        return a, b
    return b, a


def merge_records(primary, secondary):
    """Merge secondary into primary in place."""
    # Keep longer given name
    pg = primary.get("given", "") or ""
    sg = secondary.get("given", "") or ""
    if len(sg) > len(pg):
        primary["given"] = sg
        # Update full_name
        primary["full_name"] = f"{sg} {primary.get('family', '')}".strip()

    # ORCID
    if not primary.get("orcid") and secondary.get("orcid"):
        primary["orcid"] = secondary["orcid"]

    # Name variants
    existing_variants = set(primary.get("name_variants", []))
    for v in secondary.get("name_variants", []):
        existing_variants.add(v)
    # Also add the secondary's full name as a variant
    sec_name = secondary.get("full_name", "")
    if sec_name:
        existing_variants.add(sec_name)
    # Remove primary's own full_name from variants
    existing_variants.discard(primary.get("full_name", ""))
    primary["name_variants"] = sorted(existing_variants)

    # Publications (deduplicate by DOI)
    existing_dois = set()
    merged_pubs = []
    for p in primary.get("publications", []):
        doi = None
        if isinstance(p, dict):
            doi = p.get("doi") or p.get("DOI")
        if doi:
            existing_dois.add(doi.lower())
        merged_pubs.append(p)
    for p in secondary.get("publications", []):
        doi = None
        if isinstance(p, dict):
            doi = p.get("doi") or p.get("DOI")
        if doi and doi.lower() in existing_dois:
            continue
        if doi:
            existing_dois.add(doi.lower())
        merged_pubs.append(p)
    primary["publications"] = merged_pubs

    # Affiliations (deduplicate by name)
    existing_aff_names = set()
    merged_affs = []
    for aff in primary.get("affiliations", []):
        name = aff.get("name", str(aff)) if isinstance(aff, dict) else str(aff)
        if name.lower() not in existing_aff_names:
            existing_aff_names.add(name.lower())
            merged_affs.append(aff)
    for aff in secondary.get("affiliations", []):
        name = aff.get("name", str(aff)) if isinstance(aff, dict) else str(aff)
        if name.lower() not in existing_aff_names:
            existing_aff_names.add(name.lower())
            merged_affs.append(aff)
    primary["affiliations"] = merged_affs

    # Mark as LLM-verified
    primary["merge_confidence"] = "llm_verified"

    return primary


def apply_merges(authors, verdicts_cache, threshold):
    """Apply all merges and return the cleaned author list."""
    author_map = {a["id"]: a for a in authors}

    # Collect merge pairs
    merge_pairs = []  # (loser_id, winner_id)
    for key, verdict in verdicts_cache.items():
        if not verdict.get("same_person"):
            continue
        if not meets_threshold(verdict.get("confidence", "low"), threshold):
            continue
        id1, id2 = key
        if id1 not in author_map or id2 not in author_map:
            continue
        primary, secondary = pick_primary(author_map[id1], author_map[id2])
        merge_pairs.append((secondary["id"], primary["id"]))

    if not merge_pairs:
        return authors, 0

    # Build merge_map and resolve chains
    merge_map = {loser: winner for loser, winner in merge_pairs}
    merge_map = resolve_merge_chains(merge_map)

    # Group losers by their final winner
    winner_losers = {}
    for loser, winner in merge_map.items():
        winner_losers.setdefault(winner, []).append(loser)

    # Perform merges
    merges_applied = 0
    for winner_id, loser_ids in winner_losers.items():
        if winner_id not in author_map:
            continue
        for loser_id in loser_ids:
            if loser_id not in author_map:
                continue
            merge_records(author_map[winner_id], author_map[loser_id])
            merges_applied += 1

    # Collect IDs to remove
    removed_ids = set(merge_map.keys())

    # Build output list, cleaning up similar_to references
    result = []
    for a in authors:
        if a["id"] in removed_ids:
            continue
        # Clean similar_to: remove references to merged-away IDs and
        # remap to winners where appropriate
        new_similar = []
        seen = set()
        for entry in a.get("similar_to", []):
            sid = _resolve_similar_id(entry)
            if sid is None:
                continue
            resolved = merge_map.get(sid, sid)
            if resolved == a["id"]:
                continue
            if resolved in removed_ids:
                continue
            if resolved not in seen:
                seen.add(resolved)
                # Preserve original format: if entry was a dict, update
                # its id; if it was a string, use the resolved string
                if isinstance(entry, dict):
                    updated = dict(entry)
                    id_key = "id" if "id" in entry else ("ID" if "ID" in entry else "author_id")
                    updated[id_key] = resolved
                    new_similar.append(updated)
                else:
                    new_similar.append(resolved)
        a["similar_to"] = new_similar
        result.append(a)

    return result, merges_applied


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Resolve similar author pairs using Claude Haiku."
    )
    parser.add_argument(
        "input_json", nargs="?", default="Normalised_Authors.json",
        help="Input JSON file (default: Normalised_Authors.json)"
    )
    parser.add_argument(
        "--output", "-o", default="Resolved_Authors.json",
        help="Output JSON file (default: Resolved_Authors.json)"
    )
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to config.ini for API key"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show pairs without calling the API"
    )
    parser.add_argument(
        "--merge-threshold", choices=["high", "medium", "low"],
        default="medium",
        help="Minimum confidence to merge (default: medium)"
    )
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Delete verdict cache and re-evaluate all pairs"
    )
    args = parser.parse_args()

    # Load input
    if not os.path.isfile(args.input_json):
        print(f"ERROR: Input file not found: {args.input_json}")
        sys.exit(1)

    with open(args.input_json, "r", encoding="utf-8") as f:
        authors = json.load(f)

    print(f"Loaded {len(authors)} researchers from {args.input_json}")

    # Extract pairs
    pairs = extract_pairs(authors)
    print(f"Found {len(pairs)} unique similar_to pairs")

    if not pairs:
        print("No pairs to resolve. Writing output as-is.")
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(authors, f, indent=2, ensure_ascii=False)
        print(f"Saved {args.output}")
        return

    # Handle cache
    if args.clear_cache and os.path.isfile(VERDICTS_FILE):
        os.remove(VERDICTS_FILE)
        print("Cleared verdict cache.")

    verdicts_cache = {} if args.clear_cache else load_verdicts_cache()
    cached_count = sum(1 for p in pairs if p in verdicts_cache)
    if cached_count:
        print(f"Found {cached_count} cached verdicts, {len(pairs) - cached_count} to evaluate")

    # Dry run
    if args.dry_run:
        author_map = {a["id"]: a for a in authors}
        print("\n-- DRY RUN: pairs to evaluate --")
        for i, (id1, id2) in enumerate(pairs, 1):
            a = author_map.get(id1, {})
            b = author_map.get(id2, {})
            cached = "(cached)" if (id1, id2) in verdicts_cache else "(pending)"
            print(f"  [{i}/{len(pairs)}] {a.get('full_name', id1)} <-> {b.get('full_name', id2)} {cached}")
        print("\nDry run complete. No API calls made.")
        return

    # Resolve API key (defer hard failure until we actually need it)
    api_key = get_api_key(args.config)
    uncached_count = sum(1 for p in pairs if p not in verdicts_cache)
    if not api_key and uncached_count > 0:
        print("ERROR: No API key found. Set ANTHROPIC_API_KEY or provide --config.")
        sys.exit(1)

    # Process pairs
    author_map = {a["id"]: a for a in authors}
    same_count = 0
    diff_count = 0
    new_calls = 0

    for i, (id1, id2) in enumerate(pairs, 1):
        a = author_map.get(id1)
        b = author_map.get(id2)
        if not a or not b:
            continue

        name_a = a.get("full_name", id1)
        name_b = b.get("full_name", id2)

        # Check cache
        if (id1, id2) in verdicts_cache:
            verdict = verdicts_cache[(id1, id2)]
            tag = "[SAME]" if verdict["same_person"] else "[DIFF]"
            print(f"  [{i}/{len(pairs)}] {tag} {name_a} <-> {name_b} (cached)")
            if verdict["same_person"]:
                same_count += 1
            else:
                diff_count += 1
            continue

        # Build prompt and call API
        prompt = build_prompt(a, b)
        raw_response = ""

        try:
            if new_calls > 0:
                time.sleep(RATE_LIMIT_DELAY)
            raw_response = call_haiku(api_key, prompt)
            new_calls += 1
            verdict = parse_response(raw_response)
        except Exception as e:
            print(f"  [{i}/{len(pairs)}] [ERR] {name_a} <-> {name_b}: {e}")
            verdict = {
                "same_person": False,
                "confidence": "low",
                "reasoning": f"API error: {e}",
            }

        # Store verdict
        verdict["id1"] = id1
        verdict["id2"] = id2
        verdict["name1"] = name_a
        verdict["name2"] = name_b
        verdict["raw_response"] = raw_response
        verdicts_cache[(id1, id2)] = verdict

        tag = "[SAME]" if verdict["same_person"] else "[DIFF]"
        conf = verdict.get("confidence", "?")
        print(f"  [{i}/{len(pairs)}] {tag} {name_a} <-> {name_b} (confidence: {conf})")

        if verdict["same_person"]:
            same_count += 1
        else:
            diff_count += 1

        # Save cache incrementally
        save_verdicts(verdicts_cache)

    # Final save of verdicts
    save_verdicts(verdicts_cache)
    print(f"\nSaved {len(verdicts_cache)} verdicts to {VERDICTS_FILE}")

    # Apply merges
    before_count = len(authors)
    resolved_authors, merges_applied = apply_merges(
        authors, verdicts_cache, args.merge_threshold
    )
    after_count = len(resolved_authors)

    # Write output
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(resolved_authors, f, indent=2, ensure_ascii=False)

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Pairs reviewed:        {len(pairs)}")
    print(f"  Same person verdicts:  {same_count}")
    print(f"  Different verdicts:    {diff_count}")
    print(f"  Merges applied:        {merges_applied}")
    print(f"  Researchers before:    {before_count}")
    print(f"  Researchers after:     {after_count}")
    print("=" * 60)
    print(f"\n  Output: {args.output}")
    print(f"  Verdicts: {VERDICTS_FILE}")


if __name__ == "__main__":
    main()