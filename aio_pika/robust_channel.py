import asyncio
from typing import Callable, Any, Union, Awaitable

from logging import getLogger

from .exchange import Exchange, ExchangeType
from .message import IncomingMessage
from .queue import Queue
from .common import BaseChannel, FutureStore
from .channel import Channel
from .robust_queue import RobustQueue
from .robust_exchange import RobustExchange


log = getLogger(__name__)

FunctionOrCoroutine = Union[
    Callable[[IncomingMessage], Any],
    Awaitable[IncomingMessage]
]


class RobustChannel(Channel):
    """ Channel abstraction """

    QUEUE_CLASS = RobustQueue
    EXCHANGE_CLASS = RobustExchange

    def __init__(self, connection, loop: asyncio.AbstractEventLoop,
                 future_store: FutureStore, channel_number: int=None,
                 publisher_confirms: bool=True, on_return_raises=False,
                 success_close_codes: tuple=(0,)):
        """

        :param connection: :class:`aio_pika.adapter.AsyncioConnection` instance
        :param loop:
            Event loop (:func:`asyncio.get_event_loop()` when :class:`None`)
        :param future_store: :class:`aio_pika.common.FutureStore` instance
        :param channel_number: specify the channel number explicit
        :param publisher_confirms:
            False if you don't need delivery confirmations
            (in pursuit of performance)
        :param on_return_raises:
            raise an :class:`aio_pika.exceptions.UnroutableError`
            when mandatory message will be returned
        :param success_close_codes: don't set exception and don't log error if
            the channel was closed with specified codes
        """
        super().__init__(
            loop=loop,
            future_store=future_store.get_child(),
            connection=connection,
            channel_number=channel_number,
            publisher_confirms=publisher_confirms,
            on_return_raises=on_return_raises,
            success_close_codes=success_close_codes,
        )

        self._closed = False
        self._exchanges = dict()
        self._queues = dict()
        self._qos = 0, 0

    async def on_reconnect(self, connection, channel_number):
        exc = ConnectionError('Auto Reconnect Error')

        if not self._closing.done():
            self._closing.set_exception(exc)

        self._closing = self.loop.create_future()
        self._futures.reject_all(exc)
        self._connection = connection
        self._channel_number = channel_number

        await self.initialize()

        for exchange in self._exchanges.values():
            await exchange.on_reconnect(self)

        for queue in self._queues.values():
            await queue.on_reconnect(self)

    async def initialize(self, timeout=None):
        result = await super().initialize()

        prefetch_count, prefetch_size = self._qos

        await self.set_qos(
            prefetch_count=prefetch_count,
            prefetch_size=prefetch_size
        )

        return result

    async def set_qos(self, prefetch_count: int=0, prefetch_size: int=0,
                      all_channels=False, timeout: int=None):
        if all_channels:
            raise NotImplementedError("Not available to RobustConnection")

        self._qos = prefetch_count, prefetch_size

        return await super().set_qos(
            prefetch_count=prefetch_count,
            prefetch_size=prefetch_size,
            timeout=timeout,
        )

    @BaseChannel._ensure_channel_is_open
    async def close(self) -> None:
        if self._closed:
            return

        async with self._write_lock:
            self._closed = True
            self._channel.close()
            await self.closing
            self._channel = None

    async def declare_exchange(self, name: str,
                               type: ExchangeType=ExchangeType.DIRECT,
                               durable: bool=None, auto_delete: bool=False,
                               internal: bool=False, passive: bool=False,
                               arguments: dict=None, timeout: int=None,
                               robust: bool=True) -> Exchange:

        exchange = await super().declare_exchange(
            name=name, type=type, durable=durable, auto_delete=auto_delete,
            internal=internal, passive=passive, arguments=arguments,
            timeout=timeout,
        )

        if not internal and robust:
            self._exchanges[name] = exchange

        return exchange

    async def exchange_delete(self, exchange_name: str, timeout: int=None,
                              if_unused=False, nowait=False):
        result = await super().exchange_delete(
            exchange_name=exchange_name, timeout=timeout,
            if_unused=if_unused, nowait=nowait
        )

        self._exchanges.pop(exchange_name, None)

        return result

    async def declare_queue(self, name: str=None, *, durable: bool=None,
                            exclusive: bool=False,
                            passive: bool=False, auto_delete: bool=False,
                            arguments: dict=None, timeout: int=None,
                            robust: bool=True) -> Queue:

        queue = await super().declare_queue(
            name=name, durable=durable, exclusive=exclusive,
            passive=passive, auto_delete=auto_delete,
            arguments=arguments, timeout=timeout,
        )

        if robust:
            self._queues[name] = queue

        return queue

    async def queue_delete(self, queue_name: str, timeout: int=None,
                           if_unused: bool=False, if_empty: bool=False,
                           nowait: bool=False):
        result = await super().queue_delete(
            queue_name=queue_name, timeout=timeout,
            if_unused=if_unused, if_empty=if_empty, nowait=nowait
        )

        self._queues.pop(queue_name, None)
        return result


__all__ = ('RobustChannel',)
