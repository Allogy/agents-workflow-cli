from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'release' / 'validate_release.py'

spec = spec_from_file_location('validate_release', SCRIPT_PATH)
assert spec and spec.loader
validate_release = module_from_spec(spec)
sys.modules[spec.name] = validate_release
spec.loader.exec_module(validate_release)


def test_cli_versions_are_semver_and_match():
    versions = validate_release.get_package_versions('cli')

    assert validate_release.is_semver(versions.pyproject)
    assert versions.pyproject == versions.package


def test_models_versions_are_semver_and_match():
    versions = validate_release.get_package_versions('models')

    assert validate_release.is_semver(versions.pyproject)
    assert versions.pyproject == versions.package


def test_tag_validation_cli_matches_version():
    versions = validate_release.get_package_versions('cli')

    assert validate_release.tag_matches_version(
        'cli-v',
        versions.pyproject,
        f'cli-v{versions.pyproject}',
    )


def test_tag_validation_models_matches_version():
    versions = validate_release.get_package_versions('models')

    assert validate_release.tag_matches_version(
        'models-v',
        versions.pyproject,
        f'models-v{versions.pyproject}',
    )


def test_semver_rejects_invalid_versions():
    assert not validate_release.is_semver('0.1')
    assert not validate_release.is_semver('v1.2.3')
    assert not validate_release.is_semver('1.2.3.4')
