import asyncio
from core import Packet, BotMiddleware
from tinydb import TinyDB
import websockets

from actions import (
	PingAction, 
	SetNickAction, 
	)

class Bot(object):
	TAG_RAW = 'tag_raw'
	TAG_DO_ACTION = 'tag_do_action'

	def __init__(self, 	room_address):
		self.loop = asyncio.get_event_loop()
		self.action_queue = asyncio.JoinableQueue()
		self.packet_queue = asyncio.JoinableQueue()

		self.room_address = room_address
		self.ws_lock = asyncio.Lock()
		self.ws = None
		self.db = TinyDB('./MusicBotDB_dev.json')
		self.internal_coroutines = []
		self.mid = 0

		self.reset_mid()
		### middleware

		self.middleware = {}
		self.queues = {}
		self.packet_queues = []

	def add_middleware(self, middleware):
		if not middleware.TAG in self.middleware:
			print('new middleware: ', middleware)
			for required_middleware in middleware.get_middleware_required():
				print('adding required middleware: ', required_middleware)
				self.add_middleware(required_middleware())

			print('indexing middleware: ', middleware)
			self.middleware[middleware.TAG] = middleware

			middleware.register_queues(self)
			middleware.load_state_from_db(self.db)
			middleware.create_task(self.loop, self.db)
			for tag, request in middleware.MIDDLEWARE_SUPPORT_REQUESTS.items():
				result = self.middleware[tag].request_support(request)
				if result != True:
					middleware.support_request_failed(tag, result)
					print('middleware request for support failed: {} -> {}'.format(middleware, tag))
					return

	def recieve_messages_for_tag(self, tag, queue):
		if tag == self.TAG_RAW:
			self.packet_queues.append(queue)
		else:
			if self.middleware[tag].TYPE == BotMiddleware.OUTPUT:
				self.middleware[tag].add_output_queue(tag, queue)

	def get_input_queue(self, tag):
		if tag == self.TAG_DO_ACTION:
			print('get_input_queue({}) -> {}'.format(tag, self.action_queue))
			return self.action_queue
		else:
			print('get_input_queue({}) -> {}'.format(tag, self.middleware[tag].input))
			if self.middleware[tag].TYPE == BotMiddleware.INPUT:
				return self.middleware[tag].input


	def reset_mid(self):
		def mid_itr():
			i = 0
			while True:
				yield i
				i += 1
		self.mid = mid_itr()

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
					print('connection failed')
					yield from asyncio.sleep(1)
					continue

				print('connection succeeded')
				self.ws_lock.release()
				yield from self.setup()

			packet = yield from self.ws.recv()
			if packet:
				try:
					packet = Packet(packet)
				except:
					print('Packet {} did not meet expectations! Please investigate!'.format(packet))
					continue

				if packet.type == 'ping-event':
					yield from self.action_queue.put((self.TAG_DO_ACTION, PingAction()))
				else:
					for message in packet.messages():
						for queue in self.packet_queues:
							yield from queue.put((self.TAG_RAW, message))

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