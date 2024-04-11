import RobloxLongPolling

poll = RobloxLongPolling.RLP({'port': 2004})

def handle_connection(connection: RobloxLongPolling.Connection):
    print('New connection:', connection.id)
    poll.broadcast("new connection", connection.id)
    connection.send('welcome', 'hello there!')

    def hello_handler(data):
        print("Received hello message:", data)
    
    connection.on('hello', hello_handler)

    def ping_handler(_args):
        print("Keep-Alive Ping received")

    connection.on('internal_ping', ping_handler)

    def disconnect_handler(_args):
        poll.broadcast("disconnection", connection.id)
    
    connection.on('disconnect', disconnect_handler)

poll.on('connection', handle_connection)

poll.run()