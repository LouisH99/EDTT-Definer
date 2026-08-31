from __future__ import annotations

from pathlib import Path

import pytest

from edtt_definer.parser import load_document, serialize_document
from edtt_definer.validator import Severity, validate_document


EXAMPLE_FILES = sorted((Path(__file__).parents[1] / "time trials").glob("*.txt"))


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda path: path.name)
def test_example_file_is_lossless_and_has_no_blocking_errors(path: Path) -> None:
    document = load_document(path)

    assert serialize_document(document).encode(document.encoding) == path.read_bytes()
    assert not [
        issue.message
        for issue in validate_document(document)
        if issue.severity is Severity.ERROR
    ]
