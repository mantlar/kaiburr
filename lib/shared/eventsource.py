import threading
import queue
import socket
import json
import logging

Log = logging.getLogger(__name__)

class IEventSource:
    """Base class for all event sources (Log or UDP Emitter)"""
    def __init__(self):
        self._queueLock = threading.Lock()
        self._messageQueueSwap = queue.Queue()
        self._workingMessageQueue = queue.Queue()
        self._isOpened = False

    def Open(self) -> bool:
        self._isOpened = True
        return True

    def Close(self):
        self._isOpened = False

    def IsOpened(self) -> bool:
        return self._isOpened

    def GetMessages(self) -> queue.Queue:
        with self._queueLock:
            tmp = self._workingMessageQueue
            self._workingMessageQueue = self._messageQueueSwap
            self._workingMessageQueue.queue.clear()
            self._messageQueueSwap = tmp
            return self._messageQueueSwap


class EmitterSource(IEventSource):
    """Event source that receives JSON structured events over UDP from the Kaiburr engine emitter."""
    def __init__(self, port: int):
        super().__init__()
        self._port = port
        self._sock = None
        self._thread = None
        self._stopEvent = threading.Event()

    def Open(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.bind(('127.0.0.1', self._port))
            self._sock.settimeout(1.0)
        except Exception as e:
            Log.error(f"EmitterSource: Failed to bind to UDP port {self._port}: {e}")
            return False

        super().Open()
        self._stopEvent.clear()
        self._thread = threading.Thread(target=self._ListenThread, daemon=True)
        self._thread.start()
        Log.info(f"EmitterSource listening on UDP port {self._port}")
        return True

    def Close(self):
        super().Close()
        self._stopEvent.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._sock:
            self._sock.close()
            self._sock = None

    def _ListenThread(self):
        from lib.shared.logMessage import LogMessage
        while not self._stopEvent.is_set():
            try:
                data, _ = self._sock.recvfrom(65535)
                # It can be multiple newline-separated JSON objects
                lines = data.decode('utf-8', errors='replace').split('\n')
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        msg = LogMessage()
                        msg.source = "emitter"
                        msg.structured = obj
                        # Map log lines
                        if obj.get('e') == 'log':
                            raw_log = obj.get('l', '')
                            # Strip the 7-character timestamp ("%3i:%02i ") to match the legacy log reader behavior
                            msg.content = raw_log[7:] if len(raw_log) >= 7 else raw_log
                        else:
                            msg.content = line
                        
                        with self._queueLock:
                            self._workingMessageQueue.put(msg)
                    except json.JSONDecodeError:
                        Log.warning(f"EmitterSource: Failed to decode JSON: {line}")
            except socket.timeout:
                continue
            except Exception as e:
                if not self._stopEvent.is_set():
                    Log.error(f"EmitterSource Error: {e}")

