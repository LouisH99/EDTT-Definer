from __future__ import annotations

import json

import pytest

from edtt_definer.updates import (
    ReleaseInfo,
    is_newer_release,
    is_trusted_release_url,
    parse_release_payload,
    parse_version_tag,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("v0.5", (0, 5, 0)),
        ("V0.5", (0, 5, 0)),
        ("0.5.1", (0, 5, 1)),
        (" v12.34.56 ", (12, 34, 56)),
        ("v0.5-beta", None),
        ("release-0.5", None),
        ("0.5.1.2", None),
    ],
)
def test_parse_version_tag(value, expected) -> None:
    assert parse_version_tag(value) == expected


def test_release_payload_and_version_comparison() -> None:
    payload = json.dumps(
        {
            "tag_name": "V0.5.1",
            "name": "EDTT Definer v0.5.1",
            "html_url": "https://github.com/LouisH99/EDTT-Definer/releases/tag/v0.5.1",
        }
    )
    release = parse_release_payload(payload)
    assert release.normalized_version == "0.5.1"
    assert release.title == "EDTT Definer v0.5.1"
    assert is_newer_release(release, "0.5.0")
    assert not is_newer_release(release, "v0.5.1")


def test_stable_release_is_newer_than_matching_development_build() -> None:
    release = parse_release_payload(
        json.dumps(
            {
                "tag_name": "v0.6",
                "name": "EDTT Definer v0.6",
                "html_url": "https://github.com/LouisH99/EDTT-Definer/releases/tag/v0.6",
            }
        )
    )

    assert is_newer_release(release, "0.6.0.dev0")
    assert not is_newer_release(release, "0.6.0")
    assert not is_newer_release(release, "0.7.0.dev0")


def test_patch_development_build_waits_for_matching_stable_patch() -> None:
    v06 = ReleaseInfo(
        "v0.6", (0, 6, 0),
        "https://github.com/LouisH99/EDTT-Definer/releases/tag/v0.6", "v0.6",
    )
    v061 = ReleaseInfo(
        "v0.6.1", (0, 6, 1),
        "https://github.com/LouisH99/EDTT-Definer/releases/tag/v0.6.1", "v0.6.1",
    )

    assert not is_newer_release(v06, "0.6.1.dev0")
    assert is_newer_release(v061, "0.6.1.dev0")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/LouisH99/EDTT-Definer/releases/tag/v0.5",
        "https://example.com/LouisH99/EDTT-Definer/releases/tag/v0.5",
        "https://github.com/SomeoneElse/EDTT-Definer/releases/tag/v0.5",
        "https://github.com/LouisH99/EDTT-Definer/issues",
        "https://github.com@evil.example/LouisH99/EDTT-Definer/releases/tag/v0.5",
        "https://github.com/LouisH99/EDTT-Definer/releases/tag/v0.5?download=1",
    ],
)
def test_untrusted_release_urls_are_rejected(url) -> None:
    assert not is_trusted_release_url(url)


def test_valid_repository_release_url_is_trusted() -> None:
    assert is_trusted_release_url(
        "https://github.com/LouisH99/EDTT-Definer/releases/tag/v0.5"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"tag_name":"v0.5"}',
        '{"tag_name":"preview","html_url":"https://github.com/LouisH99/EDTT-Definer/releases/tag/preview"}',
        '{"tag_name":"v0.5","html_url":"https://evil.example/release"}',
    ],
)
def test_malformed_or_untrusted_release_payloads_are_rejected(payload) -> None:
    with pytest.raises(ValueError):
        parse_release_payload(payload)
