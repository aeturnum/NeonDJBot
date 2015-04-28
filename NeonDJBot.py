# -*- coding: utf-8 -*-
import asyncio

from bot import Bot
from middleware import SimpleActionMiddleware, PlayQueuedSongsMiddleware
from actions import SetNickAction


class NeonDJBot(Bot):
	@asyncio.coroutine
	def setup(self):
		yield from self.action_queue.put((self.TAG_DO_ACTION, SetNickAction("♬|NeonDJBot|Test")))

#bot = NeonDJBot('ws://localhost:8765/')
bot = NeonDJBot('wss://euphoria.io/room/music/ws')
#bot = NeonDJBot('wss://euphoria.io/room/test/ws')
bot.add_middleware(SimpleActionMiddleware())
bot.add_middleware(PlayQueuedSongsMiddleware())


bot.connect()