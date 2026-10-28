"""Keep test artifacts within the project with inherited Windows directory ACLs."""
from pathlib import Path
import uuid
import pytest


@pytest.fixture
def tmp_path(request):
    root=Path(__file__).resolve().parents[2]/'output'/'block_builder_checks'/'test_runs'
    path=root/(request.node.name[:65]+'_'+uuid.uuid4().hex[:10])
    path.mkdir(parents=True)
    return path
