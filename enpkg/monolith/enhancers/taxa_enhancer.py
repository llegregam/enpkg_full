"""Submodule for the taxa enhancer."""

import logging
from re import match as regex_match
from typing import Dict, Optional

import requests

from enpkg.monolith.data.analysis import Analysis
from enpkg.monolith.data.otl_class import LineageItem, Match
from enpkg.monolith.data.wikidata_ott_query_class import WikidataOTTQuery
from enpkg.monolith.enhancers.enhancer import Enhancer
from enpkg.monolith.exceptions import EnrichmentError

logger = logging.getLogger(__name__)


def _sanitize_github_username(username: str) -> str:
    if not isinstance(username, str):
        raise TypeError("Github Username must be a string formatted as 'github.com/YOUR_USERNAME")
    if not regex_match(r"^[a-zA-Z0-9-]+$", username):
        raise ValueError("Invalid GitHub username: only alphanumeric characters and hyphens allowed")

    return username

class TaxaEnhancer(Enhancer):
    """Enhancer that adds taxa information to the analysis."""

    OTL_ENDPOINT: str = "https://api.opentreeoflife.org/v3"
    # The Open Tree of Life API answers every call as a JSON POST, and is
    # occasionally slow to respond on a cold cache. The timeout matches the
    # Wikidata one below: long enough to absorb that, short enough that a
    # wedged endpoint cannot stall a whole batch run indefinitely.
    OTL_TIMEOUT: int = 30
    WIKIDATA_ENDPOINT: str = "https://query.wikidata.org/sparql"
    # Wikidata's SPARQL endpoint requires an identifying User-Agent; the
    # default `python-requests/x.y.z` is rate-limited and now (2024+)
    # rejected outright with HTTP 403. See
    # https://meta.wikimedia.org/wiki/User-Agent_policy.
    WIKIDATA_USER_AGENT_TEMPLATE: str = "ms2kg-pipeline/0.1 (https://github.com/{github_username})"
    WIKIDATA_QUERY_TEMPLATE: str = """
    PREFIX wdt: <http://www.wikidata.org/prop/direct/>
    SELECT ?ott ?wd ?img
    WHERE{{
        ?wd wdt:P9157 ?ott
        OPTIONAL{{ ?wd wdt:P18 ?img }}
        VALUES ?ott {{'{open_tree_taxon_id}'}}
    }}
    """

    def __init__(self, github_username: str = "llegregam"):
        """Initializes the enhancer.

        Sets up an instance-level cache of Wikidata SPARQL results keyed by
        ``open_tree_taxon_id``. The same enhancer is reused across batch
        experiments via build_shared_steps, so this cache survives the
        natural scope: one entry per unique OTT taxon for the life of the
        Python process.

        Both successful WikidataOTTQuery results and definitive failures
        (None sentinel, written on RequestException or non-200) are cached
        — without negative caching, a 30s timeout on the first experiment
        would be re-paid by every subsequent experiment that shares the
        same taxon ID, deepening WDQS rate-limit penalties.

        Args:
            github_username: Github username to populate the User-Agent header in Wikidata requests.
        """
        self._wikidata_by_open_tree_taxon_id: dict[int, Optional[WikidataOTTQuery]] = {}
        sane_github_username = _sanitize_github_username(github_username)
        self._wikidata_user_agent = self.__class__.WIKIDATA_USER_AGENT_TEMPLATE.format(
            github_username=sane_github_username
        )


    def name(self) -> str:
        """Returns the name of the enhancer."""
        return "Taxonomical Enhancer"

    def _call_open_tree(self, fragment: str, payload: Dict) -> Dict:
        """POST a payload to an Open Tree of Life v3 endpoint and return the decoded body.

        Args:
            fragment: Path fragment below the v3 root, e.g. ``"tnrs/match_names"``.
            payload: JSON body of the call.

        Returns:
            The decoded JSON response.

        Raises:
            EnrichmentError: The endpoint was unreachable, timed out, or answered
                with a non-200 status.
        """
        try:
            response = requests.post(
                f"{self.OTL_ENDPOINT}/{fragment}",
                json=payload,
                headers={"content-type": "application/json", "accept": "application/json"},
                timeout=self.OTL_TIMEOUT,
            )
        except requests.exceptions.RequestException as exc:
            raise EnrichmentError(
                f"Open Tree of Life request to '{fragment}' failed "
                f"({type(exc).__name__}: {exc})"
            ) from exc

        if response.status_code != 200:
            raise EnrichmentError(
                f"Open Tree of Life returned HTTP {response.status_code} for '{fragment}'"
            )

        return response.json()

    def retrieve_matches(self, genus: str, species: str) -> list[Match]:
        """Retrieves OTT matches for the source taxon."""

        ott_match: Dict = self._call_open_tree(
            "tnrs/match_names",
            {
                "names": [f"{genus} {species}"],
                "do_approximate_matching": True,
                "include_suppressed": False,
            },
        )

        if "results" not in ott_match:
            raise EnrichmentError(f"No OTT results found searching for '{genus} {species}'")

        ott_match_results = ott_match["results"][0]

        if "matches" not in ott_match_results:
            raise EnrichmentError(f"No matches in the OTT match results searching for '{genus} {species}'")

        matches: list[Match] = [
            Match.from_dict(match) for match in ott_match_results["matches"]
        ]

        if len(matches) == 0:
            return []

        # We retrieve the upper taxon information for each match
        for match in matches:
            match.set_lineage(
                LineageItem.from_dict(
                    self._call_open_tree(
                        "taxonomy/taxon_info",
                        {
                            "ott_id": match.open_tree_taxon_id,
                            "include_lineage": True,
                            "include_children": False,
                            "include_terminal_descendants": False,
                        },
                    )
                )
            )


        # Fetch Wikidata information for each match. WDQS query latency is
        # highly variable (cold queries can take 10-30s) and the public
        # endpoint occasionally rejects with 403 / 429 under load — both are
        # non-fatal for the pipeline, so per-match failures only log a
        # warning and skip Wikidata enrichment for that match. The 30s
        # timeout is a compromise: long enough to absorb a cold-cache hit,
        # short enough that a wedged endpoint doesn't stall the whole step.
        #
        # Results are cached per open_tree_taxon_id (see __init__) so
        # repeated taxa across batch experiments fire at most one SPARQL
        # request each — including negative results, which short-circuit
        # subsequent experiments past the 30s timeout / 429 backoff.
        for match in matches:
            taxon_id = match.open_tree_taxon_id
            if taxon_id in self._wikidata_by_open_tree_taxon_id:
                cached = self._wikidata_by_open_tree_taxon_id[taxon_id]
                if cached is not None:
                    match.set_wikidata(cached)
                continue

            try:
                r = requests.get(
                    self.WIKIDATA_ENDPOINT,
                    params={
                        "format": "json",
                        "query": self.WIKIDATA_QUERY_TEMPLATE.format(
                            open_tree_taxon_id=taxon_id,
                        ),
                    },
                    headers={"User-Agent": self._wikidata_user_agent},
                    timeout=30,
                )
            except requests.exceptions.RequestException as exc:
                self._wikidata_by_open_tree_taxon_id[taxon_id] = None
                logger.warning(
                    "Wikidata SPARQL request failed for open_tree_taxon_id=%s (%s: %s); "
                    "skipping wikidata enrichment for this match (cached as failed).",
                    taxon_id, type(exc).__name__, exc,
                )
                continue

            if r.status_code != 200:
                self._wikidata_by_open_tree_taxon_id[taxon_id] = None
                logger.warning(
                    "Wikidata SPARQL returned HTTP %d for open_tree_taxon_id=%s; "
                    "skipping wikidata enrichment for this match (cached as failed).",
                    r.status_code, taxon_id,
                )
                continue

            wikidata = WikidataOTTQuery.from_dict(r.json())
            self._wikidata_by_open_tree_taxon_id[taxon_id] = wikidata
            match.set_wikidata(wikidata)

        return matches

    def enhance(self, analysis: Analysis) -> Analysis:
        """Return the analysis with OTT matches for its source organism appended.

        No-op (returns the analysis unchanged) when the sample has no usable
        source taxon, so callers don't need to guard first.
        """
        if not analysis.has_source_taxon:
            return analysis
        genus, species = analysis.genus_and_species
        new_matches = self.retrieve_matches(genus, species)
        return analysis.model_copy(
            update={"ott_matches": [*analysis.ott_matches, *new_matches]}
        )
