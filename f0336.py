#!/usr/bin/env python3
"""
Fetch Missing Affiliations from ORCID
=======================================
Reads Resolved_Authors.json (or Normalised_Authors.json) and finds
researchers who have an ORCID but no affiliations. Fetches their
employment/education history from the ORCID public API and adds
the affiliations.

Only uses ORCID ID for lookup — no name-based searching.

The ORCID public API returns employment and education records which
include organisation name, city, country, and sometimes ROR/GRID IDs.

Output: Authors_With_Affiliations.json (same structure, affiliations filled in)

Setup:
  No API key needed — uses ORCID public API.

Usage:
  python f0336.py [Resolved_Authors.json] [--output Authors_With_Affiliations.json]
"""

import sys
import json
import time
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = "Resolved_Authors.json"
DEFAULT_OUTPUT = "Authors_With_Affiliations.json"
ORCID_API_BASE = "https://pub.orcid.org/v3.0"


# ============================================================
# ORCID API
# ============================================================

def fetch_orcid_record(orcid, session, max_retries=3):
    """
    Fetch the full ORCID record for a given ORCID ID.
    Returns the parsed JSON or None on failure.
    """
    url = f"{ORCID_API_BASE}/{orcid}/record"

    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, headers={"Accept": "application/json"}, timeout=15)

            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                wait = min(2 ** attempt * 2, 30)
                print(f"    Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                time.sleep(2 ** attempt)
            else:
                print(f"    Failed after {max_retries} retries: {e}")
                return None

    return None


def extract_affiliations_from_orcid(record):
    """
    Extract affiliations from an ORCID record.

    Pulls from both employments and educations sections.
    Returns a list of affiliation dicts matching the format used
    in earlier pipeline steps:
    {
        "name": "Organisation Name",
        "ror": "https://ror.org/...",   (if available)
        "place": "City, Country"        (if available)
    }
    """
    affiliations = []
    seen_orgs = set()  # Deduplicate by normalised org name

    # Extract from employments
    employments = (record
                   .get("activities-summary", {})
                   .get("employments", {})
                   .get("affiliation-group", []))

    for group in employments:
        for summary in group.get("summaries", []):
            emp = summary.get("employment-summary", {})
            aff = _parse_affiliation_summary(emp)
            if aff:
                key = aff["name"].lower().strip()
                if key not in seen_orgs:
                    seen_orgs.add(key)
                    affiliations.append(aff)

    # Extract from educations
    educations = (record
                  .get("activities-summary", {})
                  .get("educations", {})
                  .get("affiliation-group", []))

    for group in educations:
        for summary in group.get("summaries", []):
            edu = summary.get("education-summary", {})
            aff = _parse_affiliation_summary(edu)
            if aff:
                key = aff["name"].lower().strip()
                if key not in seen_orgs:
                    seen_orgs.add(key)
                    affiliations.append(aff)

    return affiliations


def _parse_affiliation_summary(summary):
    """
    Parse an employment-summary or education-summary into an
    affiliation dict.
    """
    org = summary.get("organization", {})
    if not org:
        return None

    name = org.get("name", "").strip()
    if not name:
        return None

    aff = {"name": name}

    # Extract place (city, country)
    address = org.get("address", {})
    city = address.get("city", "")
    country = address.get("country", "")
    if city and country:
        aff["place"] = f"{city}, {country}"
    elif country:
        aff["place"] = country
    elif city:
        aff["place"] = city

    # Extract ROR or GRID from disambiguated-organization
    disamb = org.get("disambiguated-organization", {})
    if disamb:
        source = disamb.get("disambiguated-organization-identifier", "")
        source_type = disamb.get("disambiguation-source", "")

        if source_type == "ROR":
            aff["ror"] = source
        elif source_type == "GRID":
            aff["grid"] = source
        elif source_type == "RINGGOLD":
            aff["ringgold"] = source

    # Extract department if available
    dept = summary.get("department-name", "")
    if dept:
        aff["department"] = dept

    # Extract role if available
    role = summary.get("role-title", "")
    if role:
        aff["role"] = role

    return aff


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fetch missing affiliations from ORCID API"
    )
    parser.add_argument("input_json", nargs="?", default=None,
                        help=f"Input JSON (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", "-o", default=None,
                        help=f"Output JSON (default: {DEFAULT_OUTPUT})")
    parser.add_argument("--overwrite", action="store_true",
                        help="Also fetch for researchers who already have affiliations")
    args = parser.parse_args()

    # Resolve paths
    if args.input_json:
        input_path = Path(args.input_json).resolve()
    else:
        input_path = Path.cwd() / DEFAULT_INPUT
        if not input_path.exists():
            input_path = SCRIPT_DIR / DEFAULT_INPUT

    if not input_path.exists():
        print(f"ERROR: {input_path} not found")
        sys.exit(1)

    output_path = Path(args.output).resolve() if args.output else input_path.parent / DEFAULT_OUTPUT
    cache_path = input_path.parent / "orcid_affiliation_cache.json"

    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")
    print(f"Cache:  {cache_path}")
    print()

    # Load researchers
    with open(input_path, "r", encoding="utf-8") as f:
        researchers = json.load(f)

    # Load cache
    cache = {}
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        print(f"ORCID cache: {len(cache)} entries")

    # Find researchers needing affiliation lookup
    needs_lookup = []
    for r in researchers:
        orcid = r.get("orcid")
        if not orcid:
            continue

        has_affs = bool(r.get("affiliations"))
        if has_affs and not args.overwrite:
            continue

        needs_lookup.append(r)

    # Stats
    total = len(researchers)
    with_orcid = sum(1 for r in researchers if r.get("orcid"))
    with_affs = sum(1 for r in researchers if r.get("affiliations"))
    without_affs_with_orcid = len(needs_lookup)

    print(f"Total researchers:              {total}")
    print(f"  With ORCID:                   {with_orcid}")
    print(f"  With affiliations:            {with_affs}")
    print(f"  Need lookup (ORCID, no aff):  {without_affs_with_orcid}")

    if not needs_lookup:
        print("\nNo researchers need affiliation lookup.")
        # Still save output (copy of input)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(researchers, f, ensure_ascii=False, indent=2)
        print(f"Saved: {output_path}")
        return

    # Fetch from ORCID API
    session = requests.Session()
    fetched = 0
    found = 0
    cached_hits = 0

    print(f"\nFetching affiliations from ORCID API...")

    for i, r in enumerate(needs_lookup, 1):
        orcid = r["orcid"]

        # Check cache first
        if orcid in cache:
            cached_affs = cache[orcid]
            if cached_affs:
                _merge_affiliations(r, cached_affs)
                found += 1
            cached_hits += 1
            continue

        print(f"  [{i}/{len(needs_lookup)}] {r['full_name']} ({orcid})", end=" ", flush=True)

        record = fetch_orcid_record(orcid, session)
        fetched += 1

        if record:
            affs = extract_affiliations_from_orcid(record)
            cache[orcid] = affs

            if affs:
                _merge_affiliations(r, affs)
                found += 1
                print(f"-> {len(affs)} affiliations")
            else:
                print("-> no affiliations in ORCID profile")
        else:
            cache[orcid] = []
            print("-> ORCID record not found")

        # Rate limit: ORCID public API
        time.sleep(0.3)

    # Save cache
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    # Save output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(researchers, f, ensure_ascii=False, indent=2)

    # Summary
    after_affs = sum(1 for r in researchers if r.get("affiliations"))

    print(f"\n{'='*55}")
    print(f"AFFILIATION FETCH SUMMARY")
    print(f"{'='*55}")
    print(f"Researchers processed:       {len(needs_lookup)}")
    print(f"  From cache:                {cached_hits}")
    print(f"  API calls:                 {fetched}")
    print(f"  Affiliations found:        {found}")
    print(f"Before: {with_affs} researchers with affiliations")
    print(f"After:  {after_affs} researchers with affiliations")
    print(f"Gained: {after_affs - with_affs}")
    print(f"{'='*55}")
    print(f"\nSaved: {output_path}")


def _merge_affiliations(researcher, new_affs):
    """
    Merge new affiliations into a researcher's existing list.
    Deduplicates by normalised org name.
    """
    existing = researcher.get("affiliations", [])
    existing_names = {a.get("name", "").lower().strip() for a in existing}

    for aff in new_affs:
        name = aff.get("name", "").lower().strip()
        if name and name not in existing_names:
            existing.append(aff)
            existing_names.add(name)

    researcher["affiliations"] = existing


if __name__ == "__main__":
    main()