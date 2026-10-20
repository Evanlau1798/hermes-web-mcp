"""Thin, synchronous host for Hermes' native Computer Use implementation.

Load web-mcp runtime first. Register ``adapter.schema`` as hermes_computer_use,
then call ``adapter.computer_use(arguments, session_id=client_session_namespace)``
inside the host's client scope. ``close(session_id)`` releases that backend;
``close()`` waits for calls and permanently closes this adapter. The host owns
approval-session cleanup and MCP text/image conversion. Native JSON strings and
multimodal dictionaries pass through unchanged.

Private upstream contract: execute tool.py in a separate module namespace, keeping
its handlers, action table, guards, caches and atexit cleanup together. Bind only
_new_backend, _cua_permission_mode and the two auxiliary-vision routing functions
on that instance. No shared native module, config or plugin registry is patched.
Upstream changes to these seams require rerunning the adapter contract tests.
"""

import atexit
from copy import deepcopy
import importlib.util
import json
import os
import threading
from types import SimpleNamespace

from jsonschema import Draft202012Validator


def _no_auxiliary_vision(*args, **kwargs):
    raise RuntimeError('Auxiliary vision is disabled for web-mcp Computer Use')


class ComputerAdapter:
    """Use a nonempty, trusted client/session namespace, never a tool argument.

    ValueError: invalid arguments or session id. RuntimeError: closed adapter,
    unsafe runtime/provider contract, or a native exception outside its error
    envelope. Driver/dispatch/guard failures normally retain native JSON errors.
    Construction never probes, starts or installs a driver. Calls are serialized
    with lifecycle operations; each session retains its own native backend.
    """

    def __init__(self):
        if os.environ.get('HERMES_SAFE_MODE') != '1':
            raise RuntimeError('ComputerAdapter requires HERMES_SAFE_MODE=1')
        from plugins.computer_use import cua
        from tools.computer_use.backend import ComputerUseProvider
        from tools.computer_use.schema import COMPUTER_USE_SCHEMA

        registered = []
        cua.register(SimpleNamespace(register_computer_use_provider=registered.append))
        if (len(registered) != 1 or not isinstance(registered[0], ComputerUseProvider)
                or registered[0].name != 'cua'):
            raise RuntimeError('Built-in cua provider registration failed')
        self._provider = registered[0]
        self._schema = deepcopy(COMPUTER_USE_SCHEMA)
        parameters = deepcopy(self._schema['parameters'])
        parameters['additionalProperties'] = False
        self._validator = Draft202012Validator(parameters)
        self._lock = threading.RLock()
        self._closed = False
        self._pending = None

        source = importlib.util.find_spec('tools.computer_use.tool')
        spec = importlib.util.spec_from_file_location('tools.computer_use._web_mcp_adapter', source.origin)
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        self._native = native
        required = ('handle_computer_use', 'release_computer_use_session',
                    '_shutdown_backend_atexit', '_new_backend', '_cua_permission_mode',
                    '_should_route_through_aux_vision', '_route_capture_through_aux_vision')
        if not all(callable(getattr(native, name, None)) for name in required):
            cleanup = getattr(native, '_shutdown_backend_atexit', None)
            if callable(cleanup):
                atexit.unregister(cleanup)
            raise RuntimeError('Unsupported native Computer Use lifecycle/routing contract')
        native._new_backend = self._new_backend
        native._cua_permission_mode = self._permission_mode
        native._should_route_through_aux_vision = lambda: False
        native._route_capture_through_aux_vision = _no_auxiliary_vision

    @property
    def schema(self):
        """Return a copy of the native schema; the host supplies the public name."""
        return deepcopy(self._schema)

    @staticmethod
    def _permission_mode(session_id):
        from tools.computer_use.cua_backend import _cua_configured_permission_mode
        mode = _cua_configured_permission_mode()
        if mode not in ('standard', 'bounded'):
            raise RuntimeError('Refusing an unsafe cua permission mode')
        return mode

    def _new_backend(self, permission_mode):
        if permission_mode not in ('standard', 'bounded'):
            raise RuntimeError('Refusing an unsafe cua permission mode')
        self._pending = self._provider.create_backend(permission_mode=permission_mode)
        return self._pending

    @staticmethod
    def _check_session(session_id):
        if not isinstance(session_id, str) or not session_id.strip() or '\0' in session_id:
            raise ValueError('session_id must be a nonempty client/session namespace without NUL')

    def computer_use(self, arguments, *, session_id):
        """Validate then dispatch one native action, retaining native approval guards."""
        self._check_session(session_id)
        if not isinstance(arguments, dict):
            raise ValueError('computer_use arguments must be an object')
        error = next(self._validator.iter_errors(arguments), None)
        if error is not None:
            raise ValueError(error.message)
        try:
            json.dumps(arguments, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError('computer_use arguments must be finite JSON values') from exc
        from tools.approval_context import set_current_session_key, reset_current_session_key

        with self._lock:
            if self._closed:
                raise RuntimeError('ComputerAdapter is closed')
            token = set_current_session_key(session_id)
            try:
                return self._native.handle_computer_use(dict(arguments), session_id=session_id)
            finally:
                reset_current_session_key(token)
                pending, self._pending = self._pending, None
                # Native startup can fail before installing the backend in its cache.
                if pending is not None and all(b is not pending for b in self._native._backends.values()):
                    pending.stop()

    def close(self, session_id=None):
        """Release one session, or permanently close only this adapter's backends."""
        if session_id is not None:
            self._check_session(session_id)
        with self._lock:
            if self._closed:
                return
            if session_id is not None:
                self._native.release_computer_use_session(session_id)
            else:
                self._closed = True
                self._native._shutdown_backend_atexit()
                self._native._reset_screenshot_dedup()
                atexit.unregister(self._native._shutdown_backend_atexit)
