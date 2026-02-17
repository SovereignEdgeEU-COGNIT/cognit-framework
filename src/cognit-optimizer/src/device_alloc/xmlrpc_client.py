"""Proxy for interaction with OpenNebula XML-RPC API."""

__all__ = [
    'OneXMLRPCAuthenticationError',
    'OneXMLRPCAuthorizationError',
    'OneXMLRPCExistenceError',
    'OneXMLRPCActionError',
    'OneXMLRPCAPIError',
    'OneXMLRPCInternalError',
    'OneXMLRPCAllocationError',
    'OneXMLRPCLockError',
    'OnedServerProxy'
]

from os.path import expanduser, join as path_join, exists as path_exists
from types import TracebackType
from typing import Any, Literal, Union, TYPE_CHECKING
from xml.etree.ElementTree import Element, ParseError, fromstring
from xmlrpc.client import ServerProxy

if TYPE_CHECKING:
    from typing_extensions import Self
else:
    try:
        from typing import Self  # Python 3.11+
    except ImportError:
        from typing_extensions import Self  # Python 3.10 fallback

# ERRORS ===============================================================

# NOTE: Error codes, names, and messages are defined here:
# https://docs.opennebula.io/6.8/integration_and_development/system_interfaces/api.html


class _OneXMLRPCError(Exception):
    _MESSAGE = ''

    def __init__(self, message) -> None:
        super().__init__(f'{self._MESSAGE}\n{message}')


class OneXMLRPCAuthenticationError(_OneXMLRPCError):
    _MESSAGE = 'User could not be authenticated.'


class OneXMLRPCAuthorizationError(_OneXMLRPCError):
    _MESSAGE = 'User is not authorized to perform the requested action.'


class OneXMLRPCExistenceError(_OneXMLRPCError):
    _MESSAGE = 'The requested resource does not exist.'


class OneXMLRPCActionError(_OneXMLRPCError):
    _MESSAGE = 'Wrong state to perform action.'


class OneXMLRPCAPIError(_OneXMLRPCError):
    _MESSAGE = 'Wrong parameters.'


class OneXMLRPCInternalError(_OneXMLRPCError):
    _MESSAGE = 'Internal error.'


class OneXMLRPCAllocationError(_OneXMLRPCError):
    _MESSAGE = 'The resource cannot be allocated.'


class OneXMLRPCLockError(_OneXMLRPCError):
    _MESSAGE = 'The resource is locked.'


_ERRORS = {
    0x0100: OneXMLRPCAuthenticationError,
    0x0200: OneXMLRPCAuthorizationError,
    0x0400: OneXMLRPCExistenceError,
    0x0800: OneXMLRPCActionError,
    0x1000: OneXMLRPCAPIError,
    0x2000: OneXMLRPCInternalError,
    0x4000: OneXMLRPCAllocationError,
    0x8000: OneXMLRPCLockError,
}


# XML PARSING ==========================================================

# TODO: XML parsing functions need additional considerations.
# Alternatively, an external library like `xmltodict` can be used.


def _extract(element: Element) -> Union[str, dict[str, Any], None]:
    if not element:
        return element.text

    data: dict[str, Any] = {}
    for node in element:
        result = _extract(node)
        if (node_data := data.get(node.tag)) is None:
            data[node.tag] = result
        else:
            if isinstance(node_data, list):
                node_data.append(result)
            else:
                data[node.tag] = [node_data, result]
    # data['custom_attrs'] = element.attrib
    return data


def _parse(response: str) -> dict[str, Any]:
    try:
        element = fromstring(response)
    except ParseError as err:
        raise ValueError("'response' cannot be parsed") from err

    return {element.tag: _extract(element)}


# REQUEST HANDLING =====================================================


_AUTH_FILE_PATHS = [
    path_join(expanduser('~'), '.one', 'one_auth'),
    '/var/lib/one/.one/one_auth'
]


def _find_session_auth() -> str:
    for auth_file_path in _AUTH_FILE_PATHS:
        if not path_exists(auth_file_path):
            continue
        with open(auth_file_path, mode='r', encoding='utf-8') as auth_file:
            return auth_file.read().strip()
    raise FileNotFoundError("'auth_file_path' cannot be found")


class OnedServerProxy:
    """
    Proxy for interaction with OpenNebula XML-RPC API.

    Proxy for making XML-RPC requests to the OpenNebula XML-RPC API,
    parse XML responses and handle errors.

    Parameters
    ----------
    uri : str
        The XML-RPC endpoint for the OpenNebula oned server.
    session : str
        The session string used for authentication with oned server.
    """

    __slots__ = ('_server_proxy', '_session')

    def __init__(
        self, uri: str = 'http://localhost:2633/RPC2', session: str = ''
    ) -> None:
        self._server_proxy = ServerProxy(uri=uri)
        self._session = session or _find_session_auth()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: Union[type[BaseException], None],
        exception_value: Union[BaseException, None],
        traceback: Union[TracebackType, None],
    ) -> Literal[False]:
        self.close()
        return False

    def __call__(self, method_name: str, *args) -> Any:
        resp = self.request(method_name, *args)
        if isinstance(resp, str) and resp[0] == '<' and resp[-1] == '>':
            return _parse(resp)
        return resp

    def request(self, method_name: str, *args) -> Any:
        """
        Send a request to the OpenNebula XML-RPC API.

        Construct and send a request to the OpenNebula XML-RPC API.
        It process the server's response, and raise an appropriate
        exception if an error occurs.

        Parameters
        ----------
        method_name : str
            The name of the method to call on the XML-RPC API.
        *args
            Additional arguments to pass to the server method.

        Raises
        ------
        OneXMLRPCAuthenticationError
            If the user could not be authenticated.
        OneXMLRPCAuthorizationError
            If the user is not authorized to perform the requested
            action.
        OneXMLRPCExistenceError
            If the requested resource does not exist.
        OneXMLRPCActionError
            If the action cannot be performed due to the wrong state.
        OneXMLRPCAPIError
            If the parameters provided are incorrect.
        OneXMLRPCInternalError
            If an internal error occurs on the server.
        OneXMLRPCAllocationError
            If the resource cannot be allocated.
        OneXMLRPCLockError
            If the resource is locked.

        """
        method = getattr(self._server_proxy, method_name)
        success, body, error_code, *error_info = method(self._session, *args)
        if success:
            return body
        error = f'Error: {body}'
        code = f'Error code: {hex(error_code)}'
        info = 'Error info: ' + ' '.join(map(str, error_info))
        raise _ERRORS[error_code](f'{error}\n{code}\n{info}')

    def close(self) -> None:
        self._server_proxy('close')()

