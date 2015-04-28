import asyncio
import subprocess

from bot import Bot
from middleware import BotMiddleware, UsesLogger, UsesRaw

class BotMonitorMiddleware(BotMiddleware, UsesLogger, UsesRaw):
	TAG = 'tag_bot_monitor'
	TYPE = BotMiddleware.OUTPUT

	LOG_NAME = 'Monitor'

	def __init__(self):
		super(BotMonitorMiddleware, self).__init__()
		UsesRaw.set_handler(self, self.handle_event)
		self.start_process()

	def start_process(self):
		self.bot_process = subprocess.Popen('python3 ./NeonDJBot.py', shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

	def git_pull(self):
		out = subprocess.check_output('git pull', shell=True)

	def __del__(self):
		self.bot_process.kill()

	@asyncio.coroutine
	def handle_event(self, packet):
		if packet.type == 'send-event':
			for message in packet.messages():
				if message.data['content'].find('!restart') == 0:
					self.bot_process.kill()
					self.start_process()
				if message.data['content'].find('!update') == 0:
					self.bot_process.kill()
					self.git_pull()
					self.start_process()
				if message.data['content'].find('!stopbot') == 0:
					self.bot_process.kill()
					self.bot_process = None
				if message.data['content'].find('!startbot') == 0:
					if self.bot_process:
						self.bot_process.kill()
					self.start_process()


class MonitorBot(Bot):
	pass

bot = MonitorBot('wss://euphoria.io/room/music/ws')
bot.add_middleware(BotMonitorMiddleware())

bot.connect()