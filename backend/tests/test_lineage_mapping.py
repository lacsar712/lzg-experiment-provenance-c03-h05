import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user, require_researcher
from app.database import Base, get_db
from app.main import app

# 刻意让两个值长得完全不一样，对调时一眼可辨
DATASET_SHA = hashlib.sha256(b"dataset-v1").hexdigest()  # 64 hex
CODE_COMMIT = "c0ffee1" * 5  # 35 hex，明显不是 sha256


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _compile_jsonb_sqlite(_type, compiler, **kw):
        return "JSON"

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def _override_get_db():
        yield session

    fake_user = {"username": "researcher", "role": "researcher"}
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_current_user] = lambda: fake_user
    app.dependency_overrides[require_researcher] = lambda: fake_user
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        session.close()


def test_lineage_fields_match_detail(client):
    created = client.post(
        "/api/runs",
        json={
            "project": "p1",
            "name": "mapping-check",
            "dataset_content_sha256": DATASET_SHA,
            "code_commit_sha": CODE_COMMIT,
        },
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]

    detail = client.get(f"/api/runs/{run_id}")
    lineage = client.get(f"/api/runs/{run_id}/lineage")
    assert detail.status_code == 200, detail.text
    assert lineage.status_code == 200, lineage.text

    # 详情接口原样返回写入值
    assert detail.json()["dataset_content_sha256"] == DATASET_SHA
    assert detail.json()["code_commit_sha"] == CODE_COMMIT

    # 血缘接口与详情一致（回归：此前两字段在组装时对调）
    assert lineage.json()["dataset_content_sha256"] == DATASET_SHA
    assert lineage.json()["code_commit_sha"] == CODE_COMMIT
    assert lineage.json()["dataset_content_sha256"] == detail.json()["dataset_content_sha256"]
    assert lineage.json()["code_commit_sha"] == detail.json()["code_commit_sha"]
