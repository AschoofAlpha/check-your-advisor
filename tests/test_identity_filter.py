#!/usr/bin/env python3
"""
Author identity filter tests.

Demonstrates with synthetic PubMed records that the filter keeps papers by the
target researcher and rejects same-name researchers at other institutions.

All data here is fictional. The ORCID used is 0000-0002-1825-0097, which is
ORCID's own published example identifier for the fictional character Josiah
Carberry, so no real person's identifier appears in this repository.

The test defines its own identity fixture rather than importing the shipped
default, so it keeps passing regardless of what the packaged defaults are.

Run: python tests/test_identity_filter.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

logging.basicConfig(level=logging.DEBUG, format="%(message)s", stream=sys.stdout)

from check_your_advisor.pubmed_api import is_first_or_corresponding  # noqa: E402

# ============================================================
# Identity fixture — the "who am I looking for" configuration
# ============================================================
TARGET_NAME = "Li Wenhao"
TARGET_AFFILIATION = "Nanhai Medical University"
TEST_ORCID = "0000-0002-1825-0097"

TEST_IDENTITY = {
    "affiliation_keywords": [
        "Nanhai Medical University",
        "Nanhai Med Univ",
        "Affiliated Hospital of Nanhai Medical",
        "Nanhai Union Hospital",
    ],
    "email_domains": ["@nanhai-med.example.edu"],
    "orcid": TEST_ORCID,
    "require_affiliation": True,
}


def _author(name, affiliation="", corresponding=False, email="", orcid=""):
    last, fore = name.split(" ", 1)
    return {
        "name": name, "last": last, "fore": fore, "initials": fore[:1],
        "affiliation": affiliation, "is_corresponding": corresponding,
        "email": email, "orcid": orcid,
    }


# ============================================================
# 6 synthetic papers covering each decision path
# ============================================================
TEST_PAPERS = [
    # 1. Target researcher, first author, affiliation + email match -> KEEP
    {
        "pmid": "10000001",
        "title": "Immunotherapy for hepatocellular carcinoma",
        "authors": [
            _author("Li Wenhao",
                    "Department of Hepatobiliary Surgery, Nanhai Medical University, China",
                    corresponding=True, email="liwenhao@nanhai-med.example.edu"),
            _author("Wang Li", "Some Other Institution"),
        ],
    },
    # 2. Same name, different field and institution -> SKIP
    {
        "pmid": "10000002",
        "title": "Lake eutrophication and cyanobacterial blooms",
        "authors": [
            _author("Li Wenhao",
                    "Institute of Geography and Limnology, Example Academy of Sciences",
                    corresponding=True, email="lwh@limnology.example.ac"),
        ],
    },
    # 3. Last-position corresponding author, affiliated-hospital variant -> KEEP
    {
        "pmid": "10000003",
        "title": "Advances in laparoscopic gastric cancer surgery",
        "authors": [
            _author("Liu Hua", "Random Hospital"),
            _author("Li Wenhao",
                    "Affiliated Hospital of Nanhai Medical University",
                    corresponding=True),
        ],
    },
    # 4. Same name, computer science department -> SKIP
    {
        "pmid": "10000004",
        "title": "Deep learning for image recognition",
        "authors": [
            _author("Li Wenhao",
                    "Department of Computer Science, Example Institute of Technology",
                    corresponding=True),
        ],
    },
    # 5. Affiliation matches but middle-author position -> SKIP
    {
        "pmid": "10000005",
        "title": "A multicentre clinical trial",
        "authors": [
            _author("Zhao Min", "X"),
            _author("Li Wenhao", "Nanhai Medical University"),
            _author("Sun Qi", "Y", corresponding=True),
        ],
    },
    # 6. ORCID match wins even with affiliation missing -> KEEP
    {
        "pmid": "10000006",
        "title": "A rare hepatobiliary case report",
        "authors": [
            _author("Li Wenhao", "", corresponding=True, orcid=TEST_ORCID),
        ],
    },
]

EXPECTED = {
    "10000001": True,   # affiliation + email match
    "10000002": False,  # same name, unrelated institution
    "10000003": True,   # affiliated-hospital name variant
    "10000004": False,  # same name, unrelated institution
    "10000005": False,  # not first or corresponding author
    "10000006": True,   # ORCID match, affiliation absent
}


def run_tests() -> bool:
    print("=" * 70)
    print(f"Author identity filter — target: {TARGET_NAME} @ {TARGET_AFFILIATION}")
    print("=" * 70)

    passed = failed = 0
    for paper in TEST_PAPERS:
        print(f"\n--- PMID {paper['pmid']}: {paper['title']} ---")
        is_match, role = is_first_or_corresponding(
            paper, TARGET_NAME, TARGET_AFFILIATION, TEST_IDENTITY
        )
        expect = EXPECTED[paper["pmid"]]
        ok = is_match == expect
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        print(f"  Expected: {'KEEP' if expect else 'SKIP'} | "
              f"Actual: {'KEEP' if is_match else 'SKIP'} | "
              f"Role: {role} | {'[PASS]' if ok else '[FAIL]'}")

    print("\n" + "=" * 70)
    print(f"Summary: {passed} passed / {failed} failed / {len(TEST_PAPERS)} total")
    print("=" * 70)
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)
