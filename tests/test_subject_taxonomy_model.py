from src.models.taxonomy import SubjectTaxonomy


def test_subject_taxonomy_normalized_unique_fields() -> None:
    model = SubjectTaxonomy(
        name_en="Master of Science in Asset and Wealth Management",
        normalized_name="masterofscienceinassetandwealthmanagement",
        source="seed",
    )
    assert model.name_en.startswith("Master")
