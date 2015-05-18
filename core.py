import json
import asyncio
import sys
from time import time, localtime, strftime
import traceback


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
			#print('{}({})::recieve_messages_for_tag[{}]'.format(self, cls, cls.CONSUMES.TAG))
			host.recieve_messages_for_tag(cls.CONSUMES.TAG, self.input)
		if cls.PRODUCES:
			#print('{}({})::get_input_queue[{}]'.format(self, cls, cls.PRODUCES.TAG))
			self.add_output_queue(cls.PRODUCES.TAG,
				host.get_input_queue(cls.PRODUCES.TAG))

	@classmethod
	def register_queue_functions(cls, self):
		if cls.CONSUMES:
			for tag, function in cls.ROUTES.items():
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

	CLOSING_TAG = 'middleware::closing'

	def __init__(self):
		super(BotMiddleware, self).__init__()
		self.input = asyncio.JoinableQueue()
		self._output = {}
		self.task = None
		self.closing = False
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

	@asyncio.coroutine
	def start_close(self):
		self.closing = True
		yield from self.input.put((self.CLOSING_TAG, self.CLOSING_TAG))

	def done(self):
		return self.task.done()

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
			if tag != self.CLOSING_TAG:
				handler = None
				try:
					handler = self._recv_functions[tag]
				except:
					print('handler for message type "{}" not found!'.format(tag))

				try:
					yield from handler(data)
				except Exception as e:
					if self._exception_handler:
						yield from self._exception_handler(e)
			
			self.input.task_done()

			if self.closing and self.input.empty():
				break


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

class Log(object):
	VERBOSE = 4
	DEBUG = 3
	WARN = 2
	ERROR = 1
	EXCEPTION = 0

	def __init__(self, level, source='', *args):
		super(Log, self).__init__()
		self.args = args
		self.level = level
		self.traceback = None
		if level == Log.EXCEPTION:
			self.traceback = traceback.format_exc()
		else:
			self.traceback = ''.join(traceback.format_stack())
			
		self.file = None
		self.timestamp = int(time())
		self.source = source

	def add_traceback(self, log):
		for line in self.traceback.split('\n'):
			log += '\n{}'.format(line)

		return log


	def log_str(self):
		level_string = {self.VERBOSE:'V', self.DEBUG:'D', self.WARN:'W', self.ERROR:'E', self.EXCEPTION:'X'}	
		timestring = strftime("%m/%d|%H:%M:%S", localtime(self.timestamp))
		info_string = '[{}|{:.4}|{}]'.format(timestring, self.source, level_string[self.level])
		try:
			arg_string = str(self.args[0]).format(*self.args[1:])
		except Exception as e:
			arg_string = 'malformed log: {} -> {}'.format(self, e)
			self.level = Log.EXCEPTION

		full_log = arg_string
		if self.level == Log.EXCEPTION:
			full_log = self.add_traceback(full_log)

		log_lines = []
		first = True
		for line in full_log.split('\n'):
			if first:
				log_lines.append('{}: {}'.format(info_string, line))
				first = False
			else:
				log_lines.append('{}| {}'.format(info_string, line))

		return '\n'.join(log_lines)

	def __str__(self):
		return 'source: {}, level: {}, args: {}'.format(self.source, self.level, self.args)

class LoggerMiddleware(BotMiddleware):
	TAG = 'tag_logger_middleware'
	TYPE = BotMiddleware.INPUT

	def __init__(self):
		super(LoggerMiddleware, self).__init__()
		default_log = sys.argv[0].replace('.py', '') + '.log'
		self.log_file_names = [default_log]
		self.default_file = default_log
		self.log_files = {}

	def create_files(self):
		for file_name in self.log_file_names:
			self.log_files[file_name] = open(file_name, 'a')

	def load_state_from_db(self, db):
		super(LoggerMiddleware, self).load_state_from_db(db)
		self.create_files()

	@asyncio.coroutine
	def handle_event(self, log):
		if isinstance(log, Log):
			file_name = log.file if log.file else self.default_file
			fh = self.log_files[file_name]
			print(log.log_str())
			fh.write(log.log_str() + '\n')
			fh.flush()

class UsesLogger(UsesMiddleware):
	PRODUCES = LoggerMiddleware
	LOG_NAME = ''

	@classmethod
	def setup_self(cls, self):
		self._exception_handler = self.handle_exception

	def log(self, level, *args):
		l = Log(level, self.LOG_NAME, *args)
		asyncio.async(
			self.send(l, tag=LoggerMiddleware.TAG)
			)

	def exception(self, *args):
		return self.log(Log.EXCEPTION, *args)

	def error(self, *args):
		return self.log(Log.ERROR, *args)

	def debug(self, *args):
		return self.log(Log.DEBUG, *args)

	def verbose(self, *args):
		return self.log(Log.VERBOSE, *args)

	@asyncio.coroutine
	def handle_exception(self, e):
		self.exception('Caught exception in {}.loop: {}', self.__class__, e)
		yield from asyncio.sleep(1)

class FakeRAWMiddleware(BotMiddleware):
	TAG = 'tag_raw'

class FakeActionMiddleware(BotMiddleware):
	TAG = 'tag_do_action'