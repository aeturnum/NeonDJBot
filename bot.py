import asyncio
from time import time
import signal

from tinydb import TinyDB
import websockets

from core import Packet, BotMiddleware, LoggerMiddleware, Log

from actions import (
	PingAction, 
	SetNickAction, 
	)

class Bot(object):
	TAG_RAW = 'tag_raw'
	TAG_DO_ACTION = 'tag_do_action'

	RECONNECT_TIMEOUT = 5

	def __init__(self, 	room_address, db_name='./MusicBotDB.json'):
		self.loop = asyncio.get_event_loop()
		self.action_queue = asyncio.JoinableQueue()
		self.packet_queue = asyncio.JoinableQueue()
		self.ws_queue = asyncio.JoinableQueue()

		self.action_task = None
		self.recv_task = None

		self.room_address = room_address
		self.ws = None
		self.db = TinyDB(db_name)
		self.mid = 0

		self.next_ping_time = int(time()) 
		self.last_latency = None
		self.last_ping_log = int(time())

		self.reset_mid()
		### middleware

		self.middleware = {}
		self.queues = {}
		self.packet_queues = []

		self.add_middleware(LoggerMiddleware())
		self.log_queue = self.get_input_queue(LoggerMiddleware.TAG)

		signal.signal(signal.SIGINT, self.sigint_handler())
		self.closing = False

	def sigint_handler(self):
		def handler(signum, frame):
			self.closing = True
		return handler

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
			for tag, request in middleware.MIDDLEWARE_SUPPORT_REQUESTS.items():
				result = self.middleware[tag].request_support(request)
				if result != True:
					middleware.support_request_failed(tag, result)
					self.error('middleware request for support failed: {} -> {}'.format(middleware, tag))
					return

			middleware.create_task(self.loop, self.db)

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
	def connect_ws(self, max_attempts = -1):
		attempts = 0

		# if we've ever had a web socket, empty ws_queue
		if self.ws:
			yield from self.ws_queue.get()
			self.ws_queue.task_done()

		while True:
			try:
				self.ws = yield from websockets.connect(self.room_address)
				break
			except:
				if self.closing:
					return True

				self.debug('connection attempt {} failed', attempts)
				yield from asyncio.sleep(5)
				attempts += 1
				if max_attempts > 0 and attempts >= max_attempts:
					self.debug('max connection attempts exceeded, closing.')
					return False

		self.debug('connection succeeded')
		if self.closing:
			yield from self.ws.close()
		else:
			yield from self.setup()
			yield from self.ws_queue.put(self.ws)

			new_recv_task = self.loop.create_task(self.recv_loop())
			if self.recv_task:
				self.recv_task.cancel()
			self.recv_task = new_recv_task
		return True

	@asyncio.coroutine
	def close_bot(self):
		for m in self.middleware.values():
			yield from m.start_close()

		if self.ws:
			yield from self.ws.close()

		while True:
			done = True
			for m in self.middleware.values():
				done = done and m.done()

			if done:
				break
			else:
				yield from asyncio.sleep(0.1)

		# empty queue
		yield from self.action_queue.join()
		self.action_task.cancel()
		

	@asyncio.coroutine
	def connection_monitor(self):
		# create connection
		connect_succeeded = yield from self.connect_ws(max_attempts = 1)
		# sleep to allow first ping
		yield from asyncio.sleep(1)
		while True:
			now = int(time())

			if not connect_succeeded:
				self.debug('Max connection attempts exceeded, closing bot.')
				self.closing = True

			if self.closing:
				yield from self.close_bot()
				break

			yield from self.check_tasks()

			if self.next_ping_timelimit <= now:
				self.debug('Ping timeout has been missed, re-connecting')
				connect_succeeded = yield from self.connect_ws(max_attempts = 600)
			else:
				yield from asyncio.sleep(1)

	def anticipate_ping(self, ping_packet):
		now = int(time())
		latency = now - ping_packet.data['time']
		if latency != self.last_latency:
			self.debug('Current latency from server: {}', latency)
			self.last_latency = latency
		# delay before reconnect is equal to next - now
		# plus timeout and travel time
		self.next_ping_timelimit = ping_packet.data['next'] + self.RECONNECT_TIMEOUT + latency

	@asyncio.coroutine
	def setup(self):
		return

	@asyncio.coroutine
	def recv_loop(self):
		while True:
			packet = yield from self.ws.recv()
			if packet:
				try:
					packet = Packet(packet)
				except:
					self.error('Packet {} did not meet expectations! Please investigate!'.format(packet))
					continue
				#self.verbose('Packet type: {}', packet.type)

				if packet.type == 'ping-event':
					self.anticipate_ping(packet)
					yield from self.action_queue.put((self.TAG_DO_ACTION, PingAction()))
				else:
					for queue in self.packet_queues:
						yield from queue.put((self.TAG_RAW, packet))
			else:
				if self.closing:
					break
				else:
					self.debug("websocket is closed, reconnecting")
					yield from self.connect_ws(max_attempts = 600)

	@asyncio.coroutine
	def execute_actions_task(self):
		while True:
			tag, action = yield from self.action_queue.get()

			# set websocket
			ws = yield from self.ws_queue.get()
			action.ws = ws
			yield from self.ws_queue.put(ws)
			self.ws_queue.task_done()

			#print('processing action: {}'.format(action))
			task = action.get_coroutine(self.db, self.mid, self.action_queue)
			asyncio.async(task())

			self.action_queue.task_done()

	@asyncio.coroutine
	def check_tasks(self):
		tasks = [(tag, middleware.task) for tag, middleware in self.middleware.items()]
		tasks.append(('tag_do_action', self.action_task))
		tasks.append(('tag_raw', self.recv_task))

		for tag, task in tasks:
			# should not happen, really
			if task.done():
				try:
					result = task.result()
					self.error('{} middleware done early, returned: {}', tag, result)
					# catch all the things!
				except Exception as e:
					self.exception('{} middleware threw exception: {}', tag, e)

				# again!
				if tag in self.middleware:
					middleware = self.middleware[tag]
					middleware.create_task(self.loop, self.db)
				elif tag == 'tag_do_action':
					self.action_task = self.loop.create_task(self.execute_actions_task())
				elif tag == 'tag_raw':
					seld.debug('Recv loop will be reconnected by connection monitor.')
			

	def connect(self):
		self.action_task = self.loop.create_task(self.execute_actions_task())

		self.loop.run_until_complete(self.connection_monitor())
