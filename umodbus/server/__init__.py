try:
    from socketserver import BaseRequestHandler
except ImportError:
    from SocketServer import BaseRequestHandler

import asyncio
from binascii import hexlify

from abc import ABC, abstractmethod
from typing import TypedDict
from collections.abc import ByteString

from umodbus import log
from umodbus.functions import create_function_from_request_pdu
from umodbus.exceptions import ModbusError, ServerDeviceFailureError
from umodbus.utils import (get_function_code_from_request_pdu,
                           pack_exception_pdu, recv_exactly,
                           resolve_awaitable, recv_exactly_async)


ASYNC_HANDLER_TASK_MARK =  "__UMODBUS_HANDLER_TASK__"


def route(self, slave_ids=None, function_codes=None, addresses=None):
    """ A decorator that is used to register an endpoint for a given
    rule::

        @server.route(slave_ids=[1], function_codes=[1, 2], addresses=list(range(100, 200)))  # NOQA
        def read_single_bit_values(slave_id, address):
            return random.choise([0, 1])

    Any argument can be omitted to match any value.

    :param slave_ids: A list (or iterable) of slave ids.
    :param function_codes: A list (or iterable) of function codes.
    :param addresses: A list (or iterable) of addresses.
    """
    def inner(f):
        self.route_map.add_rule(f, slave_ids, function_codes, addresses)
        return f

    return inner


class Metadata(TypedDict):
    transaction_id: int
    protocol_id: int
    length: int
    unit_id: int


class BaseAbstractRequestHandler(ABC):
    """
    An abstract class that defines the interface of how to
    parse the modbus request and construct a response
    """
    @abstractmethod
    def get_meta_data(self, request_adu: ByteString) -> Metadata:
        """" Extract MBAP header from request adu and return it. The dict has
        4 keys: transaction_id, protocol_id, length and unit_id.

        :param request_adu: A bytearray containing request ADU.
        :return: Dict with meta data of request.
        """
        ...

    @abstractmethod
    def get_request_pdu(self, request_adu: ByteString) -> ByteString:
        """ Extract PDU from request ADU and return it.

        :param request_adu: A bytearray containing request ADU.
        :return: An bytearray container request PDU.
        """
        ...

    @abstractmethod
    def create_response_adu(self, meta_data: Metadata, response_pdu: ByteString) -> ByteString:
        """ Build response ADU from meta data and response PDU and return it.

        :param meta_data: A dict with meta data.
        :param request_pdu: A bytearray containing request PDU.
        :return: A bytearray containing request ADU.
        """
        ...


class AbstractRequestHandler(BaseAbstractRequestHandler, BaseRequestHandler):
    """ A subclass of :class:`socketserver.BaseRequestHandler` dispatching
    incoming Modbus requests using the server's :attr:`route_map`.

    """
    def handle(self):
        try:
            while True:
                try:
                    mbap_header = recv_exactly(self.request.recv, 7)
                    remaining = self.get_meta_data(mbap_header)['length'] - 1
                    request_pdu = recv_exactly(self.request.recv, remaining)
                except ValueError:
                    return

                response_adu = self.process(mbap_header + request_pdu)
                self.respond(response_adu)
        except:
            log.exception('Error while handling request')
            raise

    def process(self, request_adu):
        """ Process request ADU and return response.

        :param request_adu: A bytearray containing the ADU request.
        :return: A bytearray containing the response of the ADU request.
        """
        meta_data = self.get_meta_data(request_adu)
        request_pdu = self.get_request_pdu(request_adu)

        response_pdu = self.execute_route(meta_data, request_pdu)
        response_adu = self.create_response_adu(meta_data, response_pdu)

        return response_adu

    def execute_route(self, meta_data, request_pdu):
        """ Execute configured route based on requests meta data and request
        PDU.

        :param meta_data: A dict with meta data. It must at least contain
            key 'unit_id'.
        :param request_pdu: A bytearray containing request PDU.
        :return: A bytearry containing reponse PDU.
        """
        try:
            function = create_function_from_request_pdu(request_pdu)
            results =\
                function.execute(meta_data['unit_id'], self.server.route_map) # type: ignore

            try:
                # ReadFunction's use results of callbacks to build response
                # PDU...
                return function.create_response_pdu(results)
            except TypeError:
                # ...other functions don't.
                return function.create_response_pdu()
        except ModbusError as e:
            function_code = get_function_code_from_request_pdu(request_pdu)
            return pack_exception_pdu(function_code, e.error_code)
        except Exception:
            log.exception('Could not handle request')
            function_code = get_function_code_from_request_pdu(request_pdu)

            return pack_exception_pdu(function_code,
                                      ServerDeviceFailureError.error_code)

    def respond(self, response_adu):
        """ Send response ADU back to client.

        :param response_adu: A bytearray containing the response of an ADU.
        """
        log.debug('--> {0} - {1}.'.format(self.client_address[0],
                  hexlify(response_adu)))
        self.request.sendall(response_adu)



class AsyncAbstractRequestHandler(BaseAbstractRequestHandler):
    """ A class similar to :py:class:`AbstractRequestHandler` that
    dispatches incoming Modbus requests using the server's :attr:`route_map`.

    This is an asyncio implementation of the above class.
    """

    async def handle_async(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        # marking the current task as a handler task
        # This allows to cancel this task when the client
        # leaves the connection open without sending anything,
        # thus making this task "hang" in await reader.read()
        task = asyncio.current_task()

        if task is None:
            raise Exception("The handler is being invoked oustide the asyncio.start_server() context")

        task.set_name(ASYNC_HANDLER_TASK_MARK)

        try:
            while True:
                try:
                    mbap_header = await recv_exactly_async(reader, 7)
                    remaining = self.get_meta_data(mbap_header)['length'] - 1
                    request_pdu = await recv_exactly_async(reader, remaining)
                except ValueError:
                    return

                response_adu = await self.process(mbap_header + request_pdu)

                writer.write(response_adu)
                await writer.drain()
        except asyncio.CancelledError:
            raise
        except:
            log.exception('Error while handling request')
        finally:
            writer.close()
            await writer.wait_closed()

    async def process(self, request_adu):
        """ Process request ADU and return response.

        :param request_adu: A bytearray containing the ADU request.
        :return: A bytearray containing the response of the ADU request.
        """
        meta_data = self.get_meta_data(request_adu)
        request_pdu = self.get_request_pdu(request_adu)

        response_pdu = await self.execute_route(meta_data, request_pdu)
        response_adu = self.create_response_adu(meta_data, response_pdu)

        return response_adu

    async def execute_route(self, meta_data, request_pdu):
        """ Execute configured route based on requests meta data and request
        PDU.

        :param meta_data: A dict with meta data. It must at least contain
            key 'unit_id'.
        :param request_pdu: A bytearray containing request PDU.
        :return: A bytearry containing reponse PDU.
        """
        try:
            function = create_function_from_request_pdu(request_pdu)
            results =\
                function.execute(meta_data['unit_id'], self.server.route_map) # type: ignore

            results = await resolve_awaitable(results)

            try:
                # ReadFunction's use results of callbacks to build response
                # PDU...
                return function.create_response_pdu(results)
            except TypeError:
                # ...other functions don't.
                return function.create_response_pdu()
        except asyncio.CancelledError:
            raise
        except ModbusError as e:
            function_code = get_function_code_from_request_pdu(request_pdu)
            return pack_exception_pdu(function_code, e.error_code)
        except Exception:
            log.exception('Could not handle request')
            function_code = get_function_code_from_request_pdu(request_pdu)

            return pack_exception_pdu(function_code,
                                      ServerDeviceFailureError.error_code)
