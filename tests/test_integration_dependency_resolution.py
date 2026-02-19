"""Integration tests for RAG-950 dependency resolution against a live API.

These tests hit the real dev server to verify that agent and knowledge base
name-to-UUID resolution works end-to-end.  They are gated behind the
``integration`` pytest marker so the regular ``uv run pytest`` suite remains
fast and offline.

Run with:
    uv run pytest tests/test_integration_dependency_resolution.py -v \
        --host https://dev.sb.allogy.com \
        --api-key <key> \
        --org <org-uuid>

Or via marker:
    uv run pytest -m integration -v --host ... --api-key ... --org ...
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from workflow_models.wdf import WorkflowDefinition
from workflow_models.wdf.nodes import NodeDefinition

from cli.client import WorkflowClient
from cli.commands.push import (
    DependencyResolutionError,
    _is_uuid,
    resolve_dependencies,
)
from cli.lockfile import WorkflowLock

# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_workflow(nodes: dict[str, NodeDefinition]) -> WorkflowDefinition:
    """Build a minimal WorkflowDefinition with the given nodes."""
    slugs = list(nodes.keys())
    edges = []
    # Chain nodes in order so entry/exit are valid
    for i in range(len(slugs) - 1):
        edges.append({'from': slugs[i], 'to': slugs[i + 1], 'type': 'STATIC'})

    return WorkflowDefinition.model_construct(
        name='Integration Test Workflow',
        description='Auto-generated for integration testing',
        version=1,
        tags=[],
        state_schema={'inputs': {}, 'outputs': {}, 'variables': {}},
        nodes=nodes,
        edges=edges,
        entry=slugs[0],
        exit=slugs[-1],
    )


def _input_node() -> NodeDefinition:
    return NodeDefinition.model_construct(
        type='plain_txt_input',
        execution_mode='INPUT',
        label='Input',
        config={},
    )


# ---------------------------------------------------------------------------
# 1. API connectivity — list agents and knowledge bases
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestApiConnectivity:
    """Verify basic API connectivity and that agents/KBs exist."""

    def test_list_agents_returns_non_empty(self, live_client: WorkflowClient) -> None:
        """The dev org should have at least one agent."""
        agents = live_client.list_agents()
        assert isinstance(agents, list)
        assert len(agents) > 0, 'Expected at least one agent in the dev org'
        # Each agent should have an id and name
        for agent in agents:
            assert 'id' in agent
            assert 'name' in agent
            assert _is_uuid(agent['id']), f'Agent id is not a UUID: {agent["id"]}'

    def test_list_knowledge_bases_returns_non_empty(self, live_client: WorkflowClient) -> None:
        """The dev org should have at least one knowledge base."""
        kbs = live_client.list_knowledge_bases()
        assert isinstance(kbs, list)
        assert len(kbs) > 0, 'Expected at least one KB in the dev org'
        for kb in kbs:
            assert 'id' in kb
            assert 'name' in kb
            assert _is_uuid(kb['id']), f'KB id is not a UUID: {kb["id"]}'


# ---------------------------------------------------------------------------
# 2. Find by name — agent and KB name resolution via API
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFindByName:
    """Verify that find_agent_by_name / find_knowledge_base_by_name work
    against the live API with known resources."""

    def test_find_agent_by_exact_name(self, live_client: WorkflowClient) -> None:
        """'Data Analyst Agent' should resolve to a valid UUID."""
        result = live_client.find_agent_by_name('Data Analyst Agent')
        assert result is not None, "Agent 'Data Analyst Agent' not found"
        assert _is_uuid(result['id'])
        assert result['name'] == 'Data Analyst Agent'

    def test_find_agent_case_insensitive(self, live_client: WorkflowClient) -> None:
        """Agent lookup should be case-insensitive."""
        result = live_client.find_agent_by_name('data analyst agent')
        assert result is not None, 'Case-insensitive agent lookup failed'
        assert result['name'] == 'Data Analyst Agent'

    def test_find_agent_nonexistent_returns_none(self, live_client: WorkflowClient) -> None:
        """A nonsense name should return None, not raise."""
        result = live_client.find_agent_by_name('nonexistent-agent-xyz-999')
        assert result is None

    def test_find_kb_by_exact_name(self, live_client: WorkflowClient) -> None:
        """'standards' knowledge base should resolve to a valid UUID."""
        result = live_client.find_knowledge_base_by_name('standards')
        assert result is not None, "KB 'standards' not found"
        assert _is_uuid(result['id'])
        assert result['name'] == 'standards'

    def test_find_kb_case_insensitive(self, live_client: WorkflowClient) -> None:
        """KB lookup should be case-insensitive."""
        result = live_client.find_knowledge_base_by_name('Standards')
        assert result is not None, 'Case-insensitive KB lookup failed'
        assert result['name'] == 'standards'

    def test_find_kb_nonexistent_returns_none(self, live_client: WorkflowClient) -> None:
        """A nonsense KB name should return None, not raise."""
        result = live_client.find_knowledge_base_by_name('nonexistent-kb-xyz-999')
        assert result is None


# ---------------------------------------------------------------------------
# 3. resolve_dependencies — full 3-tier resolution against live API
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestResolveDependenciesLive:
    """End-to-end dependency resolution using the real API."""

    def test_resolve_agent_by_name(self, live_client: WorkflowClient) -> None:
        """A workflow with agent_name should resolve to a UUID via API."""
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='My Agent',
                    config={'agent_name': 'Data Analyst Agent'},
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client)
        assert 'agent:Data Analyst Agent' in resolved
        agent_uuid = resolved['agent:Data Analyst Agent']
        assert isinstance(agent_uuid, UUID)
        assert str(agent_uuid) == 'e0b3bdc6-9fcb-45ee-8833-26aa5cd1d2e0'

    def test_resolve_kb_by_name(self, live_client: WorkflowClient) -> None:
        """A workflow with knowledge_base_name should resolve to a UUID via API."""
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'retrieval': NodeDefinition.model_construct(
                    type='retrieve',
                    execution_mode='FLOW',
                    label='KB Retrieval',
                    config={'knowledge_base_name': 'standards'},
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client)
        assert 'kb:standards' in resolved
        kb_uuid = resolved['kb:standards']
        assert isinstance(kb_uuid, UUID)
        assert str(kb_uuid) == '6c26048d-2f9c-4177-a126-f2ed8cd02a0e'

    def test_resolve_kb_names_list(self, live_client: WorkflowClient) -> None:
        """knowledge_base_names list should resolve all entries."""
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'rag': NodeDefinition.model_construct(
                    type='rag_agent',
                    execution_mode='MESSAGES',
                    label='RAG Agent',
                    config={
                        'agent_name': 'Data Analyst Agent',
                        'knowledge_base_names': ['standards', 'industry'],
                    },
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client)
        assert 'agent:Data Analyst Agent' in resolved
        assert 'kb:standards' in resolved
        assert 'kb:industry' in resolved
        assert str(resolved['kb:industry']) == 'e70c07d8-8dbe-4833-9e42-a10dd0d21893'

    def test_resolve_nonexistent_agent_raises_with_alternatives(
        self, live_client: WorkflowClient
    ) -> None:
        """Unresolvable agent name should raise DependencyResolutionError
        listing available alternatives."""
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='Bad Agent',
                    config={'agent_name': 'Does Not Exist Agent'},
                ),
            }
        )

        with pytest.raises(DependencyResolutionError, match='Does Not Exist Agent') as exc_info:
            resolve_dependencies(workflow, live_client)

        # Error message should list at least one real agent name
        assert 'Data Analyst Agent' in str(exc_info.value)

    def test_resolve_nonexistent_kb_raises_with_alternatives(
        self, live_client: WorkflowClient
    ) -> None:
        """Unresolvable KB name should raise DependencyResolutionError
        listing available alternatives."""
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'retrieval': NodeDefinition.model_construct(
                    type='retrieve',
                    execution_mode='FLOW',
                    label='Bad KB',
                    config={'knowledge_base_name': 'does-not-exist-kb'},
                ),
            }
        )

        with pytest.raises(DependencyResolutionError, match='does-not-exist-kb') as exc_info:
            resolve_dependencies(workflow, live_client)

        # Error message should list at least one real KB name
        assert 'standards' in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. UUID passthrough — no API call needed when value is already a UUID
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUuidPassthroughLive:
    """Verify that UUID values skip API lookup entirely (still works
    against a live client, but shouldn't trigger extra requests)."""

    def test_agent_uuid_passthrough(self, live_client: WorkflowClient) -> None:
        """When agent_name is already a UUID, resolve_dependencies returns it
        without calling find_agent_by_name."""
        raw_uuid = 'e0b3bdc6-9fcb-45ee-8833-26aa5cd1d2e0'
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='Agent',
                    config={'agent_name': raw_uuid},
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client)
        assert f'agent:{raw_uuid}' in resolved
        assert resolved[f'agent:{raw_uuid}'] == UUID(raw_uuid)

    def test_kb_uuid_passthrough(self, live_client: WorkflowClient) -> None:
        """When knowledge_base_name is already a UUID, it passes through."""
        raw_uuid = '6c26048d-2f9c-4177-a126-f2ed8cd02a0e'
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'retrieval': NodeDefinition.model_construct(
                    type='retrieve',
                    execution_mode='FLOW',
                    label='Retrieval',
                    config={'knowledge_base_name': raw_uuid},
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client)
        assert f'kb:{raw_uuid}' in resolved
        assert resolved[f'kb:{raw_uuid}'] == UUID(raw_uuid)

    def test_mixed_uuid_and_name(self, live_client: WorkflowClient) -> None:
        """A workflow with one UUID ref and one name ref resolves both."""
        agent_uuid = 'e0b3bdc6-9fcb-45ee-8833-26aa5cd1d2e0'
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'rag': NodeDefinition.model_construct(
                    type='rag_agent',
                    execution_mode='MESSAGES',
                    label='RAG Agent',
                    config={
                        'agent_name': agent_uuid,  # UUID passthrough
                        'knowledge_base_names': ['standards'],  # name lookup
                    },
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client)
        assert resolved[f'agent:{agent_uuid}'] == UUID(agent_uuid)
        assert 'kb:standards' in resolved
        assert isinstance(resolved['kb:standards'], UUID)


# ---------------------------------------------------------------------------
# 5. Lockfile cache — cached dependencies skip API calls
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLockfileCacheLive:
    """Verify that lockfile-cached dependencies are used when available."""

    def test_cached_agent_used_from_lockfile(
        self, live_client: WorkflowClient, org_id: str
    ) -> None:
        """When the lockfile already has agent:Name, the cached UUID is used."""
        cached_uuid = UUID('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
        lock = WorkflowLock(
            workflow_id=UUID('00000000-0000-0000-0000-000000000001'),
            organization_id=UUID(org_id),
            version=1,
            instance='https://dev.sb.allogy.com',
            dependencies={'agent:Data Analyst Agent': cached_uuid},
            pushed_at=datetime.now(UTC),
        )

        workflow = _make_workflow(
            {
                'input': _input_node(),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='Agent',
                    config={'agent_name': 'Data Analyst Agent'},
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client, existing_lock=lock)
        # Should use the cached value, NOT the real API value
        assert resolved['agent:Data Analyst Agent'] == cached_uuid

    def test_cached_kb_used_from_lockfile(self, live_client: WorkflowClient, org_id: str) -> None:
        """When the lockfile already has kb:Name, the cached UUID is used."""
        cached_uuid = UUID('11111111-2222-3333-4444-555555555555')
        lock = WorkflowLock(
            workflow_id=UUID('00000000-0000-0000-0000-000000000001'),
            organization_id=UUID(org_id),
            version=1,
            instance='https://dev.sb.allogy.com',
            dependencies={'kb:standards': cached_uuid},
            pushed_at=datetime.now(UTC),
        )

        workflow = _make_workflow(
            {
                'input': _input_node(),
                'retrieval': NodeDefinition.model_construct(
                    type='retrieve',
                    execution_mode='FLOW',
                    label='Retrieval',
                    config={'knowledge_base_name': 'standards'},
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client, existing_lock=lock)
        assert resolved['kb:standards'] == cached_uuid

    def test_uncached_falls_through_to_api(self, live_client: WorkflowClient, org_id: str) -> None:
        """When the lockfile exists but doesn't have the key, the API is called."""
        lock = WorkflowLock(
            workflow_id=UUID('00000000-0000-0000-0000-000000000001'),
            organization_id=UUID(org_id),
            version=1,
            instance='https://dev.sb.allogy.com',
            dependencies={},  # empty cache
            pushed_at=datetime.now(UTC),
        )

        workflow = _make_workflow(
            {
                'input': _input_node(),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='Agent',
                    config={'agent_name': 'Data Analyst Agent'},
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client, existing_lock=lock)
        # Should resolve via API to the real UUID
        assert resolved['agent:Data Analyst Agent'] == UUID('e0b3bdc6-9fcb-45ee-8833-26aa5cd1d2e0')

    def test_no_lockfile_resolves_via_api(self, live_client: WorkflowClient) -> None:
        """When existing_lock is None, everything resolves via API."""
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'rag': NodeDefinition.model_construct(
                    type='rag_agent',
                    execution_mode='MESSAGES',
                    label='RAG',
                    config={
                        'agent_name': 'Data Analyst Agent',
                        'knowledge_base_names': ['standards'],
                    },
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client, existing_lock=None)
        assert resolved['agent:Data Analyst Agent'] == UUID('e0b3bdc6-9fcb-45ee-8833-26aa5cd1d2e0')
        assert resolved['kb:standards'] == UUID('6c26048d-2f9c-4177-a126-f2ed8cd02a0e')


# ---------------------------------------------------------------------------
# 6. Lockfile round-trip — resolved deps survive serialization
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLockfileRoundtripLive:
    """Verify that resolved dependencies survive a lockfile write/read cycle."""

    def test_resolved_deps_persist_through_yaml_roundtrip(
        self, live_client: WorkflowClient, org_id: str
    ) -> None:
        """Resolve live deps -> store in WorkflowLock -> to_yaml_dict ->
        from_yaml_dict -> verify UUIDs match."""
        workflow = _make_workflow(
            {
                'input': _input_node(),
                'rag': NodeDefinition.model_construct(
                    type='rag_agent',
                    execution_mode='MESSAGES',
                    label='RAG',
                    config={
                        'agent_name': 'Data Analyst Agent',
                        'knowledge_base_names': ['standards', 'industry'],
                    },
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client)

        # Build a lockfile with the resolved deps
        lock = WorkflowLock(
            workflow_id=UUID('00000000-0000-0000-0000-000000000001'),
            organization_id=UUID(org_id),
            version=1,
            instance='https://dev.sb.allogy.com',
            nodes={'input': UUID('00000000-0000-0000-0000-000000000010')},
            edges={},
            dependencies=resolved,
            pushed_at=datetime.now(UTC),
        )

        # Round-trip through YAML serialization
        yaml_dict = lock.to_yaml_dict()
        restored = WorkflowLock.from_yaml_dict(yaml_dict)

        # Dependencies should survive the round-trip
        assert restored.dependencies == resolved
        assert restored.dependencies['agent:Data Analyst Agent'] == UUID(
            'e0b3bdc6-9fcb-45ee-8833-26aa5cd1d2e0'
        )
        assert restored.dependencies['kb:standards'] == UUID('6c26048d-2f9c-4177-a126-f2ed8cd02a0e')
        assert restored.dependencies['kb:industry'] == UUID('e70c07d8-8dbe-4833-9e42-a10dd0d21893')

    def test_resolved_deps_survive_disk_roundtrip(
        self, live_client: WorkflowClient, org_id: str, tmp_path: Path
    ) -> None:
        """Resolve live deps -> write lockfile to disk -> read back -> verify."""
        from cli.lockfile import read_lockfile, write_lockfile

        workflow = _make_workflow(
            {
                'input': _input_node(),
                'agent': NodeDefinition.model_construct(
                    type='agent',
                    execution_mode='MESSAGES',
                    label='Agent',
                    config={'agent_name': 'Code Assistant Agent'},
                ),
            }
        )

        resolved = resolve_dependencies(workflow, live_client)

        lock = WorkflowLock(
            workflow_id=UUID('00000000-0000-0000-0000-000000000001'),
            organization_id=UUID(org_id),
            version=1,
            instance='https://dev.sb.allogy.com',
            dependencies=resolved,
            pushed_at=datetime.now(UTC),
        )

        lock_path = tmp_path / 'test.workflow.lock'
        write_lockfile(lock_path, lock)
        restored = read_lockfile(lock_path)

        assert restored is not None
        assert restored.dependencies == resolved
        assert 'agent:Code Assistant Agent' in restored.dependencies
        assert restored.dependencies['agent:Code Assistant Agent'] == UUID(
            '2792240d-07cf-4674-82be-9b71189bb286'
        )
