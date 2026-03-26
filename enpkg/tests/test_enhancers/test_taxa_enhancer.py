"""Test suite for the Taxa Enhancer."""
import logging
from time import time

from enpkg.monolith.enhancers.taxa_enhancer import TaxaEnhancer
from enpkg.monolith.data.otl_class import Match


class TestTaxaEnhancer:
    """Test class for TaxaEnhancer."""

    def test_initialization(self, taxa_enhancer: TaxaEnhancer) -> None:
        """Test that TaxaEnhancer correctly initializes.

        Args:
            taxa_enhancer: The taxa enhancer instance correctly initialized via fixtures.
        """
        assert taxa_enhancer is not None, "TaxaEnhancer should not be None."
        assert taxa_enhancer.name() == "Taxonomical Enhancer", "TaxaEnhancer name property should match."

    def test_enhance_taxa(self, taxa_enhancer: TaxaEnhancer, logger: logging.Logger) -> None:
        """Test the taxa enhancement functionality.

        Args:
            taxa_enhancer: The fully populated TaxaEnhancer instance.
            logger: A contextual logger.
        """
        genus = "Homo"
        species = "sapiens"

        start = time()
        matches = taxa_enhancer.enhance(genus, species)
        logger.info(f"Retrieved {len(matches)} taxa matches in {time() - start:.2f} seconds")

        assert isinstance(matches, list), "Enhance should return a list of Match objects."
        assert len(matches) > 0, f"No matches found for {genus} {species}."
        
        # Verify the structure of the returned objects
        first_match = matches[0]
        assert isinstance(first_match, Match), "Elements of returned list should be Match instances."
        assert hasattr(first_match, "ott_id"), "Match object should have 'ott_id'."
        
        # Ensure lineage and wikidata attributes exist (even if None, depending on API response)
        assert first_match.lineage is not None, "Match object should have lineage set."
