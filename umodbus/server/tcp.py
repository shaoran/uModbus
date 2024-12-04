import asyncio
import struct
from types import MethodType

from umodbus.route import Map
from umodbus.server import (AbstractRequestHandler, route,
                            Metadata, AsyncAbstractRequestHandler)
from umodbus.utils import unpack_mbap, pack_mbap
from umodbus.exceptions import ServerDeviceFailureError


def get_server(server_class, server_address, request_handler_class):
    """ Return instance of :param:`server_class` with :param:`request_handler`
    bound to it.
    This method also binds a :func:`route` method to the server instance.
        >>> server = get_server(TcpServer, ('localhost', 502), RequestHandler)
        >>> server.serve_forever()
    :param server_class: (sub)Class of :class:`socketserver.BaseServer`.
    :param request_handler_class: (sub)Class of
        :class:`umodbus.server.RequestHandler`.
    :return: Instance of :param:`server_class`.
    """
    s = server_class(server_address, request_handler_class)

    s.route_map = Map()
    s.route = MethodType(route, s)

    return s

async def get_server_async(server_address):
    """ Return instance of :py:class:`asyncio.Server` with routing bound to it

    :param server_address: a tuple with bind address and port
    :return: Instance of :py:class:`asyncio.Server`
    """

    hdl = AsyncRequestHandler()

    server = await asyncio.start_server(hdl.handle_async, host=server_address[0], port=server_address[1], reuse_address=True)

    server.route_map = Map()
    server.route = MethodType(route, server)

    hdl.server = server

    return server


class RequestHandler(AbstractRequestHandler):
    """ A subclass of :class:`socketserver.BaseRequestHandler` dispatching
    incoming Modbus TCP/IP request using the server's :attr:`route_map`.

    """
    def get_meta_data(self, request_adu) -> Metadata:
        """" Extract MBAP header from request adu and return it. The dict has
        4 keys: transaction_id, protocol_id, length and unit_id.

        :param request_adu: A bytearray containing request ADU.
        :return: Dict with meta data of request.
        """
        try:
            transaction_id, protocol_id, length, unit_id = \
                unpack_mbap(request_adu[:7])
        except struct.error:
            raise ServerDeviceFailureError()

        return {
            'transaction_id': transaction_id,
            'protocol_id': protocol_id,
            'length': length,
            'unit_id': unit_id,
        }

    def get_request_pdu(self, request_adu):
        """ Extract PDU from request ADU and return it.

        :param request_adu: A bytearray containing request ADU.
        :return: An bytearray container request PDU.
        """
        return request_adu[7:]

    def create_response_adu(self, meta_data, response_pdu):
        """ Build response ADU from meta data and response PDU and return it.

        :param meta_data: A dict with meta data.
        :param request_pdu: A bytearray containing request PDU.
        :return: A bytearray containing request ADU.
        """
        response_mbap = pack_mbap(
            transaction_id=meta_data['transaction_id'],
            protocol_id=meta_data['protocol_id'],
            length=len(response_pdu) + 1,
            unit_id=meta_data['unit_id']
        )

        return response_mbap + response_pdu


class DummyRequestHandler(RequestHandler):
    """
    A dummy :py:class:`RequestHandler` with the protocol implementations
    that can be used in the async request handler
    """
    def __init__(self):
        pass

class AsyncRequestHandler(AsyncAbstractRequestHandler):
    """
    A async version of :py:class:`RequestHandler`.

    In order to reuse the modbus protocol parsing and payload
    responses without having to split the code in multiple mixings,
    multiple functions that are needed in the socketserver protocol
    are overridden to do nothing at all.

    """
    def __init__(self):
        # helper object that allows to reuse parsing functions
        self._sync_request_handler = DummyRequestHandler()

    def get_meta_data(self, request_adu):
        return self._sync_request_handler.get_meta_data(request_adu)

    def get_request_pdu(self, request_adu):
        return self._sync_request_handler.get_request_pdu(request_adu)

    def create_response_adu(self, meta_data, response_pdu):
        return self._sync_request_handler.create_response_adu(meta_data, response_pdu)
