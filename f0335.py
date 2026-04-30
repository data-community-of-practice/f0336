#!/usr/bin/env python3
"""
Normalise & Merge Crossref Authors
=========================================
Reads Crossref_AuthorMetadata.json and deduplicates authors.

Auto-merge rules (confident):
  1. Same ORCID → always merge
  2. Surname-first fix: "Doe" / "Jane L." → flip, then compare
  3. No ORCID, names identical after stripping periods/spaces:
     "Jane L. Doe" = "Jane L Doe"
     "J.L. Doe" = "J L Doe" = "JL Doe"
     "John M. Smith" = "John M Smith"
     Full words must match exactly — initials only match other initials.

Flag as similar (for manual review):
  4. Initial vs full name: "J.L. Doe" ~ "Jane L. Doe"
  5. Missing name parts: "Jane Doe" ~ "Jane L. Doe"
  6. Spelling variants: "John Mathew Smith" ~ "John Matthew Smith"
  7. Possible surname-first: "Doe Jane" ~ "Jane Doe"

Output: Normalised_Authors.json

Usage:
  python f0335.py [Crossref_AuthorMetadata.json] [--output Normalised_Authors.json]
"""

import sys
import json
import uuid
import re
import argparse
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = "Crossref_AuthorMetadata.json"
DEFAULT_OUTPUT = "Normalised_Authors.json"


# ============================================================
# NAME PARSING
# ============================================================

def clean_str(s):
    """Lowercase, strip extra whitespace."""
    return re.sub(r'\s+', ' ', s).strip().lower()


def strip_periods(s):
    """Remove periods only."""
    return s.replace(".", "")


def tokenise_given(given):
    """
    Split a given name string into tokens, expanding compressed initials.

    "Jane L."    → ["jane", "l"]
    "J.L."       → ["j", "l"]
    "JL"         → ["j", "l"]
    "J L"        → ["j", "l"]
    "Jane Lee"   → ["jane", "lee"]
    "John M"     → ["john", "m"]
    "Wei"        → ["wei"]   (short name with vowel, kept as word)
    "DJ"         → ["d", "j"] (no vowels, split into initials)
    """
    s = strip_periods(given).strip().lower()
    if not s:
        return []

    parts = s.split()
    result = []
    vowels = set("aeiou")

    for p in parts:
        if len(p) <= 1:
            result.append(p)
        elif len(p) <= 3 and all(c.isalpha() for c in p):
            has_vowel = any(c in vowels for c in p)
            if has_vowel:
                result.append(p)
            else:
                result.extend(list(p))
        else:
            result.append(p)

    return result


def is_initial(token):
    """Is this token a single-letter initial?"""
    return len(token) == 1 and token.isalpha()


# ============================================================
# MERGE KEY — for auto-merge (conservative)
# ============================================================

def make_merge_key(given, family):
    """
    Build a merge key where ONLY formatting differences collapse.

    "Jane L. Doe"   → "doe|jane l"
    "Jane L Doe"    → "doe|jane l"
    "J.L. Doe"      → "doe|j l"
    "JL Doe"        → "doe|j l"
    "J L Doe"       → "doe|j l"
    "John M. Smith" → "smith|john m"

    Note: "Jane L Doe" and "J L Doe" get DIFFERENT keys.
    """
    fam = clean_str(strip_periods(family))
    tokens = tokenise_given(given)

    if not fam:
        return None

    given_key = " ".join(tokens)
    return f"{fam}|{given_key}"


# ============================================================
# SIMILARITY DETECTION
# ============================================================

def names_are_similar(given1, family1, given2, family2):
    """
    Check if two names are "similar" (flag for review).
    """
    fam1 = clean_str(strip_periods(family1))
    fam2 = clean_str(strip_periods(family2))
    tokens1 = tokenise_given(given1)
    tokens2 = tokenise_given(given2)

    fam_exact = (fam1 == fam2)
    fam_hyphen = _hyphen_equivalent(fam1, fam2)

    if fam_exact:
        if not tokens1 or not tokens2:
            return True
        return _given_tokens_similar(tokens1, tokens2, given1, given2)

    if fam_hyphen:
        return True

    given1_str = clean_str(strip_periods(given1))
    given2_str = clean_str(strip_periods(given2))

    if fam1 == given2_str and fam2 == given1_str:
        return True

    return False


def _hyphen_equivalent(s1, s2):
    if s1 == s2:
        return False
    return s1.replace("-", " ") == s2.replace("-", " ")


def _given_tokens_similar(tokens1, tokens2, raw_given1="", raw_given2=""):
    if tokens1 == tokens2:
        return False

    if raw_given1 and raw_given2:
        g1 = clean_str(strip_periods(raw_given1))
        g2 = clean_str(strip_periods(raw_given2))
        if _hyphen_equivalent(g1, g2):
            return True

    shorter, longer = (tokens1, tokens2) if len(tokens1) <= len(tokens2) else (tokens2, tokens1)

    matched_count = 0
    for sp in shorter:
        for lp in longer:
            if sp == lp:
                matched_count += 1
                break
            elif is_initial(sp) and lp.startswith(sp):
                matched_count += 1
                break
            elif is_initial(lp) and sp.startswith(lp):
                matched_count += 1
                break
            elif _spelling_similar(sp, lp):
                matched_count += 1
                break

    return matched_count > 0


def _spelling_similar(word1, word2):
    if is_initial(word1) or is_initial(word2):
        return False
    if len(word1) < 3 or len(word2) < 3:
        return False
    if abs(len(word1) - len(word2)) > 2:
        return False
    d = _levenshtein(word1, word2)
    max_edits = 1 if max(len(word1), len(word2)) <= 5 else 2
    return 0 < d <= max_edits


def _levenshtein(s1, s2):
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr.append(min(curr[j] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


# ============================================================
# SURNAME-FIRST DETECTION
# ============================================================

def detect_and_fix_surname_first(appearances):
    fixed = 0
    for app in appearances:
        g = app["given"].strip()
        f = app["family"].strip()
        if not g or not f:
            continue
        if "." in f and "." not in g:
            app["given"], app["family"] = f, g
            app["_swapped"] = True
            fixed += 1
    return fixed


# ============================================================
# AFFILIATION DEDUP
# ============================================================

def normalise_aff_name(name):
    n = name.lower()
    n = n.replace("&amp;", "and").replace("&", "and")
    n = n.replace("st.", "st").replace("st'", "st")
    n = re.sub(r'[,.\-\'\"()]', ' ', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n


def aff_similarity(name1, name2):
    n1 = set(normalise_aff_name(name1).split())
    n2 = set(normalise_aff_name(name2).split())
    if not n1 or not n2:
        return 0.0
    return len(n1 & n2) / len(n1 | n2)


def dedupe_affiliations(affiliations, threshold=0.80):
    """Cluster affiliations by similarity, keep longest version.

    Field mapping from f0334.py output:
      - name:     affiliation name string
      - ror_id:   ROR identifier (e.g., "https://ror.org/0153tk833")
      - place:    location string (e.g., "Charlottesville, USA")
    """
    if not affiliations:
        return []

    clusters = []
    for aff in affiliations:
        name = aff.get("name", "").strip()
        if not name:
            continue

        merged = False
        for cluster in clusters:
            rep = cluster[0]
            if aff_similarity(name, rep.get("name", "")) >= threshold:
                if len(name) > len(rep.get("name", "")):
                    ror_id = aff.get("ror_id") or rep.get("ror_id")
                    place = aff.get("place") or rep.get("place")
                    cluster[0] = dict(aff)
                    if ror_id:
                        cluster[0]["ror_id"] = ror_id
                    if place:
                        cluster[0]["place"] = place
                else:
                    if not rep.get("ror_id") and aff.get("ror_id"):
                        rep["ror_id"] = aff["ror_id"]
                    if not rep.get("place") and aff.get("place"):
                        rep["place"] = aff["place"]
                merged = True
                break

        if not merged:
            clusters.append([dict(aff), []])

    return [c[0] for c in clusters]


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Normalise and merge Crossref authors"
    )
    parser.add_argument("input_json", nargs="?", default=None,
                        help=f"Input JSON (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", "-o", default=None,
                        help=f"Output JSON (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

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

    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")
    print()

    with open(input_path, "r", encoding="utf-8") as f:
        crossref_data = json.load(f)

    print(f"DOIs in input: {len(crossref_data)}")

    # Build DOI → title lookup
    doi_titles = {}
    for entry in crossref_data:
        doi = entry.get("doi", "")
        title = entry.get("title", "")
        if doi and title:
            doi_titles[doi] = title

    # Collect appearances
    # NOTE: f0334 uses "affiliations" (plural) and "ror_id" for the ROR field
    appearances = []
    for entry in crossref_data:
        doi = entry.get("doi", "")
        if entry.get("error") or not doi:
            continue
        for author in entry.get("authors", []):
            g = author.get("given", "").strip()
            f = author.get("family", "").strip()
            if not f and not g:
                continue
            appearances.append({
                "given": g,
                "family": f,
                "orcid": author.get("orcid"),
                "doi": doi,
                "affiliations": author.get("affiliations", []),  # "affiliations" not "affiliation"
            })

    print(f"Total author appearances: {len(appearances)}")

    # ---- STEP A: Fix surname-first ----
    print(f"\nStep A: Fixing surname-first entries...")
    swapped = detect_and_fix_surname_first(appearances)
    print(f"  Fixed: {swapped}")
    for app in appearances:
        if app.get("_swapped"):
            print(f"    \"{app['given']} {app['family']}\" (was swapped)")

    # ---- STEP B: ORCID-based merge ----
    print(f"\nStep B: Merging by ORCID...")

    clusters = {}
    orcid_to_cluster = {}
    assigned = set()

    for i, app in enumerate(appearances):
        if app.get("orcid"):
            orcid = app["orcid"]
            if orcid not in orcid_to_cluster:
                cid = str(uuid.uuid4())
                orcid_to_cluster[orcid] = cid
                clusters[cid] = {"orcid": orcid, "appearances": []}
            clusters[orcid_to_cluster[orcid]]["appearances"].append(app)
            assigned.add(i)

    print(f"  ORCID clusters: {len(clusters)}")

    # ---- STEP C: Merge non-ORCID by exact merge key ----
    print(f"\nStep C: Merging non-ORCID by normalised name...")

    orcid_merge_keys = {}
    for cid, cluster in clusters.items():
        for app in cluster["appearances"]:
            mk = make_merge_key(app["given"], app["family"])
            if mk:
                orcid_merge_keys[mk] = cid

    mergekey_groups = defaultdict(list)
    no_key = []

    for i, app in enumerate(appearances):
        if i in assigned:
            continue
        mk = make_merge_key(app["given"], app["family"])
        if mk:
            if mk in orcid_merge_keys:
                cid = orcid_merge_keys[mk]
                clusters[cid]["appearances"].append(app)
                assigned.add(i)
            else:
                mergekey_groups[mk].append(app)
                assigned.add(i)
        else:
            no_key.append(app)

    for mk, apps in mergekey_groups.items():
        cid = str(uuid.uuid4())
        clusters[cid] = {"orcid": None, "appearances": apps}

    for app in no_key:
        cid = str(uuid.uuid4())
        clusters[cid] = {"orcid": None, "appearances": [app]}

    print(f"  Total clusters: {len(clusters)}")

    # ---- STEP D: Build researcher nodes ----
    print(f"\nStep D: Building researcher nodes...")

    researchers = []
    cluster_to_rid = {}

    for cid, cluster in clusters.items():
        rid = str(uuid.uuid4())
        cluster_to_rid[cid] = rid

        apps = cluster["appearances"]
        orcid = cluster.get("orcid")

        name_variants = set()
        all_dois = set()
        all_affiliations = []
        best_given = ""
        best_family = ""

        for app in apps:
            variant = f"{app['given']} {app['family']}".strip()
            if variant:
                name_variants.add(variant)
            all_dois.add(app["doi"])

            # Collect affiliations — field is "affiliations" (plural)
            all_affiliations.extend(app.get("affiliations", []))

            if app.get("orcid") and not orcid:
                orcid = app["orcid"]

            if len(app["given"]) > len(best_given):
                best_given = app["given"]
                best_family = app["family"]
            elif not best_family:
                best_family = app["family"]

        # Build publications list with titles
        pub_list = []
        for doi in sorted(all_dois):
            pub_entry = {"doi": doi}
            if doi in doi_titles:
                pub_entry["title"] = doi_titles[doi]
            pub_list.append(pub_entry)

        researcher = {
            "id": rid,
            "given": best_given,
            "family": best_family,
            "full_name": f"{best_given} {best_family}".strip(),
            "orcid": orcid,
            "name_variants": sorted(name_variants),
            "publications": pub_list,
            "affiliations": dedupe_affiliations(all_affiliations),
            "similar_to": [],
            "merge_confidence": "orcid" if orcid else "name",
        }
        researchers.append(researcher)

    print(f"  Researchers: {len(researchers)}")

    # ---- STEP E: Find similar pairs ----
    print(f"\nStep E: Finding similar pairs...")

    family_index = defaultdict(list)
    for i, r in enumerate(researchers):
        fam = clean_str(strip_periods(r["family"]))
        if fam:
            family_index[fam].append(i)

    family_dehyphen_index = defaultdict(set)
    for fam in family_index:
        dehyph = fam.replace("-", " ")
        family_dehyphen_index[dehyph].add(fam)

    given_as_family = defaultdict(list)
    for i, r in enumerate(researchers):
        given_clean = clean_str(strip_periods(r["given"]))
        if given_clean:
            given_as_family[given_clean].append(i)

    similar_pairs = set()

    for fam, indices in family_index.items():
        if len(indices) < 2:
            continue
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                ri = researchers[indices[i]]
                rj = researchers[indices[j]]
                if names_are_similar(ri["given"], ri["family"],
                                     rj["given"], rj["family"]):
                    pair = tuple(sorted([ri["id"], rj["id"]]))
                    similar_pairs.add(pair)

    for dehyph, fam_keys in family_dehyphen_index.items():
        if len(fam_keys) < 2:
            continue
        fam_list = sorted(fam_keys)
        for fi in range(len(fam_list)):
            for fj in range(fi + 1, len(fam_list)):
                for i in family_index[fam_list[fi]]:
                    for j in family_index[fam_list[fj]]:
                        ri = researchers[i]
                        rj = researchers[j]
                        if names_are_similar(ri["given"], ri["family"],
                                             rj["given"], rj["family"]):
                            pair = tuple(sorted([ri["id"], rj["id"]]))
                            similar_pairs.add(pair)

    for fam, indices in family_index.items():
        if fam in given_as_family:
            for i in indices:
                for j in given_as_family[fam]:
                    if i == j:
                        continue
                    ri = researchers[i]
                    rj = researchers[j]
                    if names_are_similar(ri["given"], ri["family"],
                                         rj["given"], rj["family"]):
                        pair = tuple(sorted([ri["id"], rj["id"]]))
                        similar_pairs.add(pair)

    rid_to_researcher = {r["id"]: r for r in researchers}
    for id1, id2 in similar_pairs:
        r1 = rid_to_researcher[id1]
        r2 = rid_to_researcher[id2]
        r1["similar_to"].append({
            "id": id2,
            "full_name": r2["full_name"],
            "orcid": r2.get("orcid"),
        })
        r2["similar_to"].append({
            "id": id1,
            "full_name": r1["full_name"],
            "orcid": r1.get("orcid"),
        })

    print(f"  Similar pairs: {len(similar_pairs)}")

    # ---- Save ----
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(researchers, f, ensure_ascii=False, indent=2)

    # ---- Summary ----
    with_orcid = sum(1 for r in researchers if r.get("orcid"))
    without_orcid = len(researchers) - with_orcid
    with_similar = sum(1 for r in researchers if r["similar_to"])
    multi_pub = sum(1 for r in researchers if len(r["publications"]) > 1)
    multi_variant = [r for r in researchers if len(r["name_variants"]) > 1]
    with_affiliations = sum(1 for r in researchers if r.get("affiliations"))
    with_ror = sum(1 for r in researchers
                   if any(a.get("ror_id") for a in r.get("affiliations", [])))

    print(f"\n{'='*60}")
    print(f"NORMALISATION SUMMARY")
    print(f"{'='*60}")
    print(f"Input appearances:          {len(appearances)}")
    print(f"Deduplicated researchers:   {len(researchers)}")
    print(f"  With ORCID:               {with_orcid}")
    print(f"  Without ORCID:            {without_orcid}")
    print(f"  With affiliations:        {with_affiliations}")
    print(f"  With ROR ID:              {with_ror}")
    print(f"  Multi-publication:        {multi_pub}")
    print(f"  Merged name variants:     {len(multi_variant)}")
    print(f"  Flagged similar:          {with_similar} ({len(similar_pairs)} pairs)")

    if multi_variant:
        print(f"\nTop merged researchers:")
        for r in sorted(multi_variant, key=lambda x: len(x["name_variants"]), reverse=True)[:15]:
            tag = f" [ORCID: {r['orcid']}]" if r["orcid"] else " [no ORCID]"
            aff_names = [a.get("name", "?") for a in r.get("affiliations", [])]
            name_display = r['full_name'].encode("ascii", errors="replace").decode("ascii")
            print(f"  {name_display}{tag}")
            variants_display = str(r['name_variants']).encode("ascii", errors="replace").decode("ascii")
            print(f"    Variants: {variants_display}")
            print(f"    Pubs: {len(r['publications'])}")
            if aff_names:
                aff_display = str(aff_names).encode("ascii", errors="replace").decode("ascii")
                print(f"    Affiliations: {aff_display}")

    if similar_pairs:
        print(f"\nSimilar pairs (review manually):")
        shown = set()
        for r in researchers:
            for sim in r["similar_to"]:
                pk = tuple(sorted([r["id"], sim["id"]]))
                if pk not in shown:
                    shown.add(pk)
                    t1 = f" [{r['orcid']}]" if r.get("orcid") else ""
                    t2 = f" [{sim['orcid']}]" if sim.get("orcid") else ""
                    name1 = r['full_name'].encode("ascii", errors="replace").decode("ascii")
                    name2 = sim['full_name'].encode("ascii", errors="replace").decode("ascii")
                    print(f"  \"{name1}\"{t1}  ~  \"{name2}\"{t2}")
                    if len(shown) >= 30:
                        rem = len(similar_pairs) - len(shown)
                        if rem > 0:
                            print(f"  ... and {rem} more")
                        break
            if len(shown) >= 30:
                break

    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()