import os
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

from .app import application


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
	daemon_threads = True


host = os.environ.get('SUPPORTBOT_HOST', '127.0.0.1')
port = int(os.environ.get('PORT', '8080'))
print(f'Support bot listening on http://{host}:{port}')
make_server(host, port, application, server_class=ThreadingWSGIServer).serve_forever()
