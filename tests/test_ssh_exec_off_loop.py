"""ssh_exec and probe_result must not run on the dashboard's event loop.

The gateway calls back into the dashboard (/api/request, /api/ticket) while
ssh_exec is waiting on it. When FastMCP ran the sync tool on the loop, that
callback could not be served, and the gateway reported "JIT Dashboard is
unreachable" after its curl timeout (found in the 1.2 test round, n8n).
Each test blocks the tool on an event that only another coroutine on the
loop can set: on the loop it would time out, off the loop it is set."""
import asyncio
import json
import threading

from core.agent_context import get_current_agent, reset_current_agent, set_current_agent
from core.mcp_server import mcp as eshu_mcp
from db.agent_tokens import create_agent_token, get_agent_by_token


def _run_with_loop_probe(tool, args, patch_target, attr, monkeypatch):
    released = threading.Event()
    seen = {}

    def fake(*a, **kw):
        agent = get_current_agent() or {}
        seen['agent'] = agent.get('name')
        seen['released'] = released.wait(timeout=3)
        return {'status': 'ran', 'request_id': 1}

    monkeypatch.setattr(patch_target, attr, fake)

    async def main():
        async def release_soon():
            await asyncio.sleep(0.05)
            released.set()
        _, out = await asyncio.gather(release_soon(), eshu_mcp.call_tool(tool, args))
        return out

    raw, _aid = create_agent_token('loop-agent')
    token = set_current_agent(get_agent_by_token(raw))
    try:
        asyncio.run(main())
    finally:
        reset_current_agent(token)
    return seen


def test_ssh_exec_runs_off_the_event_loop(monkeypatch):
    import core.ssh_exec as mod
    seen = _run_with_loop_probe('ssh_exec', {'host': '10.0.0.5', 'command': 'uptime'},
                                mod, 'ssh_exec', monkeypatch)
    assert seen['released'] is True, 'ssh_exec blocked the event loop'
    assert seen['agent'] == 'loop-agent', 'the bound agent did not reach the worker thread'


def test_probe_result_runs_off_the_event_loop(monkeypatch):
    import core.probes as mod
    seen = _run_with_loop_probe('probe_result', {'plan_id': 1},
                                mod, 'probe_result', monkeypatch)
    assert seen['released'] is True, 'probe_result blocked the event loop'
    assert seen['agent'] == 'loop-agent'
