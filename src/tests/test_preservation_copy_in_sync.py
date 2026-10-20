"""src/pipeline/preservation.py is a verbatim copy of the service's domain module.

Two copies exist so neither side imports the other (src/pipeline must not depend on
service/, and service/ only reaches src/pipeline through adapters). This test is what
stops them drifting apart: edit service/ai_engine/domain/preservation.py, then copy it.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_pipeline_copy_matches_service_domain_module():
    service = REPO_ROOT / "service/ai_engine/domain/preservation.py"
    pipeline = REPO_ROOT / "src/pipeline/preservation.py"
    assert pipeline.read_bytes() == service.read_bytes(), (
        "src/pipeline/preservation.py has drifted from the service domain module -- "
        f"cp {service.relative_to(REPO_ROOT)} {pipeline.relative_to(REPO_ROOT)}"
    )
