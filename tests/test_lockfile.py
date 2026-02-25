"""Unit tests for lockfile management (TDD Red phase)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from cli.lockfile import (
    LOCKFILE_HEADER,
    WorkflowLock,
    get_lockfile_path,
    load_lockfile,
    read_lockfile,
    remove_lockfile,
    save_lockfile,
    update_lockfile,
    write_lockfile,
)

# --- Test Fixtures ---


@pytest.fixture
def sample_lockfile_data() -> dict:
    """Sample lockfile data for testing."""
    return {
        'workflow_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
        'organization_id': '9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d',
        'version': 1,
        'instance': 'https://api.example.com',
        'nodes': {
            'upload': 'a1b2c3d4-e5f6-7890-abcd-ef1234567890',
            'extract': 'b2c3d4e5-f6a7-8901-bcde-f12345678901',
        },
        'edges': {'upload->extract': 1001},
        'pushed_at': '2026-02-15T14:30:00+00:00',
    }


@pytest.fixture
def sample_lockfile(sample_lockfile_data: dict) -> WorkflowLock:
    """Sample WorkflowLock instance."""
    return WorkflowLock.from_yaml_dict(sample_lockfile_data)


@pytest.fixture
def temp_workflow_file(tmp_path: Path) -> Path:
    """Create a temporary workflow file."""
    workflow_path = tmp_path / 'test.workflow.yaml'
    workflow_path.write_text('name: test\nversion: 1\n')
    return workflow_path


@pytest.fixture
def temp_lockfile(tmp_path: Path, sample_lockfile_data: dict) -> Path:
    """Create a temporary lockfile."""
    lockfile_path = tmp_path / 'test.workflow.lock'
    with lockfile_path.open('w') as f:
        f.write(LOCKFILE_HEADER)
        yaml.safe_dump(sample_lockfile_data, f)
    return lockfile_path


# --- WorkflowLock Model Tests ---


def test_workflow_lock_creation(sample_lockfile: WorkflowLock):
    """Test WorkflowLock model creation."""
    assert sample_lockfile.workflow_id == UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6')
    assert sample_lockfile.organization_id == UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d')
    assert sample_lockfile.version == 1
    assert sample_lockfile.instance == 'https://api.example.com'
    assert len(sample_lockfile.nodes) == 2
    assert len(sample_lockfile.edges) == 1


def test_workflow_lock_validation_invalid_version():
    """Test WorkflowLock validation rejects unsupported versions."""
    with pytest.raises(ValueError, match='Unsupported lockfile version'):
        WorkflowLock(
            workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
            organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
            version=999,  # Invalid version
            instance='https://api.example.com',
            pushed_at=datetime.now(UTC),
        )


def test_workflow_lock_validation_empty_instance():
    """Test WorkflowLock validation rejects empty instance URLs."""
    with pytest.raises(ValueError, match='Instance URL cannot be empty'):
        WorkflowLock(
            workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
            organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
            version=1,
            instance='',  # Empty instance
            pushed_at=datetime.now(UTC),
        )


def test_workflow_lock_to_yaml_dict(sample_lockfile: WorkflowLock):
    """Test WorkflowLock serialization to YAML dict."""
    data = sample_lockfile.to_yaml_dict()
    assert isinstance(data['workflow_id'], str)
    assert isinstance(data['organization_id'], str)
    assert isinstance(data['nodes']['upload'], str)
    assert data['version'] == 1
    assert data['instance'] == 'https://api.example.com'


def test_workflow_lock_from_yaml_dict(sample_lockfile_data: dict):
    """Test WorkflowLock deserialization from YAML dict."""
    lock = WorkflowLock.from_yaml_dict(sample_lockfile_data)
    assert isinstance(lock.workflow_id, UUID)
    assert isinstance(lock.organization_id, UUID)
    assert isinstance(lock.nodes['upload'], UUID)


def test_workflow_lock_get_node_uuid(sample_lockfile: WorkflowLock):
    """Test retrieving node UUID by slug."""
    upload_uuid = sample_lockfile.get_node_uuid('upload')
    assert upload_uuid == UUID('a1b2c3d4-e5f6-7890-abcd-ef1234567890')

    missing = sample_lockfile.get_node_uuid('nonexistent')
    assert missing is None


def test_workflow_lock_get_edge_id(sample_lockfile: WorkflowLock):
    """Test retrieving edge ID by source and target slugs."""
    edge_id = sample_lockfile.get_edge_id('upload', 'extract')
    assert edge_id == 1001

    missing = sample_lockfile.get_edge_id('nonexistent', 'extract')
    assert missing is None


def test_workflow_lock_set_node_uuid(sample_lockfile: WorkflowLock):
    """Test setting node UUID."""
    new_uuid = UUID('12345678-1234-1234-1234-123456789012')
    sample_lockfile.set_node_uuid('new_node', new_uuid)
    assert sample_lockfile.get_node_uuid('new_node') == new_uuid


def test_workflow_lock_set_edge_id(sample_lockfile: WorkflowLock):
    """Test setting edge ID."""
    sample_lockfile.set_edge_id('extract', 'output', 1002)
    assert sample_lockfile.get_edge_id('extract', 'output') == 1002


def test_workflow_lock_remove_node(sample_lockfile: WorkflowLock):
    """Test removing node mapping."""
    assert sample_lockfile.remove_node('upload') is True
    assert sample_lockfile.get_node_uuid('upload') is None
    assert sample_lockfile.remove_node('upload') is False  # Already removed


def test_workflow_lock_remove_edge(sample_lockfile: WorkflowLock):
    """Test removing edge mapping."""
    assert sample_lockfile.remove_edge('upload', 'extract') is True
    assert sample_lockfile.get_edge_id('upload', 'extract') is None
    assert sample_lockfile.remove_edge('upload', 'extract') is False  # Already removed


# --- Lockfile Path Utilities ---


def test_get_lockfile_path():
    """Test lockfile path generation."""
    workflow_path = Path('/path/to/my.workflow.yaml')
    lockfile_path = get_lockfile_path(workflow_path)
    assert lockfile_path == Path('/path/to/my.workflow.lock')


def test_get_lockfile_path_no_yaml_suffix():
    """Test lockfile path generation for files without .yaml suffix."""
    workflow_path = Path('/path/to/workflow.txt')
    lockfile_path = get_lockfile_path(workflow_path)
    assert lockfile_path == Path('/path/to/workflow.txt.lock')


# --- Read Lockfile Tests (TDD Red) ---


def test_read_lockfile_success(temp_lockfile: Path):
    """Test reading a valid lockfile."""
    lock = read_lockfile(temp_lockfile)
    assert lock.workflow_id == UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6')
    assert lock.organization_id == UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d')
    assert len(lock.nodes) == 2
    assert len(lock.edges) == 1


def test_read_lockfile_not_found():
    """Test reading a non-existent lockfile."""
    with pytest.raises(FileNotFoundError):
        read_lockfile(Path('/nonexistent/lockfile.lock'))


def test_read_lockfile_invalid_yaml(tmp_path: Path):
    """Test reading a lockfile with invalid YAML."""
    invalid_lockfile = tmp_path / 'invalid.workflow.lock'
    invalid_lockfile.write_text('invalid: yaml: syntax::')

    with pytest.raises(yaml.YAMLError):
        read_lockfile(invalid_lockfile)


def test_read_lockfile_invalid_schema(tmp_path: Path):
    """Test reading a lockfile with invalid schema."""
    invalid_lockfile = tmp_path / 'invalid.workflow.lock'
    with invalid_lockfile.open('w') as f:
        f.write(LOCKFILE_HEADER)
        yaml.safe_dump({'invalid': 'schema'}, f)

    with pytest.raises(ValueError):
        read_lockfile(invalid_lockfile)


def test_load_lockfile_exists(temp_lockfile: Path):
    """Test load_lockfile returns WorkflowLock if file exists."""
    lock = load_lockfile(temp_lockfile)
    assert lock is not None
    assert isinstance(lock, WorkflowLock)


def test_load_lockfile_not_exists(tmp_path: Path):
    """Test load_lockfile returns None if file does not exist."""
    lockfile_path = tmp_path / 'nonexistent.workflow.lock'
    lock = load_lockfile(lockfile_path)
    assert lock is None


# --- Write Lockfile Tests (TDD Red) ---


def test_write_lockfile_success(tmp_path: Path, sample_lockfile: WorkflowLock):
    """Test writing a lockfile to disk."""
    lockfile_path = tmp_path / 'new.workflow.lock'
    write_lockfile(lockfile_path, sample_lockfile)

    assert lockfile_path.exists()
    content = lockfile_path.read_text()
    assert LOCKFILE_HEADER in content
    assert 'workflow_id: 3fa85f64-5717-4562-b3fc-2c963f66afa6' in content


def test_write_lockfile_creates_parent_directory(tmp_path: Path, sample_lockfile: WorkflowLock):
    """Test write_lockfile creates parent directories if needed."""
    lockfile_path = tmp_path / 'nested' / 'dir' / 'new.workflow.lock'
    write_lockfile(lockfile_path, sample_lockfile)

    assert lockfile_path.exists()
    assert lockfile_path.parent.exists()


def test_write_lockfile_overwrites_existing(temp_lockfile: Path):
    """Test write_lockfile overwrites existing file."""
    new_lock = WorkflowLock(
        workflow_id=UUID('00000000-0000-0000-0000-000000000000'),
        organization_id=UUID('11111111-1111-1111-1111-111111111111'),
        version=1,
        instance='https://new.example.com',
        pushed_at=datetime.now(UTC),
    )
    write_lockfile(temp_lockfile, new_lock)

    content = temp_lockfile.read_text()
    assert '00000000-0000-0000-0000-000000000000' in content


def test_save_lockfile_creates_new(
    tmp_path: Path, temp_workflow_file: Path, sample_lockfile: WorkflowLock
):
    """Test save_lockfile creates a new lockfile from workflow path."""
    save_lockfile(temp_workflow_file, sample_lockfile)

    lockfile_path = get_lockfile_path(temp_workflow_file)
    assert lockfile_path.exists()

    lock = read_lockfile(lockfile_path)
    assert lock.workflow_id == sample_lockfile.workflow_id


# --- Update Lockfile Tests (TDD Red) ---


def test_update_lockfile_modify_existing(temp_lockfile: Path):
    """Test updating an existing lockfile in place."""
    lock = read_lockfile(temp_lockfile)
    lock.set_node_uuid('new_node', UUID('99999999-9999-9999-9999-999999999999'))
    lock.pushed_at = datetime(2026, 2, 18, 10, 0, 0, tzinfo=UTC)

    update_lockfile(temp_lockfile, lock)

    updated = read_lockfile(temp_lockfile)
    assert updated.get_node_uuid('new_node') == UUID('99999999-9999-9999-9999-999999999999')
    assert updated.pushed_at == datetime(2026, 2, 18, 10, 0, 0, tzinfo=UTC)


def test_update_lockfile_adds_nodes_and_edges(temp_lockfile: Path):
    """Test update_lockfile can add new nodes and edges."""
    lock = read_lockfile(temp_lockfile)
    lock.set_node_uuid('transform', UUID('cccccccc-cccc-cccc-cccc-cccccccccccc'))
    lock.set_edge_id('extract', 'transform', 1003)

    update_lockfile(temp_lockfile, lock)

    updated = read_lockfile(temp_lockfile)
    assert len(updated.nodes) == 3  # upload, extract, transform
    assert len(updated.edges) == 2  # upload->extract, extract->transform


def test_update_lockfile_removes_nodes_and_edges(temp_lockfile: Path):
    """Test update_lockfile can remove nodes and edges."""
    lock = read_lockfile(temp_lockfile)
    lock.remove_node('extract')
    lock.remove_edge('upload', 'extract')

    update_lockfile(temp_lockfile, lock)

    updated = read_lockfile(temp_lockfile)
    assert len(updated.nodes) == 1  # Only upload
    assert len(updated.edges) == 0


# --- Remove Lockfile Tests ---


def test_remove_lockfile_exists(temp_lockfile: Path):
    """Test removing an existing lockfile."""
    assert temp_lockfile.exists()
    remove_lockfile(temp_lockfile)
    assert not temp_lockfile.exists()


def test_remove_lockfile_not_exists(tmp_path: Path):
    """Test removing a non-existent lockfile does not raise error."""
    lockfile_path = tmp_path / 'nonexistent.workflow.lock'
    remove_lockfile(lockfile_path)  # Should not raise
    assert not lockfile_path.exists()


# --- Edge Cases ---


def test_workflow_lock_empty_nodes_and_edges():
    """Test WorkflowLock with empty nodes and edges."""
    lock = WorkflowLock(
        workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
        organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
        version=1,
        instance='https://api.example.com',
        nodes={},
        edges={},
        pushed_at=datetime.now(UTC),
    )
    assert len(lock.nodes) == 0
    assert len(lock.edges) == 0


def test_workflow_lock_roundtrip_serialization(sample_lockfile: WorkflowLock):
    """Test WorkflowLock round-trip serialization (to_yaml_dict -> from_yaml_dict)."""
    data = sample_lockfile.to_yaml_dict()
    restored = WorkflowLock.from_yaml_dict(data)

    assert restored.workflow_id == sample_lockfile.workflow_id
    assert restored.organization_id == sample_lockfile.organization_id
    assert restored.nodes == sample_lockfile.nodes
    assert restored.edges == sample_lockfile.edges
    assert restored.pushed_at == sample_lockfile.pushed_at


# ============================================================================
# Dependencies Field Tests (RAG-950: Lockfile dependency caching)
# ============================================================================


class TestLockfileDependencies:
    """Test the dependencies field on WorkflowLock."""

    def test_dependencies_default_empty(self):
        """Test that dependencies field defaults to empty dict."""
        lock = WorkflowLock(
            workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
            organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
            version=1,
            instance='https://api.example.com',
            pushed_at=datetime.now(UTC),
        )
        assert lock.dependencies == {}

    def test_dependencies_stores_agent_mappings(self):
        """Test that dependencies can store agent name->UUID mappings."""
        agent_uuid = UUID('12345678-1234-1234-1234-123456789012')
        lock = WorkflowLock(
            workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
            organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
            version=1,
            instance='https://api.example.com',
            dependencies={'agent:Invoice Agent': agent_uuid},
            pushed_at=datetime.now(UTC),
        )
        assert lock.dependencies['agent:Invoice Agent'] == agent_uuid

    def test_dependencies_stores_kb_mappings(self):
        """Test that dependencies can store KB name->UUID mappings."""
        kb_uuid = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        lock = WorkflowLock(
            workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
            organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
            version=1,
            instance='https://api.example.com',
            dependencies={'kb:Company Policies': kb_uuid},
            pushed_at=datetime.now(UTC),
        )
        assert lock.dependencies['kb:Company Policies'] == kb_uuid

    def test_dependencies_to_yaml_dict(self):
        """Test dependencies serialization in to_yaml_dict."""
        agent_uuid = UUID('12345678-1234-1234-1234-123456789012')
        kb_uuid = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        lock = WorkflowLock(
            workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
            organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
            version=1,
            instance='https://api.example.com',
            dependencies={
                'agent:Invoice Agent': agent_uuid,
                'kb:Company Policies': kb_uuid,
            },
            pushed_at=datetime.now(UTC),
        )
        data = lock.to_yaml_dict()
        assert 'dependencies' in data
        assert data['dependencies']['agent:Invoice Agent'] == str(agent_uuid)
        assert data['dependencies']['kb:Company Policies'] == str(kb_uuid)

    def test_dependencies_from_yaml_dict(self):
        """Test dependencies deserialization from YAML dict."""
        data = {
            'workflow_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
            'organization_id': '9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d',
            'version': 1,
            'instance': 'https://api.example.com',
            'nodes': {},
            'edges': {},
            'dependencies': {
                'agent:Invoice Agent': '12345678-1234-1234-1234-123456789012',
                'kb:Company Policies': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            },
            'pushed_at': '2026-02-15T14:30:00+00:00',
        }
        lock = WorkflowLock.from_yaml_dict(data)
        assert lock.dependencies['agent:Invoice Agent'] == UUID(
            '12345678-1234-1234-1234-123456789012'
        )
        assert lock.dependencies['kb:Company Policies'] == UUID(
            'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        )

    def test_dependencies_roundtrip(self):
        """Test dependencies survive serialization round-trip."""
        agent_uuid = UUID('12345678-1234-1234-1234-123456789012')
        lock = WorkflowLock(
            workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
            organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
            version=1,
            instance='https://api.example.com',
            dependencies={'agent:Test Agent': agent_uuid},
            pushed_at=datetime.now(UTC),
        )
        data = lock.to_yaml_dict()
        restored = WorkflowLock.from_yaml_dict(data)
        assert restored.dependencies == lock.dependencies

    def test_dependencies_backward_compat_no_field(self):
        """Test that lockfiles without dependencies field can still be loaded."""
        data = {
            'workflow_id': '3fa85f64-5717-4562-b3fc-2c963f66afa6',
            'organization_id': '9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d',
            'version': 1,
            'instance': 'https://api.example.com',
            'nodes': {},
            'edges': {},
            'pushed_at': '2026-02-15T14:30:00+00:00',
            # No 'dependencies' key at all
        }
        lock = WorkflowLock.from_yaml_dict(data)
        assert lock.dependencies == {}

    def test_dependencies_written_to_disk_and_read_back(self, tmp_path: Path):
        """Test that dependencies survive write/read to actual YAML file."""
        agent_uuid = UUID('12345678-1234-1234-1234-123456789012')
        kb_uuid = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
        lock = WorkflowLock(
            workflow_id=UUID('3fa85f64-5717-4562-b3fc-2c963f66afa6'),
            organization_id=UUID('9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d'),
            version=1,
            instance='https://api.example.com',
            dependencies={
                'agent:Test Agent': agent_uuid,
                'kb:Test KB': kb_uuid,
            },
            pushed_at=datetime.now(UTC),
        )

        lockfile_path = tmp_path / 'test.workflow.lock'
        write_lockfile(lockfile_path, lock)

        restored = read_lockfile(lockfile_path)
        assert restored.dependencies['agent:Test Agent'] == agent_uuid
        assert restored.dependencies['kb:Test KB'] == kb_uuid
