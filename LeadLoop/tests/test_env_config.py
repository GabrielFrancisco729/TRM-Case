"""Configuration contract for Claude integration: local .env, no secrets in package."""
from pathlib import Path
from dotenv import dotenv_values


def test_env_template_has_required_keys():
    root=Path(__file__).resolve().parents[1]
    config=dotenv_values(root / '.env.example')
    assert 'ANTHROPIC_API_KEY' in config
    assert 'LEADLOOP_LLM_MODEL' in config
    assert config['ANTHROPIC_API_KEY'] == ''


def test_server_loads_local_env_without_overriding_shell():
    root=Path(__file__).resolve().parents[1]
    text=(root / 'server.py').read_text(encoding='utf-8')
    assert "load_dotenv(dotenv_path=BASE / '.env', override=False)" in text
