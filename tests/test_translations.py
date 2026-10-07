import json
import re
from pathlib import Path

import pytest

TRANSLATIONS = Path(__file__).parent.parent / "custom_components/pesc/translations"


@pytest.mark.parametrize("path", sorted(TRANSLATIONS.glob("*.json")), ids=str)
def test_selector_option_keys(path: Path) -> None:
    selectors = json.loads(path.read_text(encoding="utf-8"))["selector"]
    for selector in selectors.values():
        for key in selector.get("options", {}):
            assert re.fullmatch(r"[a-z0-9]([a-z0-9-_]*[a-z0-9])?", key), key
