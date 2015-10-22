import pytest
import struct

from modbus.route import Map
from modbus.functions import (function_factory, Function, SingleBitFunction,
                              ReadCoils, ReadDiscreteInputs)
from modbus.exceptions import IllegalDataValueError, IllegalDataAddressError


@pytest.fixture
def function():
    """ Return instance of :class:`modbus.functions.Function`. """
    return Function()


@pytest.fixture
def single_bit_function():
    function_code = 1
    starting_address = 100
    quantity = 3

    pdu = struct.pack('>BHH', function_code, starting_address, quantity)

    return ReadCoils.create_from_request_pdu(pdu)


@pytest.fixture
def single_bit_enpoint():
    """ Return endpoint for Modbus request acting on single bit values,
    like Modbus function codes 01 an 02.
    """
    def endpoint(slave_id, address):
        return address % 2


@pytest.fixture
def route_map():
    return Map()


class TestFunction:
    def test_create_from_request_pdu(self):
        """ Call should raise NotImplementedError. """
        with pytest.raises(NotImplementedError):
            Function.create_from_request_pdu('pdu')

    def test_execute(self, function):
        """ Call should raise NotImplementedError. """
        with pytest.raises(NotImplementedError):
            function.execute('slave_id', 'route_map')

    def test_get_response_pdu(self, function):
        """ Call should raise NotImplementedError. """
        with pytest.raises(NotImplementedError):
            function.get_response_pdu('data')


class TestSingleBitFunction:
    @pytest.mark.parametrize('quantity', [
        0,
        2001,
    ])
    def test_create_single_bit_function_with_invalid_quantity(self, quantity):
        """ When initiated with incorrect quantity, constructor should raise
        an error.
        """
        with pytest.raises(IllegalDataValueError):
            ReadCoils(100, quantity)

    def test_create_from_request_pdu(self):
        """ Call should return instance with correct attributes and vaules. """
        function_code = 1
        starting_address = 100
        quantity = 3

        pdu = struct.pack('>BHH', function_code, starting_address, quantity)

        function = SingleBitFunction.create_from_request_pdu(pdu)
        assert function.starting_address == starting_address
        assert function.quantity == quantity

    def test_execute(self, single_bit_function, route_map, monkeypatch):
        """ SingleBitFunction.execute should execute endpoints an return correct
        result.
        """
        def match_mock(*args, **kwargs):
            return lambda slave_id, address: address % 2

        monkeypatch.setattr(route_map, 'match', match_mock)
        assert single_bit_function.execute(1, route_map) == [0, 1, 0]

    def test_execute_raising_illegal_data_error(self, single_bit_function,
                                                route_map, monkeypatch):
        """ When no route is found for request, execute should raise an
        IllegalDataAddressError.
        """
        with pytest.raises(IllegalDataAddressError):
            single_bit_function.execute(1, route_map)

    @pytest.mark.parametrize('data,expectation', [
        ([0, 1, 1], b'\x01\x01\x03'),
        ([1, 0, 0, 0, 0, 0, 0, 1, 0], b'\x01\x02\x02\x01'),
    ])
    def test_create_response_pdu(self, single_bit_function, data, expectation):
        assert single_bit_function.create_response_pdu(data) == expectation


@pytest.mark.parametrize('pdu,cls', [
    (b'\x01\x00d\x00\x03', ReadCoils),
    (b'\x02\x00d\x00\x03', ReadDiscreteInputs),
])
def test_function_factory(pdu, cls):
    assert isinstance(function_factory(pdu), cls)


def test_caching_of_function_factory():
    """ Equal calls to :meth:`function_factory` should return same response.
    """
    function_code = 1
    starting_address = 100
    quantity = 3

    pdu = struct.pack('>BHH', function_code, starting_address, quantity)

    # Call method twice, both with same input...
    function_1 = function_factory(pdu)
    function_2 = function_factory(pdu)

    # ...output should be the same.
    assert id(function_1) == id(function_2)

    starting_address = 101
    pdu = struct.pack('>BHH', function_code, starting_address, quantity)

    # But when called with different pdu...
    function_3 = function_factory(pdu)

    # ...output should not be the same as previous calls.
    assert id(function_1) is not id(function_3)