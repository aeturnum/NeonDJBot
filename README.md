# NeonDJBot

This is an early bot, written primaraly for Euphoria. However, the architectur should support any protocol. It uses protocol agnostic 'middleware' to provide services to other parts of the bot.

## requirements
Python 3.4 - asyncio

websockets - https://pypi.python.org/pypi/websockets

tinydb - https://pypi.python.org/pypi/tinydb/2.3.1.post2

aiohttp - https://pypi.python.org/pypi/aiohttp/0.15.3

probably something else I forgot

## Bots
MonitorBot.py - starts and monitors NeonDBBot

NeonDJBot.py - queues and plays youtube songs based on user commands

ChessBot.py - uses sunfish (https://github.com/thomasahle/sunfish) to allow users to play chess.


## Tests
I have no tests at the moment :(

They are coming one of these days!

## License
With the exception of ChessBot.py, this code is released under the MIT license.

ChessBot.py, because it is a derivate work of sunfish, is licensed under the GPL.