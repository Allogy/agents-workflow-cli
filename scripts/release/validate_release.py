from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

SEMVER_RE = re.compile(r'^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')


@dataclass(frozen=True)
class PackageVersions:
    pyproject: str
    package: str


@dataclass(frozen=True)
class PackageConfig:
    name: str
    pyproject_path: Path
    init_path: Path
    tag_prefix: str


REPO_ROOT = Path(__file__).resolve().parents[2]

PACKAGE_CONFIGS = {
    'cli': PackageConfig(
        name='cli',
        pyproject_path=REPO_ROOT / 'pyproject.toml',
        init_path=REPO_ROOT / 'src' / 'cli' / '__init__.py',
        tag_prefix='cli-v',
    ),
    'models': PackageConfig(
        name='models',
        pyproject_path=REPO_ROOT / 'shared-models' / 'pyproject.toml',
        init_path=REPO_ROOT / 'shared-models' / 'src' / 'workflow_models' / '__init__.py',
        tag_prefix='models-v',
    ),
}


def is_semver(version: str) -> bool:
    return bool(SEMVER_RE.match(version))


def read_pyproject_version(path: Path) -> str:
    import tomllib

    data = tomllib.loads(path.read_text(encoding='utf-8'))
    version = data.get('project', {}).get('version')
    if not version:
        raise ValueError(f'Missing [project].version in {path}')
    return version


def read_init_version(path: Path) -> str:
    content = path.read_text(encoding='utf-8')
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
    if not match:
        raise ValueError(f'__version__ not found in {path}')
    return match.group(1)


def get_package_versions(package: str) -> PackageVersions:
    config = PACKAGE_CONFIGS[package]
    pyproject_version = read_pyproject_version(config.pyproject_path)
    package_version = read_init_version(config.init_path)
    return PackageVersions(pyproject=pyproject_version, package=package_version)


def tag_matches_version(tag_prefix: str, version: str, tag: str) -> bool:
    return tag == f'{tag_prefix}{version}'


def validate_package(package: str, tag: str | None = None) -> list[str]:
    errors: list[str] = []
    config = PACKAGE_CONFIGS[package]
    versions = get_package_versions(package)

    if not is_semver(versions.pyproject):
        errors.append(f'{config.name}: pyproject version is not semver: {versions.pyproject}')

    if versions.pyproject != versions.package:
        errors.append(
            f'{config.name}: pyproject version {versions.pyproject} '
            f'does not match package version {versions.package}'
        )

    if tag and not tag_matches_version(config.tag_prefix, versions.pyproject, tag):
        errors.append(
            f'{config.name}: tag {tag} does not match expected '
            f'{config.tag_prefix}{versions.pyproject}'
        )

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Validate release versions and tags.')
    parser.add_argument(
        '--package',
        choices=['cli', 'models', 'all'],
        default='all',
        help='Package to validate (default: all).',
    )
    parser.add_argument(
        '--tag',
        default=None,
        help='Release tag to validate (e.g., cli-v1.2.3 or models-v1.2.3).',
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.package == 'all' and args.tag:
        print('Cannot validate a single tag against multiple packages.', file=sys.stderr)
        return 2

    packages = PACKAGE_CONFIGS.keys() if args.package == 'all' else [args.package]
    errors: list[str] = []

    for package in packages:
        errors.extend(validate_package(package, tag=args.tag))

    if errors:
        print('Release validation failed:', file=sys.stderr)
        for error in errors:
            print(f'  - {error}', file=sys.stderr)
        return 1

    for package in packages:
        versions = get_package_versions(package)
        print(f'{package}: version {versions.pyproject} OK')

    if args.tag:
        print(f'tag: {args.tag} OK')

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
