import asyncio
import subprocess
import sys

from bot import Bot
from middleware import BotMiddleware, UsesLogger, UsesRaw

room_name = None
file_name = None

if __name__ == "__main__":
	if len(sys.argv) != 2 or sys.argv[1] not in ['chess', 'dj']:
		print("Usage: python3 ./MonitorBot.py <chess | dj> ")
		sys.exit(1)
	if sys.argv[1] == 'chess':
		room_name = 'wss://euphoria.io/room/chess/ws'
		file_name = 'ChessBot.py'
	elif sys.argv[1] == 'dj':
		room_name = 'wss://euphoria.io/room/music/ws'
		file_name = 'NeonDJBot.py'


class BotMonitorMiddleware(BotMiddleware, UsesLogger, UsesRaw):
	TAG = 'tag_bot_monitor'
	TYPE = BotMiddleware.OUTPUT

	LOG_NAME = 'Monitor'

	def __init__(self):
		super(BotMonitorMiddleware, self).__init__()
		UsesRaw.set_handler(self, self.handle_event)
		self.start_process()

	def start_process(self):
		global file_name
		self.bot_process = subprocess.Popen('python3 ./' + file_name, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

	def git_pull(self):
		out = subprocess.check_output('git pull', shell=True)

	def kill(self):
		if self.bot_process:
			self.bot_process.kill()
			self.bot_process = None

	def __del__(self):
		self.bot_process.kill()

	@asyncio.coroutine
	def handle_event(self, packet):
		if packet.type == 'send-event':
			for message in packet.messages():
				if message.data['content'].find('!restart') == 0:
					self.kill()
					self.start_process()
				if message.data['content'].find('!update') == 0:
					self.kill()
					self.git_pull()
					self.start_process()
				if message.data['content'].find('!stopbot') == 0:
					self.kill()
					self.bot_process = None
				if message.data['content'].find('!startbot') == 0:
					self.kill()
					self.start_process()


class MonitorBot(Bot):
	pass



bot = MonitorBot(room_name)
bot.add_middleware(BotMonitorMiddleware())

bot.connect()