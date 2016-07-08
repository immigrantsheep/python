import logging
import unittest
import pubnub as pn
from pubnub import utils

from threading import Event
from pubnub.callbacks import SubscribeCallback
from pubnub.exceptions import PubNubException
from pubnub.models.consumer.pubsub import PNPublishResult, PNMessageResult
from pubnub.pubnub import PubNub
from tests import helper
from tests.helper import CountDownLatch, pnconf_sub, pnconf_sub_copy
from six.moves.queue import Queue

pn.set_stream_logger('pubnub', logging.DEBUG)


class SubscribeListener(SubscribeCallback):
    def __init__(self):
        self.connected = False
        self.connected_event = Event()
        self.disconnected_event = Event()
        self.presence_queue = Queue()
        self.message_queue = Queue()

    def status(self, pubnub, status):
        if utils.is_subscribed_event(status) and not self.connected_event.is_set():
            self.connected_event.set()
        elif utils.is_unsubscribed_event(status) and not self.disconnected_event.is_set():
            self.disconnected_event.set()

    def message(self, pubnub, message):
        self.message_queue.put(message)

    def presence(self, pubnub, presence):
        self.presence_queue.put(presence)

    def wait_for_connect(self):
        if not self.connected_event.is_set():
            self.connected_event.wait()
        else:
            raise Exception("the instance is already connected")

    def wait_for_disconnect(self):
        if not self.disconnected_event.is_set():
            self.disconnected_event.wait()
        else:
            raise Exception("the instance is already connected")

    def wait_for_message_on(self, *channel_names):
        channel_names = list(channel_names)
        while True:
            env = self.message_queue.get()
            self.message_queue.task_done()
            if env.actual_channel in channel_names:
                return env
            else:
                continue

    def wait_for_presence_on(self, *channel_names):
        channel_names = list(channel_names)
        while True:
            env = self.presence_queue.get()
            self.presence_queue.task_done()
            if env.actual_channel[:-7] in channel_names:
                return env
            else:
                continue


class NonSubscribeListener(object):
    def __init__(self):
        self.result = None
        self.done_event = Event()

    def callback(self, result, status):
        self.result = result
        self.done_event.set()

    def await(self, timeout=5):
        """ Returns False if a timeout happened, otherwise True"""
        return self.done_event.wait(timeout)


class TestPubNubSubscribe(unittest.TestCase):
    def test_subscribe_unsubscribe(self):
        pubnub = PubNub(pnconf_sub_copy())
        ch = helper.gen_channel("test-subscribe-sub-unsub")

        try:
            listener = SubscribeListener()
            pubnub.add_listener(listener)

            pubnub.subscribe().channels(ch).execute()
            listener.wait_for_connect()

            pubnub.unsubscribe().channels(ch).execute()
            listener.wait_for_disconnect()
        except PubNubException as e:
            self.fail(e)
        finally:
            pubnub.stop()

    def test_subscribe_pub_unsubscribe(self):
        ch = helper.gen_channel("test-subscribe-sub-pub-unsub")
        pubnub = PubNub(pnconf_sub_copy())
        subscribe_listener = SubscribeListener()
        publish_operation = NonSubscribeListener()
        message = "hey"

        try:
            pubnub.add_listener(subscribe_listener)

            pubnub.subscribe().channels(ch).execute()
            subscribe_listener.wait_for_connect()

            pubnub.publish().channel(ch).message(message).async(publish_operation.callback)
            if not publish_operation.await():
                self.fail("Publish operation timeout")

            publish_result = publish_operation.result
            assert isinstance(publish_result, PNPublishResult)
            assert publish_result.timetoken > 0

            result = subscribe_listener.wait_for_message_on(ch)
            assert isinstance(result, PNMessageResult)
            assert result.actual_channel == ch
            assert result.subscribed_channel == ch
            assert result.timetoken > 0
            assert result.message == message

            pubnub.unsubscribe().channels(ch).execute()
            subscribe_listener.wait_for_disconnect()
        except PubNubException as e:
            self.fail(e)
        finally:
            pubnub.stop()

    def test_join_leave(self):
        ch = helper.gen_channel("test-subscribe-join-leave")
        ch_pnpres = ch + "-pnpres"

        pubnub = PubNub(pnconf_sub_copy())
        pubnub_listener = PubNub(pnconf_sub_copy())
        callback_messages = SubscribeListener()
        callback_presence = SubscribeListener()

        pubnub.config.uuid = helper.gen_channel("messenger")
        pubnub_listener.config.uuid = helper.gen_channel("listener")

        try:
            pubnub.add_listener(callback_messages)
            pubnub_listener.add_listener(callback_presence)

            pubnub_listener.subscribe().channels(ch).with_presence().execute()
            callback_presence.wait_for_connect()

            envelope = callback_presence.wait_for_presence_on(ch)
            assert envelope.actual_channel == ch_pnpres
            assert envelope.event == 'join'
            assert envelope.uuid == pubnub_listener.uuid

            pubnub.subscribe().channels(ch).execute()
            callback_messages.wait_for_connect()

            envelope = callback_presence.wait_for_presence_on(ch)
            assert envelope.actual_channel == ch_pnpres
            assert envelope.event == 'join'
            assert envelope.uuid == pubnub.uuid

            pubnub.unsubscribe().channels(ch).execute()
            callback_messages.wait_for_disconnect()

            envelope = callback_presence.wait_for_presence_on(ch)
            assert envelope.actual_channel == ch_pnpres
            assert envelope.event == 'leave'
            assert envelope.uuid == pubnub.uuid

            pubnub_listener.unsubscribe().channels(ch).execute()
            callback_presence.wait_for_disconnect()
        except PubNubException as e:
            self.fail(e)
        finally:
            pubnub.stop()
            pubnub_listener.stop()
