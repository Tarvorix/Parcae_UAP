from pathlib import Path

import pytest
from uap_observer.fixtures import generate


@pytest.fixture(scope="session")
def fixture_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return generate(tmp_path_factory.mktemp("fixture"))
