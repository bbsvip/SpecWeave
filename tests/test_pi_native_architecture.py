"""Architectural invariant: exactly one agent/model runtime (Pi SDK)."""
from pathlib import Path
import yaml

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'source'


def test_no_retired_python_agent_or_rpc_process():
    assert not (SRC/'pi_runner.py').exists()
    assert not (SRC/'pi_tools.ts').exists()
    ai=(SRC/'ai.py').read_text(encoding='utf-8')
    planning=(SRC/'planning_engine.py').read_text(encoding='utf-8')
    assert 'compatible_stream' not in ai
    assert '/chat/completions' not in ai
    assert 'pi_client import complete' in planning
    assert 'httpx.AsyncClient' not in planning
    assert 'async def complete(' not in (SRC/'openai_oauth_runtime.py').read_text()


def test_pi_native_host_controls_model_and_tool_lifecycle():
    code=(SRC/'pi_host.mjs').read_text(encoding='utf-8')
    for required in ('createAgentSession(', 'ModelRuntime.create(', 'SessionManager.continueRecent(',
                     "noTools:withTools?'builtin':'all'", "customTools:withTools?createScopedTools(active):[]",
                     'modelsPath', "'thinking_delta'", "'tool_execution_end'", "'message_end'"):
        assert required in code, required
    assert 'tools:[]' not in code  # Passing [] suppresses custom tools in Pi.


def test_pi_is_mandatory_in_every_compose_variant():
    for name in ('compose.yaml','compose.external-db.yaml','compose.images.yaml','compose.external-db.images.yaml'):
        compose=yaml.safe_load((ROOT/name).read_text(encoding='utf-8'))
        services=compose['services']
        assert 'pi-runner' in services
        assert 'profiles' not in services['pi-runner']
        assert 'pi-runner' in services['web']['depends_on']
