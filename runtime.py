"""Use the already installed Hermes dependency environment; never run bootstrap."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOME = ROOT.parent
SOURCE = HOME / "hermes-agent"
os.environ["HERMES_HOME"] = str(HOME)
os.environ["HERMES_QUIET"] = "1"
os.environ["HERMES_REDACT_SECRETS"] = "true"
os.environ["HERMES_SAFE_MODE"] = "1"
for inherited_context in ("HERMES_SINGLE_QUERY_SESSION", "HERMES_CRON_SESSION", "HERMES_EXEC_ASK", "HERMES_GATEWAY_SESSION"):
    os.environ.pop(inherited_context, None)
for secret in ("CONTROL_PLANE_API_KEY", "OPENAI_ADMIN_KEY", "FIRECRAWL_API_KEY"):
    os.environ.pop(secret, None)
sys.path.insert(0, str(SOURCE))
from pm.environments import activate_dependencies

activate_dependencies(SOURCE)

from hermes_cli import config_home

_initialize_home = config_home.initialize_home


def _initialize_tools_home(home, subdirs, ensured):
    from hermes_cli import config
    if os.path.normcase(str(home)) != os.path.normcase(str(HOME)):
        raise RuntimeError('This MCP only uses the existing Hermes home')
    config._ensure_default_soul_md = lambda _home: None
    return _initialize_home(home, subdirs, ensured)


# Config import itself can discover providers and initialize home before it returns.
config_home.initialize_home = _initialize_tools_home
from hermes_cli import config as hermes_config

# This is a tool-only entrypoint: retain directory/ACL checks, omit persona initialization.
for directory in (HOME, *(HOME / name for name in hermes_config._HERMES_HOME_SUBDIRS)):
    if not directory.is_dir():
        raise RuntimeError(f'Required existing Hermes directory is missing: {directory}')
hermes_config._ensure_default_soul_md = lambda _home: None

from tools import approval_context

# The operator opted into direct execution for this private entrypoint only.
# Native hardline and explicit deny rules still run before mode=off.
approval_context._get_approval_mode = lambda: 'off'
approval_context._get_single_query_approval_mode = lambda: 'deny'
