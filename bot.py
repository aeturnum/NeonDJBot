import asyncio
from time import time
from core import Packet, BotMiddleware, LoggerMiddleware, Log
from tinydb import TinyDB
import websockets

from actions import (
	PingAction, 
	SetNickAction, 
	)

class Bot(object):
	TAG_RAW = 'tag_raw'
	TAG_DO_ACTION = 'tag_do_action'

	RECONNECT_TIMEOUT = 5

	def __init__(self, 	room_address):
		self.loop = asyncio.get_event_loop()
		self.action_queue = asyncio.JoinableQueue()
		self.packet_queue = asyncio.JoinableQueue()

		self.room_address = room_address
		self.ws_lock = asyncio.Lock()
		self.ws = None
		self.db = TinyDB('./MusicBotDB.json')
		self.internal_coroutines = []
		self.mid = 0
		self.reconnect_task = None

		self.reset_mid()
		### middleware

		self.middleware = {}
		self.queues = {}
		self.packet_queues = []

		self.add_middleware(LoggerMiddleware())
		self.log_queue = self.get_input_queue(LoggerMiddleware.TAG)

	def add_middleware(self, middleware):
		if not middleware.TAG in self.middleware:
			#print('new middleware: ', middleware)
			for required_middleware in middleware.get_middleware_required():
				#print('adding required middleware: ', required_middleware)
				self.add_middleware(required_middleware())

			#print('indexing middleware: ', middleware)
			self.middleware[middleware.TAG] = middleware

			middleware.register_queues(self)
			middleware.load_state_from_db(self.db)
			middleware.create_task(self.loop, self.db)
			for tag, request in middleware.MIDDLEWARE_SUPPORT_REQUESTS.items():
				result = self.middleware[tag].request_support(request)
				if result != True:
					middleware.support_request_failed(tag, result)
					self.error('middleware request for support failed: {} -> {}'.format(middleware, tag))
					return

	def recieve_messages_for_tag(self, tag, queue):
		if tag == self.TAG_RAW:
			self.packet_queues.append(queue)
		else:
			if self.middleware[tag].TYPE == BotMiddleware.OUTPUT:
				self.middleware[tag].add_output_queue(tag, queue)

	def get_input_queue(self, tag):
		if tag == self.TAG_DO_ACTION:
			#print('get_input_queue({}) -> {}'.format(tag, self.action_queue))
			return self.action_queue
		else:
			#print('get_input_queue({}) -> {}'.format(tag, self.middleware[tag].input))
			if self.middleware[tag].TYPE == BotMiddleware.INPUT:
				return self.middleware[tag].input

	def reset_mid(self):
		def mid_itr():
			i = 0
			while True:
				yield i
				i += 1
		self.mid = mid_itr()

	#do doo do, poor engineering practices
	# copied and pasted from logging middleware because I am a bad person
	@asyncio.coroutine
	def log(self, level, *args):
		l = Log(level, 'BOT', *args)
		yield from self.log_queue.put((LoggerMiddleware.TAG ,l))

	def exception(self, *args):
		asyncio.async(self.log(Log.EXCEPTION, *args))

	def error(self, *args):
		asyncio.async(self.log(Log.ERROR, *args))

	def debug(self, *args):
		asyncio.async(self.log(Log.DEBUG, *args))

	def verbose(self, *args):
		asyncio.async(self.log(Log.VERBOSE, *args))

	@asyncio.coroutine
	def trigger_reconnect(self, delay):
		yield from asyncio.sleep(delay)
		self.debug('Server ping is overdue, triggering re-connect')
		try:
			self.ws = yield from websockets.connect(self.room_address)
		except:
			self.error("Reconnect failed, sleeping for 10 seconds and trying again!")
			# like violence, if it doesn't work, apply more
			self.reconnect_task = self.loop.create_task(self.trigger_reconnect(10))


	def set_reconnect_timeout(self, ping_packet):
		now = int(time())
		latency = now - ping_packet.data['time']
		self.debug('Current latency from server: {}', latency)
		# delay before reconnect is equal to next - now
		# plus timeout and travel time
		delay = (ping_packet.data['next'] - now) + (self.RECONNECT_TIMEOUT + latency)
		if self.reconnect_task:
			self.reconnect_task.cancel()
		self.reconnect_task = self.loop.create_task(self.trigger_reconnect(delay))

	@asyncio.coroutine
	def setup(self):
		return

	@asyncio.coroutine
	def recv_loop(self):
		yield from self.setup()
		while True:
			if not self.ws or not packet: #server d/c or connect
				yield from self.ws_lock
				try:
					self.ws = yield from websockets.connect(self.room_address)
				except:
					self.debug('connection failed')
					yield from asyncio.sleep(1)
					continue

				self.debug('connection succeeded')
				self.ws_lock.release()
				yield from self.setup()

			packet = yield from self.ws.recv()
			if packet:
				try:
					packet = Packet(packet)
				except:
					self.error('Packet {} did not meet expectations! Please investigate!'.format(packet))
					continue
				#self.verbose('Packet type: {}', packet.type)

				if packet.type == 'ping-event':
					self.set_reconnect_timeout(packet)
					yield from self.action_queue.put((self.TAG_DO_ACTION, PingAction()))
				else:
					for queue in self.packet_queues:
						yield from queue.put((self.TAG_RAW, packet))

	@asyncio.coroutine
	def execute_actions_task(self):
		while True:
			if self.ws:
				tag, action = yield from self.action_queue.get()
				# set websocket
				yield from self.ws_lock
				action.ws = self.ws
				self.ws_lock.release()

				#print('processing action: {}'.format(action))
				task = action.get_coroutine(self.db, self.mid, self.action_queue)
				asyncio.async(task())

				self.action_queue.task_done()
			else:
				yield from asyncio.sleep(1)
			

	def connect(self):
		action_task = self.loop.create_task(self.execute_actions_task())

		self.internal_coroutines = [action_task]
		self.loop.run_until_complete(self.recv_loop())