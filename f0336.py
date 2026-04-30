#!/usr/bin/env python3
"""
Fetch Missing ORCID IDs
=========================
Reads a researcher JSON (e.g., Normalised_Authors.json) and finds
researchers without an ORCID. Searches the ORCID public API using
name + affiliation to find matches.

Search strategy:
  1. Search by given name + family name + affiliation name
  2. If no result, search by given name + family name only
  3. If no result, try each name variant

Only accepts a match when:
  - Exactly one result is returned, OR
  - Multiple results but one matches the affiliation

No API key required (ORCID public API).

Output: overwrites input file with orcid fields filled in, or
        writes to a separate output file.

Usage:
  python fetch_orcid_ids.py Normalised_Authors.json
  python fetch_orcid_ids.py Normalised_Authors.json --output Authors_With_ORCIDs.json
  python fetch_orcid_ids.py --dry-run
"""

import sys
import json
import time
import re
import argparse
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: pip install requests")
    sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = "Normalised_Authors.json"
ORCID_SEARCH_URL = "https://pub.orcid.org/v3.0/search/"
ORCID_RECORD_URL = "https://pub.orcid.org/v3.0"


# ============================================================
# ORCID API
# ============================================================

def orcid_search(query, session, max_retries=3):
    """Search ORCID public API. Returns parsed JSON or None."""
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(
                ORCID_SEARCH_URL,
                params={"q": query, "rows": 10},
                headers={"Accept": "application/json"},
                timeout=15,
            )
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            if resp.status_code != 200:
                return None
            return resp.json()
        except requests.exceptions.RequestException:
            if attempt < max_retries:
                time.sleep(1)
            else:
                return None
    return None


def fetch_orcid_employments(orcid_id, session, max_retries=2):
    """Fetch employment affiliations for an ORCID ID. Returns list of org name strings (for comparison)."""
    url = f"{ORCID_RECORD_URL}/{orcid_id}/employments"
    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(
                url,
                headers={"Accept": "application/json"},
                timeout=10,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            orgs = []
            for group in data.get("affiliation-group", []):
                for summary in group.get("summaries", []):
                    emp = summary.get("employment-summary", {})
                    org = emp.get("organization", {})
                    name = org.get("name", "")
                    if name:
                        orgs.append(name.lower().strip())
            return orgs
        except requests.exceptions.RequestException:
            if attempt < max_retries:
                time.sleep(1)
            else:
                return []
    return []


def fetch_orcid_affiliations_full(orcid_id, session, max_retries=2):
    """
    Fetch full affiliation records from ORCID (employments + educations).
    Returns list of structured dicts with name, place, ror, department, role.
    """
    affiliations = []
    seen_orgs = set()

    for endpoint in ["employments", "educations"]:
        url = f"{ORCID_RECORD_URL}/{orcid_id}/{endpoint}"
        for attempt in range(1, max_retries + 1):
            try:
                resp = session.get(
                    url,
                    headers={"Accept": "application/json"},
                    timeout=10,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()

                for group in data.get("affiliation-group", []):
                    for summary in group.get("summaries", []):
                        record = summary.get(f"{endpoint[:-1]}-summary", {})
                        org = record.get("organization", {})
                        name = org.get("name", "").strip()
                        if not name:
                            continue

                        key = name.lower()
                        if key in seen_orgs:
                            continue
                        seen_orgs.add(key)

                        aff = {"name": name, "source": "orcid"}

                        # Place
                        address = org.get("address", {})
                        city = address.get("city", "")
                        country = address.get("country", "")
                        if city and country:
                            aff["place"] = f"{city}, {country}"
                        elif country:
                            aff["place"] = country

                        # ROR / GRID / RINGGOLD
                        disamb = org.get("disambiguated-organization", {})
                        if disamb:
                            source_id = disamb.get("disambiguated-organization-identifier", "")
                            source_type = disamb.get("disambiguation-source", "")
                            if source_type == "ROR" and source_id:
                                aff["ror"] = source_id
                            elif source_type == "GRID" and source_id:
                                aff["grid"] = source_id

                        # Department and role
                        dept = record.get("department-name", "")
                        role = record.get("role-title", "")
                        if dept:
                            aff["department"] = dept
                        if role:
                            aff["role"] = role

                        affiliations.append(aff)
                break
            except requests.exceptions.RequestException:
                if attempt < max_retries:
                    time.sleep(1)
                else:
                    break

    return affiliations


def merge_affiliations(researcher, new_affs):
    """Merge new affiliations into researcher, deduplicating by name."""
    existing = researcher.get("affiliations", [])
    existing_names = {normalise_for_comparison(a.get("name", "")) for a in existing}

    for aff in new_affs:
        norm = normalise_for_comparison(aff.get("name", ""))
        if norm and norm not in existing_names:
            existing.append(aff)
            existing_names.add(norm)

    researcher["affiliations"] = existing


def normalise_for_comparison(s):
    """Normalise a string for fuzzy comparison."""
    s = s.lower().strip()
    s = s.replace("&amp;", "and").replace("&", "and")
    s = re.sub(r'[,.\-\'\"()]', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def affiliation_matches(orcid_orgs, researcher_affs):
    """
    Check if any of the ORCID employment orgs match the researcher's
    affiliations. Uses token overlap.
    """
    if not orcid_orgs or not researcher_affs:
        return False

    for orcid_org in orcid_orgs:
        orcid_tokens = set(normalise_for_comparison(orcid_org).split())
        for aff in researcher_affs:
            aff_name = aff.get("name", "")
            aff_tokens = set(normalise_for_comparison(aff_name).split())
            if not orcid_tokens or not aff_tokens:
                continue
            overlap = len(orcid_tokens & aff_tokens) / len(orcid_tokens | aff_tokens)
            if overlap >= 0.4:
                return True
    return False


def search_orcid_for_researcher(researcher, session, cache):
    """
    Try to find an ORCID for a researcher using multiple strategies.
    Returns (orcid_id, strategy_used) or (None, None).
    """
    given = researcher.get("given", "").strip()
    family = researcher.get("family", "").strip()
    affiliations = researcher.get("affiliations", [])

    if not family:
        return None, None

    # Extract a clean affiliation name for the search query
    aff_name = ""
    if affiliations:
        # Use the first affiliation, try to extract core org name
        raw_aff = affiliations[0].get("name", "")
        # Take the part that looks most like an institution
        parts = [p.strip() for p in raw_aff.split(",")]
        # Pick the longest part (often the institution name)
        if parts:
            aff_name = max(parts, key=len)

    # Strategy 1: given + family + affiliation
    if given and aff_name:
        cache_key = f"s1|{given}|{family}|{aff_name}".lower()
        if cache_key not in cache:
            query = f'given-names:"{given}" AND family-name:"{family}" AND affiliation-org-name:"{aff_name}"'
            result = orcid_search(query, session)
            cache[cache_key] = result
            time.sleep(0.5)
        else:
            result = cache[cache_key]

        orcid = evaluate_search_results(result, researcher, session, cache)
        if orcid:
            return orcid, "name+affiliation"

    # Strategy 2: given + family only
    if given:
        cache_key = f"s2|{given}|{family}".lower()
        if cache_key not in cache:
            query = f'given-names:"{given}" AND family-name:"{family}"'
            result = orcid_search(query, session)
            cache[cache_key] = result
            time.sleep(0.5)
        else:
            result = cache[cache_key]

        orcid = evaluate_search_results(result, researcher, session, cache)
        if orcid:
            return orcid, "name_only"

    # Strategy 3: try name variants
    for variant in researcher.get("name_variants", []):
        # Parse variant into given/family
        parts = variant.strip().rsplit(" ", 1)
        if len(parts) == 2:
            v_given, v_family = parts
        else:
            continue

        if v_given.lower() == given.lower() and v_family.lower() == family.lower():
            continue  # Same as primary, already tried

        cache_key = f"s3|{v_given}|{v_family}".lower()
        if cache_key not in cache:
            query = f'given-names:"{v_given}" AND family-name:"{v_family}"'
            result = orcid_search(query, session)
            cache[cache_key] = result
            time.sleep(0.5)
        else:
            result = cache[cache_key]

        orcid = evaluate_search_results(result, researcher, session, cache)
        if orcid:
            return orcid, "name_variant"

    return None, None


def evaluate_search_results(result, researcher, session, cache):
    """
    Evaluate ORCID search results and return an ORCID ID if confident.

    Rules:
    - Exactly 1 result -> accept
    - 2-5 results -> check affiliations to disambiguate
    - 6+ results -> too ambiguous, reject
    """
    if not result:
        return None

    num_found = result.get("num-found", 0)
    results = result.get("result", [])

    if num_found == 0 or not results:
        return None

    # Exactly one result -> accept
    if num_found == 1:
        return results[0].get("orcid-identifier", {}).get("path")

    # 2-5 results -> try to disambiguate by affiliation
    if num_found <= 5 and researcher.get("affiliations"):
        for r in results:
            orcid_id = r.get("orcid-identifier", {}).get("path")
            if not orcid_id:
                continue

            # Check employment affiliations
            emp_cache_key = f"emp|{orcid_id}"
            if emp_cache_key not in cache:
                orgs = fetch_orcid_employments(orcid_id, session)
                cache[emp_cache_key] = orgs
                time.sleep(0.3)
            else:
                orgs = cache[emp_cache_key]

            if affiliation_matches(orgs, researcher.get("affiliations", [])):
                return orcid_id

    return None


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fetch missing ORCID IDs using name + affiliation search"
    )
    parser.add_argument("input_json", nargs="?", default=None,
                        help=f"Input JSON (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", "-o", default=None,
                        help="Output JSON (default: overwrites input)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show researchers needing lookup without API calls")
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

    output_path = Path(args.output).resolve() if args.output else input_path
    cache_path = input_path.parent / "orcid_search_cache.json"

    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")
    print(f"Cache:  {cache_path}")
    print()

    # Load
    with open(input_path, "r", encoding="utf-8") as f:
        researchers = json.load(f)

    # Find researchers missing ORCID
    missing = [r for r in researchers if not r.get("orcid")]
    has_orcid = len(researchers) - len(missing)

    print(f"Total researchers:  {len(researchers)}")
    print(f"With ORCID:         {has_orcid}")
    print(f"Missing ORCID:      {len(missing)}")

    # Stats on missing researchers
    missing_with_aff = sum(1 for r in missing if r.get("affiliations"))
    missing_no_aff = len(missing) - missing_with_aff
    print(f"  With affiliation: {missing_with_aff}")
    print(f"  No affiliation:   {missing_no_aff}")

    if not missing:
        print("\nAll researchers have ORCIDs.")
        if output_path != input_path:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(researchers, f, ensure_ascii=False, indent=2)
        return

    if args.dry_run:
        def safe(s):
            return s.encode("ascii", errors="replace").decode("ascii")
        print(f"\n--- DRY RUN ---")
        for r in missing:
            aff = r["affiliations"][0]["name"][:50] if r.get("affiliations") else "no affiliation"
            print(f"  {safe(r['full_name'])} | {safe(aff)}")
        return

    # Load cache
    cache = {}
    if cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        print(f"Cache entries:      {len(cache)}")

    print()

    session = requests.Session()
    found = 0
    affs_filled = 0
    strategies = {"name+affiliation": 0, "name_only": 0, "name_variant": 0}

    def safe(s):
        return s.encode("ascii", errors="replace").decode("ascii")

    for i, r in enumerate(missing, 1):
        print(f"[{i}/{len(missing)}] {safe(r['full_name'])}", end=" ", flush=True)

        orcid, strategy = search_orcid_for_researcher(r, session, cache)

        if orcid:
            r["orcid"] = orcid
            found += 1
            strategies[strategy] += 1

            # Fetch affiliations from ORCID profile
            if not r.get("affiliations"):
                aff_cache_key = f"affs|{orcid}"
                if aff_cache_key not in cache:
                    new_affs = fetch_orcid_affiliations_full(orcid, session)
                    cache[aff_cache_key] = new_affs
                    time.sleep(0.3)
                else:
                    new_affs = cache[aff_cache_key]

                if new_affs:
                    merge_affiliations(r, new_affs)
                    affs_filled += 1
                    aff_names = [a["name"] for a in new_affs[:2]]
                    print(f"-> {orcid} ({strategy}) + affs: {safe('; '.join(aff_names)[:50])}")
                else:
                    print(f"-> {orcid} ({strategy})")
            else:
                print(f"-> {orcid} ({strategy})")
        else:
            print("-> not found")

    # Also fill affiliations for researchers who already had an ORCID
    # but have no affiliations
    has_orcid_no_aff = [r for r in researchers
                        if r.get("orcid") and not r.get("affiliations")
                        and r not in missing]
    if has_orcid_no_aff:
        print(f"\nFetching affiliations for {len(has_orcid_no_aff)} researchers with ORCID but no affiliation...")
        for i, r in enumerate(has_orcid_no_aff, 1):
            orcid = r["orcid"]
            print(f"  [{i}/{len(has_orcid_no_aff)}] {safe(r['full_name'])} ({orcid})", end=" ", flush=True)

            aff_cache_key = f"affs|{orcid}"
            if aff_cache_key not in cache:
                new_affs = fetch_orcid_affiliations_full(orcid, session)
                cache[aff_cache_key] = new_affs
                time.sleep(0.3)
            else:
                new_affs = cache[aff_cache_key]

            if new_affs:
                merge_affiliations(r, new_affs)
                affs_filled += 1
                aff_names = [a["name"] for a in new_affs[:2]]
                print(f"-> {safe('; '.join(aff_names)[:50])}")
            else:
                print("-> no affiliations in ORCID profile")
    else:
        print("\nNo researchers with ORCID missing affiliations.")

    # Save cache
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    # Save output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(researchers, f, ensure_ascii=False, indent=2)

    # Summary
    after_orcid = sum(1 for r in researchers if r.get("orcid"))
    after_affs = sum(1 for r in researchers if r.get("affiliations"))
    before_affs = has_orcid + missing_with_aff  # rough estimate

    print(f"\n{'='*55}")
    print(f"ORCID LOOKUP SUMMARY")
    print(f"{'='*55}")
    print(f"ORCID IDs found:         {found}/{len(missing)}")
    for strat, count in strategies.items():
        if count:
            print(f"    via {strat}:  {count}")
    print(f"  Not found:             {len(missing) - found}")
    print(f"Affiliations filled:     {affs_filled}")
    print(f"ORCIDs: {has_orcid} -> {after_orcid} / {len(researchers)}")
    print(f"Affiliations: {after_affs} / {len(researchers)} researchers")
    print(f"{'='*55}")
    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()