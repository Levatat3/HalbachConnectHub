import tempfile
import unittest
from pathlib import Path

from connecthub import live, server
from connecthub import store as st
from connecthub.net import LoopbackServer


class TestExclusivePorts(unittest.TestCase):
    def test_second_server_cannot_share_a_port(self):
        with tempfile.TemporaryDirectory() as t:
            a, _ = server.serve(Path(t) / "a", 0)
            try:
                port = a.server_address[1]
                with self.assertRaises(OSError):
                    server.serve(Path(t) / "b", port)
            finally:
                a.server_close()

    def test_live_hub_port_is_exclusive_too(self):
        with tempfile.TemporaryDirectory() as t:
            store = st.Store(Path(t) / "h")
            a, _ = live.start_live_hub(store, 0)
            try:
                with self.assertRaises(OSError):
                    live.start_live_hub(store, a.server_address[1])
            finally:
                a.shutdown()
                a.server_close()

    def test_hub_falls_back_when_live_port_is_busy(self):
        with tempfile.TemporaryDirectory() as t:
            first = LoopbackServer(("127.0.0.1", 0), object)
            try:
                busy = first.server_address[1]
                srv, hub = server.serve(Path(t) / "x", 0, live_port=busy)
                try:
                    self.assertNotEqual(hub.live_srv.server_address[1], busy)
                finally:
                    hub.close()
                    srv.server_close()
            finally:
                first.server_close()


if __name__ == "__main__":
    unittest.main()
