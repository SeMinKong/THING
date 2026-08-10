"""Uploader environment configuration tests."""

import pytest

from thing_logger.uploader_contract import ConfigError
from thing_logger.uploader_contract import load_config


def test_load_config_reads_env_file_and_environment_wins(tmp_path):
    """A protected env file supplies defaults and direct env overrides it."""
    env_file = tmp_path / 'thing-uploader.env'
    env_file.write_text(
        '\n'.join([
            'THING_UPLOADER_TOKEN="file-token"',
            'THING_EC2_UPLOAD_URL="https://example.test/upload"',
            'THING_UPLOADER_SOCKET_MODE="0660"',
            'THING_UPLOADER_TLS_VERIFY="true"',
        ]),
        encoding='utf-8',
    )

    config = load_config(
        {'THING_UPLOADER_TOKEN': 'environment-token'},
        env_file_path=str(env_file),
    )

    assert config.device_token == 'environment-token'
    assert config.ec2_upload_url == 'https://example.test/upload'
    assert config.socket_mode == 0o660
    assert config.tls_verify is True


@pytest.mark.parametrize(
    'contents',
    [
        'MISSING_EQUALS',
        '1INVALID=value',
        'THING_UPLOADER_TOKEN="unterminated',
    ],
)
def test_load_config_rejects_malformed_env_file(tmp_path, contents):
    """Malformed files fail without exposing their values in the error."""
    env_file = tmp_path / 'thing-uploader.env'
    env_file.write_text(contents, encoding='utf-8')

    with pytest.raises(ConfigError, match='uploader env 파일'):
        load_config({}, env_file_path=str(env_file))


def test_load_config_rejects_missing_explicit_env_file(tmp_path):
    """An explicitly selected missing file is a startup error."""
    with pytest.raises(ConfigError, match='찾을 수 없다'):
        load_config({}, env_file_path=str(tmp_path / 'missing.env'))
