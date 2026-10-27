"""Fast local fuzzy and full-text search engine for EspressoMail."""

from espressomail.search.fuzzy import (
    FuzzySearchEngine,
    SearchQuery,
    SearchResult,
    fuzzy_score,
    parse_search_query,
)

__all__ = [
    "FuzzySearchEngine",
    "SearchQuery",
    "SearchResult",
    "fuzzy_score",
    "parse_search_query",
]
