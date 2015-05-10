# -*- coding: utf-8 -*-
import asyncio

from bot import Bot
from command import Command
from core import BotMiddleware, UsesLogger, Message
from middleware import PacketMiddleware, SimpleActionMiddleware, UsesCommands, SendsActions
from actions import SetNickAction, ReplyAction

# sunfish is sunfish.py
# hosted at https://github.com/thomasahle/sunfish
# I'm not sure what the right way of including sunfish in my repo would be,
# so you'll have to clone it yourself and put sunfish.py in chessbot's directory.
# This code is considered a derivative work of sunfish and thus is subject to the GPL.

from sunfish import Position, initial, parse, search, MATE_VALUE, render


class PrintBoardAction(ReplyAction):
	def __init__(self, board, your_move, bot_move, reply_to):
		self.board = board
		self.your_move = your_move
		self.bot_move = bot_move
		super(PrintBoardAction, self).__init__(self.print_board(), reply_to)

	def add_moves(self, board):
		if self.bot_move:
			board += '\nBot move: ' + self.bot_move

		return board

	def print_board(self):
		# black lower case
		# white upper case
		piece_map = {
			'r':'♜', 'n':'♞', 'b':'♝', 'q':'♛', 'k': '♚', 'p': '♟',
			'R':'♖', 'N':'♘', 'B':'♗', 'Q':'♕', 'K': '♔', 'P': '♙',
		}

		# replace pieces
		board = self.board
		for letter, piece in piece_map.items():
			board = board.replace(letter, piece)

		board_lines = [line.strip() for line in board.split('\n')]

		row = 8
		numbered_lines = []
		for line in board_lines:
			if len(line):
				numbered_lines.append('{}|{}'.format(row, line.replace('.', '　'))) 
				row -= 1

		legend = ['A','B','C','D','E','F','G','H']
		numbered_lines.append('　{}'.format('\u2008'.join(legend)) )

		board = '\n'.join(numbered_lines)
		return self.add_moves(board)


class MoveCommand(Command):
	DB_TAG = 'database_command_chessmove'
	COMMAND = '!move'
	COMMAND_LOCATION = 'start'

	def __init__(self, message_or_db):
		super(MoveCommand, self).__init__(message_or_db)
		if isinstance(message_or_db, Message):
			self.move_str = message_or_db.data['content'].replace(self.COMMAND, '')
			# strip whitespace
			self.move_str = self.move_str.strip()
			self.prepared = False
		else:
			self.move_str = message_or_db['move_str']
			self.move = message_or_db['move']
			self.prepared = True

	def to_db_dict(self):
		base = super(MoveCommand, self).to_db_dict()
		base['move'] = self.move
		base['move_str'] = self.move_str
		return base

	def get_actions(self):
		if not self.prepared:
			return [ReplyAction("Sorry, the move string '{}' does not appear valid.".format(self.move_str), self.parent_id)]
		else:
			return []

	@asyncio.coroutine
	def prepare(self):
		try:
			move_str = self.move_str.lower()
			self.move = parse(move_str[0:2]), parse(move_str[2:4])
			self.prepared = True
		except ValueError:
			# Inform the user when invalid input (e.g. "help") is entered
			pass

	def is_prepared(self):
		return self.prepared

	@classmethod
	def help_string(cls):
		return "{} chess move : Move the specified piece.".format(cls.COMMAND)

class NewGameCommand(Command):
	DB_TAG = 'database_command_newgame'
	COMMAND = '!newgame'
	COMMAND_LOCATION = 'start'

	@classmethod
	def help_string(cls):
		return "{} : Start a new game.".format(cls.COMMAND)

class ChessGameMiddleware(BotMiddleware, UsesCommands, UsesLogger, SendsActions):
	TAG = 'tag_chess_game'
	TYPE = BotMiddleware.OUTPUT

	LOG_NAME = 'Chess'
	MIDDLEWARE_SUPPORT_REQUESTS = {
		PacketMiddleware.TAG: [
			NewGameCommand, MoveCommand
		]
	}

	def __init__(self):
		super(ChessGameMiddleware, self).__init__()
		UsesCommands.set_handler(self, self.handle_event)
		self.new_game()

	def new_game(self):
		self.game = Position(initial, 0, (True,True), (True,True), 0, 0)

	@asyncio.coroutine
	def handle_event(self, command):
		if not command.is_prepared() or not self.backlog_processed:
			return

		self.verbose('Got Command: {}', command.DB_TAG)

		action = None

		if MoveCommand.DB_TAG in command.DB_TAG:
			move = command.move
			move_str = command.move_str
			if move in self.game.genMoves():
				self.game = self.game.move(move)
				
				self.game.rotate()
				move, score = search(self.game)
				if score <= -MATE_VALUE:
					action = ReplyAction('You Win!', self.reply_to)
				elif score >= MATE_VALUE:
					action = ReplyAction('You Lost :(', self.reply_to)
				else:
					bot_move = render(119-move[0]) + render(119-move[1])
					self.game = self.game.move(move)

					action = PrintBoardAction(self.game.board, move_str, bot_move, self.reply_to)
			else:
				action = ReplyAction('That move is invalid.', command.uid)
			

		elif NewGameCommand.DB_TAG in command.DB_TAG:
			self.new_game()
			self.reply_to = command.uid
			action = PrintBoardAction(self.game.board, None, None, self.reply_to)

		if action:
			yield from self.send_action(action)

class ChessBot(Bot):

	def __init__(self, room_address):
		super(ChessBot, self).__init__(room_address, './ChessBotDB.json')

	@asyncio.coroutine
	def setup(self):
		yield from self.action_queue.put((self.TAG_DO_ACTION, SetNickAction("♚|ChessBot|♚")))

bot = ChessBot('wss://euphoria.io/room/chess/ws')
bot.add_middleware(SimpleActionMiddleware())
bot.add_middleware(ChessGameMiddleware())


bot.connect()