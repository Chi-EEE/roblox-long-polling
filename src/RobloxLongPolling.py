from base64 import b64decode
import threading
import time
from flask import Flask, request, jsonify, current_app
import uuid
import json

class EventEmitter:
    def __init__(self):
        self._events = {}

    def on(self, event, listener):
        if event not in self._events:
            self._events[event] = []
        self._events[event].append(listener)

    def emit(self, event, *args, **kwargs):
        if event in self._events:
            for listener in self._events[event]:
                listener(*args, **kwargs)

    def remove_listener(self, event, listener):
        if event in self._events:
            self._events[event].remove(listener)

    def remove_all_listeners(self, event=None):
        if event:
            self._events[event] = []
        else:
            self._events.clear()

    def listeners(self, event):
        return self._events.get(event, [])

    def once(self, event, listener):
        def once_wrapper(*args, **kwargs):
            listener(*args, **kwargs)
            self.remove_listener(event, once_wrapper)
        self.on(event, once_wrapper)

class StoppableThread(threading.Thread):
    """Thread class with a stop() method. The thread itself has to check
    regularly for the stopped() condition."""

    def __init__(self,  *args, **kwargs):
        super(StoppableThread, self).__init__(*args, **kwargs)
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def stopped(self):
        return self._stop_event.is_set()

    def run(self):
        try:
            if self._target is not None:
                while not self.stopped():
                    self._target(*self._args, **self._kwargs)
        finally:
            # Avoid a refcycle if the thread is running a function with
            # an argument that has a member that points to the thread.
            del self._target, self._args, self._kwargs

class RLP:
    def __init__(self, settings):
        self.connections = {}
        self.port = settings.get('port', 2004)
        self.password = settings.get('password', '')
        self.stream = EventEmitter()

        self.poll_server = Flask(__name__)
        self.poll_server.add_url_rule('/connection', 'connection', self._connection, methods=['POST'])
        self.poll_server.add_url_rule('/poll/<id>', 'poll', self._poll, methods=['GET', 'POST'])
        self.poll_server.add_url_rule('/connection/<id>', 'disconnect', self._disconnect, methods=['DELETE'])


    def run(self):
        self.poll_server.run(port=self.port)
            
    def on(self, event, handler):
        return self.stream.on(event, handler)

    def broadcast(self, name, message):
        for connection_id, connection in self.connections.items():
            connection.send(name, message)

    def _connection(self):
        if self.password:
            if 'password' in request.json and request.json['password'] == self.password:
                connection_id = str(uuid.uuid4())
                connection = Connection(connection_id, lambda: self.connections.pop(connection_id))
                self.connections[connection_id] = connection
                self.stream.emit('connection', self.connections[connection_id])
                return jsonify({'success': True, 'socketId': connection_id}), 200
            else:
                return jsonify({'success': False, 'reason': 'Unauthorized'}), 401
        else:
            connection_id = str(uuid.uuid4())
            connection = Connection(connection_id, lambda: self.connections.pop(connection_id))
            self.connections[connection_id] = connection
            self.stream.emit('connection', self.connections[connection_id])
            return jsonify({'success': True, 'socketId': connection_id}), 200

    def _poll(self, id):
        if id in self.connections:
            if request.method == 'GET':
                return self.connections[id]._get()
            elif request.method == 'POST':
                return self.connections[id]._post()
        else:
            return jsonify({'success': False, 'reason': 'Not a valid connection'}), 400
     
    def _disconnect(self, id):
        if id in self.connections:
            self.connections[id]._disconnect()
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'reason': 'Not a valid connection'}), 400

class Connection:
    def __init__(self, connection_id, remove_instance):
        self.id = connection_id
        self.last_ping = None
        self.ready_to_receive = False
        self.stream = EventEmitter()
        self.sending = False
        self.send_queue = []
        self.remove_instance = remove_instance

        self.ping_interval = 2.5  # seconds
        self.ping_thread = StoppableThread(target=self._ping_loop)
        self.ping_thread.daemon = True
        self.ping_thread.start()

    def _ping_loop(self):
        if self.last_ping and self.last_ping <= time.time() - 30:
            self._disconnect()
            self.stream.emit('disconnect', 'no ping received')
        else:
            self.send('internal_ping')
        time.sleep(self.ping_interval)

    def on(self, event, callback):
        self.stream.on(event, callback)

    def send(self, name, message=None):
        self.send_queue.append({'name': name, 'message': message})

    def _get(self):
        self.last_ping = time.time()
        self.ready_to_receive = True
        self.sending = True

        def get_response():
            if self.send_queue:
                removing = self.send_queue.pop(0)
                self.last_ping = time.time()
                return jsonify({
                    'success': True,
                    'event': {
                        'name': removing.get('name'),
                        'data': json.dumps(removing.get('message'))
                    }
                })

        response = get_response()
        if response:
            return response

        def on_end():
            getting_interval.cancel()
            self.ready_to_receive = False
            self.sending = False
            self.send_queue = False

        getting_interval = threading.Timer(60, get_response)
        getting_interval.start()

        request.environ['werkzeug.server.shutdown'] = on_end
        return 'Response sent to client'

    def _post(self):
        self.last_ping = time.time()
        data = request.get_json()
        if 'name' in data and 'data' in data:
            name = b64decode(data['name']).decode('utf-8')
            data = b64decode(data['data']).decode('utf-8')
            self.stream.emit(name, data)
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'reason': 'missing parameters'}), 400

    def _disconnect(self):
        self.stream.emit('disconnect', 'called to disconnect')
        self.ping_thread.stop()

if __name__ == '__main__':
    rlp = RLP({'port': 2004, 'password': ''})
