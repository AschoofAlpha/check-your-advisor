"""
check-your-advisor — author-disambiguated PubMed harvesting and reference verification.

Two entry points over one shared HTTP and normalisation layer:

  fetch/download  Search PubMed, keep only the target researcher's papers via
                  ORCID + affiliation + email verification, race 8 open-access
                  sources for the PDF, then verify the downloaded file really
                  is the requested paper.

  verify          Check a bibliography against CrossRef and PubMed, including
                  bidirectional DOI <-> PMID resolution.
"""

__version__ = "0.3.1"

__all__ = ["__version__"]
