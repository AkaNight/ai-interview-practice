import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.demo_service import DemoService


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", ai_mode="demo")


@pytest.fixture
def ai():
    return DemoService()


@pytest.fixture
def client(settings, ai):
    with TestClient(create_app(settings, ai)) as client:
        yield client
