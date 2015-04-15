import asyncio
import websockets

@asyncio.coroutine
def loop(websocket, path):
	f = open('room_log.log' , 'r')
	line = f.readline()
	while line != '':
		yield from websocket.send(line.strip())
		yield from asyncio.sleep(.1)
		line = f.readline()

start_server = websockets.serve(loop, 'localhost', 8765)

asyncio.get_event_loop().run_until_complete(start_server)
asyncio.get_event_loop().run_forever()
