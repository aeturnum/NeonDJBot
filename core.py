import json
import asyncio

from time import time


class Message(object):
	def __init__(self, data, past=False):
		self.data = data
		self.past = past

		if 'time' in self.data:
			self.timestamp = int(self.data['time'])
			self.uid = self.data['id']

class Packet(object):
	def __init__(self, packet):
		packet = json.loads(packet)
		self.timestamp = int(time())
		if 'type' in packet:
			self.type = packet['type']
		else:
			self.type = ''
		if 'data' in packet:
			self.data = packet['data']
			if 'time' in self.data:
				self.timestamp = int(self.data['time'])
		else:
			self.data = packet

	def messages(self):
		if self.type == 'send-event' or self.type == 'send-reply':
			return [Message(self.data)]
		elif self.type == 'snapshot-event':
			return [Message(m, past=True) for m in self.data['log']]

		return []

	def __str__(self):
		return '[{}]{}'.format(self.type, self.data)

class UsesMiddleware(object):
	PRODUCES = None
	CONSUMES = None
	ROUTES = {}
	
	@classmethod
	def register_queues(cls, self, host):
		if cls.CONSUMES:
			print('{}({})::recieve_messages_for_tag[{}]'.format(self, cls, cls.CONSUMES.TAG))
			host.recieve_messages_for_tag(cls.CONSUMES.TAG, self.input)
		if cls.PRODUCES:
			print('{}({})::get_input_queue[{}]'.format(self, cls, cls.PRODUCES.TAG))
			self.add_output_queue(cls.PRODUCES.TAG,
				host.get_input_queue(cls.PRODUCES.TAG))

	@classmethod
	def register_queue_functions(cls, self):
		if cls.CONSUMES:
			for tag, function in cls.ROUTES:
				self._recv_functions[tag] = function

		cls.setup_self(self)
	
	@classmethod
	def setup_self(cls, self):
		return

	@classmethod
	def collect(cls, middleware):
		if cls.CONSUMES:
			middleware.append(cls.CONSUMES)
		if cls.PRODUCES:
			middleware.append(cls.PRODUCES)

	@classmethod
	def set_handler(cls, self, function):
		if cls.CONSUMES:
			#print('{}::Setting the handler for [{}] to {} '.format(self, cls.CONSUMES.TAG, function))
			self._recv_functions[cls.CONSUMES.TAG] = function

class BotMiddleware(object):
	TAG = ''
	INPUT = 'INPUT'
	OUTPUT = 'OUTPUT'
	TYPE = ''
	MIDDLEWARE_SUPPORT_REQUESTS = {}

	def __init__(self):
		self.input = asyncio.JoinableQueue()
		self._output = {}
		self.task = None
		self._recv_functions = {}
		self._exception_handler = None
		self._add_routes()

	def get_middleware_required(self):
		middleware = []
		for cls in self.__class__.__bases__:
			if issubclass(cls, UsesMiddleware):
				cls.collect(middleware)
				
		return middleware

	def register_queues(self, host):
		for cls in self.__class__.__bases__:
			if issubclass(cls, UsesMiddleware):
				cls.register_queues(self, host)

	def add_output_queue(self, tag, queue):
		if self.TAG not in self._output:
			self._output[tag] = []

		self._output[tag].append(queue)

	def _add_routes(self):
		for cls in self.__class__.__bases__:
			if issubclass(cls, UsesMiddleware):
				cls.register_queue_functions(self)

		self._recv_functions[self.TAG] = self.handle_event


	def load_state_from_db(self, db):
		return

	def save_state_to_db(self, db):
		return

	def create_task(self, loop, db):
		self.task = loop.create_task(self.run(db))

	def request_support(self, request):
		return False

	def support_request_failed(self, middleware_tag, middleware_response):
		raise ValueError('Middleware {} returned an errror: {}'.format(middleware_tag, middleware_response))

	def cancel(self):
		if self.task and not self.task.done():
			self.task.cancel()

	@asyncio.coroutine
	def setup(self, db):
		self.db = db

	@asyncio.coroutine
	def handle_event(self, input):
		return

	@asyncio.coroutine
	def run(self, db):
		yield from self.setup(db)
		while True:
			tag, data = yield from self.input.get()
			handler = None
			try:
				handler = self._recv_functions[tag]
			except:
				print('handler for message type "{}" not found!'.format(tag))
			try:
				yield from handler(data)
			except Exception as e:
				print('exception')
				if self._exception_handler:
					print('handler set!')
					yield from self._exception_handler(e)
				pass
			self.input.task_done()

	@staticmethod
	@asyncio.coroutine
	def _send(queue_list, tag, message):
		#print('_send([q], {}, {})'.format(tag, message))
		for q in queue_list:
			yield from q.put((tag, message))

	@asyncio.coroutine
	def send(self, data, tag=None):
		if not tag:
			tag = self.TAG
		yield from self._send(self._output[tag], tag, data)

	def __eq__(self, other):
		if hasattr(other, 'TAG'):
			return type(self.TAG) == type(other.TAG)
		return False

	def __hash__(self):
		return hash(self.TAG)

class FakeRAWMiddleware(BotMiddleware):
	TAG = 'tag_raw'

class FakeActionMiddleware(BotMiddleware):
	TAG = 'tag_do_action'