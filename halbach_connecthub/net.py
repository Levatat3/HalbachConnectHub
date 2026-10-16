"""A loopback HTTP server that refuses to share its port.

On Windows, http.server's default SO_REUSEADDR lets a second process bind a port that is
already in use, so two hubs would split requests between them (and tokens would not match).
SO_EXCLUSIVEADDRUSE closes that; elsewhere, leaving SO_REUSEADDR off is enough.
"""

import socket
from http.server import ThreadingHTTPServer


class LoopbackServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
