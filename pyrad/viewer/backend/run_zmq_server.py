# Copyright 2022 The Plenoptix Team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import, division, print_function

import base64
import json
import re
import sys
import numpy as np

if sys.version_info >= (3, 0):
    ADDRESS_IN_USE_ERROR = OSError
else:
    import socket

    ADDRESS_IN_USE_ERROR = socket.error

import tornado.gen
import tornado.ioloop
import tornado.web
import tornado.websocket
import umsgpack
import zmq
import zmq.eventloop.ioloop
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.rtcrtpsender import RTCRtpSender
from zmq.eventloop.zmqstream import ZMQStream

from pyrad.viewer.backend.tree import SceneTree, find_node, walk
from pyrad.viewer.backend.video_stream import FlagVideoStreamTrack, SingleFrameStreamTrack


def capture(pattern, s):
    match = re.match(pattern, s)
    if not match:
        raise ValueError("Could not match {:s} with pattern {:s}".format(s, pattern))
    else:
        return match.groups()[0]


def match_zmq_url(line):
    return capture(r"^zmq_url=(.*)$", line)


def _zmq_install_ioloop():
    # For pyzmq<17, install ioloop instead of a tornado ioloop
    # http://zeromq.github.com/pyzmq/eventloop.html
    try:
        pyzmq_major = int(zmq.__version__.split(".")[0])
    except ValueError:
        # Development version?
        return
    if pyzmq_major < 17:
        zmq.eventloop.ioloop.install()


_zmq_install_ioloop()


MAX_ATTEMPTS = 1000
DEFAULT_ZMQ_METHOD = "tcp"
DEFAULT_ZMQ_PORT = 6000
DEFAULT_WEBSOCKET_PORT = 8051
WEBSOCKET_COMMANDS = ["set_transform", "set_object", "get_object", "set_property", "delete"]
WEBRTC_COMMANDS = ["set_image"]


def find_available_port(func, default_port, max_attempts=MAX_ATTEMPTS, **kwargs):
    for i in range(max_attempts):
        port = default_port + i
        try:
            return func(port, **kwargs), port
        except (ADDRESS_IN_USE_ERROR, zmq.error.ZMQError):
            print("Port: {:d} in use, trying another...".format(port), file=sys.stderr)
        except Exception as e:
            print(type(e))
            raise
    else:
        raise (
            Exception(
                "Could not find an available port in the range: [{:d}, {:d})".format(
                    default_port, max_attempts + default_port
                )
            )
        )


def force_codec(pc, sender, forced_codec):
    kind = forced_codec.split("/")[0]
    codecs = RTCRtpSender.getCapabilities(kind).codecs
    transceiver = next(t for t in pc.getTransceivers() if t.sender == sender)
    transceiver.setCodecPreferences([codec for codec in codecs if codec.mimeType == forced_codec])


class WebSocketHandler(tornado.websocket.WebSocketHandler):
    def __init__(self, *args, **kwargs):
        self.bridge = kwargs.pop("bridge")
        super(WebSocketHandler, self).__init__(*args, **kwargs)

    # this disables CORS
    def check_origin(self, origin):
        return True

    def open(self):
        self.bridge.websocket_pool.add(self)
        print("opened:", self, file=sys.stderr)
        self.bridge.send_scene(self)

    async def on_message(self, message):

        data = message
        m = umsgpack.unpackb(message)
        type_ = m["type"]
        path = list(filter(lambda x: len(x) > 0, m["path"].split("/")))

        if type_ == "set_transform":
            find_node(self.bridge.tree, path).transform = data
        elif type_ == "set_object":
            find_node(self.bridge.tree, path).object = data
            find_node(self.bridge.tree, path).properties = []
        elif type_ == "offer":
            print("making an offer")
            print("sending an answer")
            # print(m)

            offer = RTCSessionDescription(m["data"]["sdp"], m["data"]["type"])

            pc = RTCPeerConnection()
            self.bridge.pcs.add(pc)  # TODO(ethan): handle this better, since this set will get large

            # video = FlagVideoStreamTrack()
            video = SingleFrameStreamTrack()
            self.bridge.video_tracks.add(video)
            video_sender = pc.addTrack(video)
            # force_codec(this.bridge.pc, video_sender, video_codec)

            await pc.setRemoteDescription(offer)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)

            cmd_data = {
                "type": "answer",
                "path": "",
                "data": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
            }
            data = umsgpack.packb(cmd_data)
            self.write_message(data, binary=True)

    def on_close(self):
        self.bridge.websocket_pool.remove(self)
        print("closed:", self, file=sys.stderr)


class ZMQWebSocketBridge(object):
    context = zmq.Context()

    def __init__(self, zmq_url=None, host="127.0.0.1", websocket_port=None):
        self.host = host
        self.websocket_pool = set()
        self.app = self.make_app()
        self.ioloop = tornado.ioloop.IOLoop.current()
        self.pcs = set()
        self.video_tracks = set()

        if zmq_url is None:

            def f(port):
                return self.setup_zmq("{:s}://{:s}:{:d}".format(DEFAULT_ZMQ_METHOD, self.host, port))

            (self.zmq_socket, self.zmq_stream, self.zmq_url), _ = find_available_port(f, DEFAULT_ZMQ_PORT)
        else:
            self.zmq_socket, self.zmq_stream, self.zmq_url = self.setup_zmq(zmq_url)

        listen_kwargs = {}

        if websocket_port is None:
            _, self.websocket_port = find_available_port(self.app.listen, DEFAULT_WEBSOCKET_PORT, **listen_kwargs)
        else:
            self.app.listen(websocket_port, **listen_kwargs)
            self.websocket_port = websocket_port

        self.tree = SceneTree()

    def __str__(self) -> str:
        class_name = self.__class__.__name__
        return f"{class_name} using zmq_url={self.zmq_url} and websocket_port={self.websocket_port}"

    def make_app(self):
        """Create a tornado application for the websocket server."""
        return tornado.web.Application([(r"/", WebSocketHandler, {"bridge": self})])

    def handle_zmq(self, frames):
        cmd = frames[0].decode("utf-8")
        print(cmd)
        if len(frames) != 3:
            self.zmq_socket.send(b"error: expected 3 frames")
            return
        path = list(filter(lambda x: len(x) > 0, frames[1].decode("utf-8").split("/")))
        data = frames[2]
        if cmd in WEBSOCKET_COMMANDS:
            self.forward_to_websockets(frames)
            if cmd == "set_transform":
                find_node(self.tree, path).transform = data
            elif cmd == "set_object":
                find_node(self.tree, path).object = data
                find_node(self.tree, path).properties = []
            elif cmd == "get_object":
                data = find_node(self.tree, path).object
                self.zmq_socket.send(data)
                return
            elif cmd == "set_property":
                find_node(self.tree, path).properties.append(data)
            elif cmd == "delete":
                if len(path) > 0:
                    parent = find_node(self.tree, path[:-1])
                    child = path[-1]
                    if child in parent:
                        del parent[child]
                else:
                    self.tree = SceneTree()
        elif cmd in WEBRTC_COMMANDS:
            if cmd == "set_image":
                for video_track in self.video_tracks:
                    unpacked_data = umsgpack.unpackb(data)
                    image = np.array(unpacked_data["image"], dtype="uint8").reshape(unpacked_data["shape"])
                    video_track.put_frame(image)
        else:
            self.zmq_socket.send(b"error: unknown command")
            return
        self.zmq_socket.send(b"ok")
        return

    def forward_to_websockets(self, frames):
        """Forward a zmq message to all websockets."""
        _, _, data = frames  # cmd, path, data
        for websocket in self.websocket_pool:
            websocket.write_message(data, binary=True)

    def setup_zmq(self, url):
        """Setup a zmq socket and connect it to the given url."""
        zmq_socket = self.context.socket(zmq.REP)
        zmq_socket.bind(url)
        zmq_stream = ZMQStream(zmq_socket)
        zmq_stream.on_recv(self.handle_zmq)
        return zmq_socket, zmq_stream, url

    def send_scene(self, websocket):
        for node in walk(self.tree):
            if node.object is not None:
                websocket.write_message(node.object, binary=True)
            for p in node.properties:
                websocket.write_message(p, binary=True)
            if node.transform is not None:
                websocket.write_message(node.transform, binary=True)

    def run(self):
        self.ioloop.start()


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Listen for ZeroMQ commands")
    parser.add_argument("--zmq-url", "-z", type=str, nargs="?", default=None)
    parser.add_argument("--websocket-port", "-wp", type=str, nargs="?", default=None)
    args = parser.parse_args()
    bridge = ZMQWebSocketBridge(zmq_url=args.zmq_url, websocket_port=args.websocket_port)
    print(bridge)
    try:
        bridge.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
