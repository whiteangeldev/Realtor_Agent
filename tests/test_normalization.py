from realtor_agent.normalization import normalize_bcfsa_record


def test_normalization_formats_name_noise() -> None:
    normalized = normalize_bcfsa_record(
        {
            "licence_number": "LIC-1",
            "name": "' Sam Ladan",
            "business_name": "ABC Realty",
        },
        {"source": "BCFSA", "fetched_at": "2026-06-22T00:00:00+00:00"},
    )

    assert normalized["name"] == "Sam Ladan"


def test_normalization_removes_different_leading_symbols() -> None:
    normalized = normalize_bcfsa_record(
        {
            "licence_number": "LIC-2",
            "name": "., Amandeep",
            "business_name": "ABC Realty",
        },
        {"source": "BCFSA", "fetched_at": "2026-06-22T00:00:00+00:00"},
    )

    assert normalized["name"] == "Amandeep"


def test_normalization_preserves_real_name_punctuation() -> None:
    normalized = normalize_bcfsa_record(
        {
            "licence_number": "LIC-3",
            "name": "Eric J. Adams",
            "business_name": "ABC Realty",
        },
        {"source": "BCFSA", "fetched_at": "2026-06-22T00:00:00+00:00"},
    )

    assert normalized["name"] == "Eric J. Adams"


def test_normalization_preserves_apostrophe_inside_name() -> None:
    normalized = normalize_bcfsa_record(
        {
            "licence_number": "LIC-4",
            "name": "Corinna Patricia O'Brien",
            "business_name": "ABC Realty",
        },
        {"source": "BCFSA", "fetched_at": "2026-06-22T00:00:00+00:00"},
    )

    assert normalized["name"] == "Corinna Patricia O'Brien"


def test_normalization_removes_trailing_comma() -> None:
    normalized = normalize_bcfsa_record(
        {
            "licence_number": "LIC-5",
            "name": "Ishpreet Singh - Singh,",
            "business_name": "ABC Realty",
        },
        {"source": "BCFSA", "fetched_at": "2026-06-22T00:00:00+00:00"},
    )

    assert normalized["name"] == "Ishpreet Singh - Singh"


def test_normalization_removes_dangling_trailing_hyphen() -> None:
    normalized = normalize_bcfsa_record(
        {
            "licence_number": "LIC-6",
            "name": "Ishpreet Singh -",
            "business_name": "ABC Realty",
        },
        {"source": "BCFSA", "fetched_at": "2026-06-22T00:00:00+00:00"},
    )

    assert normalized["name"] == "Ishpreet Singh"


def test_normalization_removes_outside_trailing_period() -> None:
    normalized = normalize_bcfsa_record(
        {
            "licence_number": "LIC-7",
            "name": "Sam Ladan.",
            "business_name": "ABC Realty",
        },
        {"source": "BCFSA", "fetched_at": "2026-06-22T00:00:00+00:00"},
    )

    assert normalized["name"] == "Sam Ladan"


def test_normalization_preserves_valid_suffix_period() -> None:
    normalized = normalize_bcfsa_record(
        {
            "licence_number": "LIC-8",
            "name": "Thomas Jones Jr.",
            "business_name": "ABC Realty",
        },
        {"source": "BCFSA", "fetched_at": "2026-06-22T00:00:00+00:00"},
    )

    assert normalized["name"] == "Thomas Jones Jr."
