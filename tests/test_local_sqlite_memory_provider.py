import json

from hermes_local_sqlite_memory import LocalSQLiteMemoryProvider


def _provider(tmp_path):
    p = LocalSQLiteMemoryProvider({"db_path": str(tmp_path / "memory.sqlite3"), "context_limit": "5"})
    p.initialize("test-session", hermes_home=str(tmp_path), platform="cli")
    return p


def test_config_schema_allows_user_defined_namespaces(tmp_path):
    p = _provider(tmp_path)
    config = p.get_config_schema()
    namespace = next(item for item in config if item["key"] == "namespace")
    assert namespace["default"] == "default"
    assert "choices" not in namespace


def test_store_and_search_memory(tmp_path):
    p = _provider(tmp_path)
    stored = json.loads(p.handle_tool_call("local_memory_store", {
        "content": "The user prefers private local memory over cloud memory.",
        "memory_type": "preference",
        "namespace": "default",
    }))
    assert stored["success"] is True
    search = json.loads(p.handle_tool_call("local_memory_search", {
        "query": "private memory",
        "namespace": "default",
    }))
    assert search["success"] is True
    assert search["memories"]
    assert search["memories"][0]["id"] == stored["memory"]["id"]


def test_namespace_isolation(tmp_path):
    p = _provider(tmp_path)
    p.handle_tool_call("local_memory_store", {
        "content": "Workspace Beta handles support requests only.",
        "memory_type": "identity",
        "namespace": "workspace_beta",
    })
    default = json.loads(p.handle_tool_call("local_memory_search", {"query": "support", "namespace": "default"}))
    workspace_beta = json.loads(p.handle_tool_call("local_memory_search", {"query": "support", "namespace": "workspace_beta"}))
    assert default["memories"] == []
    assert len(workspace_beta["memories"]) == 1


def test_sync_turn_and_include_turns(tmp_path):
    p = _provider(tmp_path)
    p.sync_turn("remember this: test host is local-host", "Noted", session_id="s1")
    result = json.loads(p.handle_tool_call("local_memory_search", {
        "query": "local-host",
        "include_turns": True,
    }))
    assert result["turns"]
    assert result["turns"][0]["session_id"] == "s1"


def test_review_proposal_approve_creates_memory(tmp_path):
    p = _provider(tmp_path)
    proposed = json.loads(p.handle_tool_call("local_memory_review", {
        "action": "propose",
        "content": "The user prefers concise technical status updates.",
        "memory_type": "preference",
    }))
    pid = proposed["proposal"]["id"]
    approved = json.loads(p.handle_tool_call("local_memory_review", {
        "action": "approve",
        "proposal_id": pid,
    }))
    assert approved["success"] is True
    assert approved["proposal"]["memory"]["content"].startswith("The user prefers")
    search = json.loads(p.handle_tool_call("local_memory_search", {"query": "concise status"}))
    assert search["memories"]


def test_session_end_creates_review_proposals(tmp_path):
    p = _provider(tmp_path)
    p.on_session_end([
        {"role": "user", "content": "I prefer private local memory for Local."},
        {"role": "assistant", "content": "we decided to use SQLite FTS5 first."},
    ])
    result = json.loads(p.handle_tool_call("local_memory_review", {"action": "list"}))
    contents = [x["content"] for x in result["proposals"]]
    assert any("private local memory" in c for c in contents)
    assert any("SQLite FTS5" in c for c in contents)


def test_prefetch_returns_context(tmp_path):
    p = _provider(tmp_path)
    p.handle_tool_call("local_memory_store", {
        "content": "Assistant handoff lives in cloud files.",
        "memory_type": "handoff",
    })
    block = p.prefetch("Where is Assistant handoff?")
    assert "Relevant Local local memories" in block
    assert "Assistant" in block


def test_graph_primitives_store_context_and_representation(tmp_path):
    p = _provider(tmp_path)
    peer = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "upsert_peer",
        "peer_handle": "user",
        "role": "user",
    }))["peer"]
    session = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "upsert_session",
        "session_id": "s-graph",
        "title": "Graph local memory design",
    }))["session"]
    msg = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "add_message",
        "session_id": session["id"],
        "peer_id": peer["id"],
        "role": "user",
        "content": "Nextcloud Talk needs peer context and workspace conclusions.",
    }))["message"]
    con = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "add_conclusion",
        "session_id": session["id"],
        "peer_id": peer["id"],
        "scope": "peer",
        "content": "The user wants chat integrations to use graph-style context.",
        "confidence": 0.9,
    }))["conclusion"]
    ctx = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "context",
        "query": "Nextcloud Talk Graph context",
    }))["context"]
    assert msg["id"].startswith("msg_")
    assert con["id"].startswith("con_")
    assert ctx["conclusions"]
    assert ctx["messages"]
    representation = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "build_representation",
        "peer_id": peer["id"],
    }))["representation"]
    assert "graph-style context" in representation["content"]
    status = json.loads(p.handle_tool_call("local_memory_status", {}))["status"]
    assert status["graph"]["workspaces"]
    assert status["graph"]["peers"]


def test_dream_cycle_consolidates_messages_into_conclusions_and_representation(tmp_path):
    p = _provider(tmp_path)
    peer = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "upsert_peer",
        "peer_handle": "user",
        "role": "user",
    }))["peer"]
    for i in range(3):
        p.handle_tool_call("local_memory_graph", {
            "action": "add_message",
            "session_id": "dream-session",
            "peer_id": peer["id"],
            "role": "user",
            "content": f"The user wants memory v2 to include private local dreaming cycle {i}.",
        })
    result = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "dream",
        "namespace": "default",
        "peer_id": peer["id"],
        "limit": 10,
    }))
    assert result["success"] is True
    dream = result["dream"]
    assert dream["created_conclusions"] >= 1
    assert dream["representation"]["source_count"] >= 1
    assert "private local dreaming" in dream["representation"]["content"].lower()
    ctx = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "context",
        "query": "private local dreaming",
    }))["context"]
    assert ctx["conclusions"]


def test_dream_cycle_is_namespace_isolated(tmp_path):
    p = _provider(tmp_path)
    workspace_peer = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "upsert_peer",
        "namespace": "workspace_beta",
        "peer_handle": "user",
        "role": "user",
    }))["peer"]
    for i in range(2):
        p.handle_tool_call("local_memory_graph", {
            "action": "add_message",
            "namespace": "workspace_beta",
            "session_id": "workspace-dream",
            "peer_id": workspace_peer["id"],
            "role": "user",
            "content": f"Workspace Beta should dream only about support ticket context {i}.",
        })
    json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "dream",
        "namespace": "workspace_beta",
        "peer_id": workspace_peer["id"],
    }))
    default_ctx = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "context",
        "namespace": "default",
        "query": "support ticket dream",
    }))["context"]
    workspace_ctx = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "context",
        "namespace": "workspace_beta",
        "query": "support ticket dream",
    }))["context"]
    assert default_ctx["conclusions"] == []
    assert workspace_ctx["conclusions"]


def test_dream_cycle_ignores_noise_and_singletons(tmp_path):
    p = _provider(tmp_path)
    peer = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "upsert_peer",
        "peer_handle": "user",
        "role": "user",
    }))["peer"]
    p.handle_tool_call("local_memory_graph", {
        "action": "add_message",
        "session_id": "noise-session",
        "peer_id": peer["id"],
        "role": "assistant",
        "content": "Reply exactly: DEFAULT_PROFILE_SMOKE_OK",
    })
    p.handle_tool_call("local_memory_graph", {
        "action": "add_message",
        "session_id": "singleton-session",
        "peer_id": peer["id"],
        "role": "user",
        "content": "What is the date 14 weeks from today?",
    })
    result = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "dream",
        "namespace": "default",
        "peer_id": peer["id"],
    }))["dream"]
    assert result["inspected_messages"] == 1
    assert result["created_conclusions"] == 0
    assert result["representation"]["source_count"] == 0


def test_dream_cycle_does_not_recreate_archived_duplicate(tmp_path):
    p = _provider(tmp_path)
    peer = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "upsert_peer",
        "peer_handle": "user",
        "role": "user",
    }))["peer"]
    for i in range(2):
        p.handle_tool_call("local_memory_graph", {
            "action": "add_message",
            "session_id": "dup-dream",
            "peer_id": peer["id"],
            "role": "user",
            "content": f"The user wants recurring local memory dreaming evidence {i}.",
        })
    first = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "dream",
        "namespace": "default",
        "peer_id": peer["id"],
    }))["dream"]
    assert first["created_conclusions"] == 1
    p.handle_tool_call("local_memory_forget", {
        "memory_id": first["conclusions"][0]["id"],
        "mode": "archive",
    })
    # Archive the conclusion directly because local_memory_forget is for durable memories.
    p._store.update_conclusion_status(first["conclusions"][0]["id"], "archived")
    second = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "dream",
        "namespace": "default",
        "peer_id": peer["id"],
    }))["dream"]
    assert second["created_conclusions"] == 0



def test_auto_dream_runs_self_maintenance_on_session_end(tmp_path):
    p = LocalSQLiteMemoryProvider({
        "db_path": str(tmp_path / "memory.sqlite3"),
        "namespace": "autodream",
        "auto_dream": "true",
        "auto_dream_interval_seconds": "0",
        "assistant_handle": "test_assistant",
    })
    p.initialize("auto-session", hermes_home=str(tmp_path), platform="cli")
    p.sync_turn("Remember the router prefers wired failover", "Noted", session_id="auto-session")
    p.sync_turn("The router wired failover should stay preferred", "Confirmed", session_id="auto-session")
    p.on_session_end([])
    status = json.loads(p.handle_tool_call("local_memory_status", {}))["status"]
    conclusions = [x for x in status["graph"]["conclusions"] if x["namespace"] == "autodream" and x["status"] == "active"]
    peers = [x for x in status["graph"]["peers"] if x["namespace"] == "autodream"]
    assert conclusions
    assert any(peer["c"] >= 2 for peer in peers)
    ctx = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "context",
        "namespace": "autodream",
        "query": "wired failover",
    }))["context"]
    assert ctx["conclusions"]


def test_self_maintain_cleans_noise_and_builds_peer_representation(tmp_path):
    p = _provider(tmp_path)
    peer = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "upsert_peer",
        "namespace": "default",
        "peer_handle": "assistant_bot",
        "role": "assistant",
    }))["peer"]
    p.handle_tool_call("local_memory_graph", {
        "action": "add_conclusion",
        "namespace": "default",
        "content": "Reply exactly: DEFAULT_PROFILE_SMOKE_OK",
        "scope": "workspace",
    })
    for i in range(2):
        p.handle_tool_call("local_memory_graph", {
            "action": "add_message",
            "namespace": "default",
            "session_id": "maintain-session",
            "peer_id": peer["id"],
            "role": "assistant",
            "content": f"The assistant should remember durable maintenance insight {i}.",
        })
    result = json.loads(p.handle_tool_call("local_memory_graph", {
        "action": "self_maintain",
        "namespace": "default",
        "peer_handle": "assistant_bot",
        "limit": 48,
    }))["maintenance"]
    assert result["cleanup"]["archived"] >= 1
    assert result["workspace_dream"]["created_conclusions"] + result["peer_dream"]["created_conclusions"] >= 1
    assert result["peer_dream"]["representation"]["kind"] == "dream_context"
