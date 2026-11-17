"""Twitch IRC Chat Monitor for real-time chat rate tracking and rolling baseline calculation."""

import time
import socket
import random
import logging
import threading
from typing import List, Dict, Optional, Tuple, Deque
from collections import deque
from dataclasses import dataclass

from live_clipper.config import ClipperConfig

logger = logging.getLogger("live_clipper.chat_monitor")


@dataclass
class ChatMessage:
    timestamp: float
    username: str
    message: str


class TwitchChatMonitor:
    """
    Connects anonymously to Twitch IRC, tracks message rates in 5-second buckets,
    maintains a 5-minute rolling average, and enforces a 5-minute warmup period.
    """
    def __init__(self, config: ClipperConfig):
        self.config = config
        self.channel = f"#{config.channel.lower().lstrip('#')}"
        self.nick = f"justinfan{random.randint(10000, 99999)}"
        
        self.is_connected = False
        self.connection_start_time: Optional[float] = None
        self.last_message_time = 0.0
        
        # Message counting: bucket_id (int: timestamp // bucket_sec) -> count
        self._lock = threading.RLock()
        self.bucket_sec = config.chat_bucket_sec # 5 seconds
        self.rolling_window_sec = config.chat_rolling_window_sec # 300 seconds (5 min)
        self.buckets: Dict[int, int] = {} # bucket_id -> count
        
        # Recent messages deque for title/context generation
        self.recent_messages: Deque[ChatMessage] = deque(maxlen=200)
        
        # Lifecycle
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._socket: Optional[socket.socket] = None

    def start(self):
        """Start chat monitor in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="TwitchChatMonitorThread", daemon=True)
        self._thread.start()
        logger.info(f"Chat monitor started for {self.channel} with nick {self.nick}")

    def stop(self):
        """Stop chat monitor and close socket."""
        self._stop_event.set()
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
                self._socket.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        self.is_connected = False
        logger.info("Chat monitor stopped")

    @property
    def warmup_elapsed_sec(self) -> float:
        """Seconds of continuous connected chat data collected."""
        if not self.is_connected or self.connection_start_time is None:
            return 0.0
        return max(0.0, time.time() - self.connection_start_time)

    @property
    def is_warmed_up(self) -> bool:
        """True if 5 complete minutes of valid chat data have been gathered."""
        return self.warmup_elapsed_sec >= self.config.warmup_duration_sec

    @property
    def warmup_progress_pct(self) -> float:
        """Warmup completion percentage (0.0 to 100.0)."""
        return min(100.0, (self.warmup_elapsed_sec / float(self.config.warmup_duration_sec)) * 100.0)

    def get_current_chat_rate(self, timestamp: Optional[float] = None) -> float:
        """
        Returns the chat rate (message count in the 5-second bucket for timestamp or current time).
        """
        now = timestamp if timestamp is not None else time.time()
        current_bucket = int(now // self.bucket_sec)
        with self._lock:
            return float(self.buckets.get(current_bucket, 0))

    def get_rolling_chat_average(self, exclude_current_spike: bool = True, timestamp: Optional[float] = None) -> float:
        """
        Computes the 5-minute rolling average of chat activity (messages per 5s bucket).
        Excludes the current bucket to prevent current spike from inflating its own baseline.
        """
        now = timestamp if timestamp is not None else time.time()
        current_bucket = int(now // self.bucket_sec)
        window_buckets = int(self.rolling_window_sec // self.bucket_sec) # 60 buckets for 300s
        
        with self._lock:
            # Baseline uses buckets prior to the current active bucket
            start_bucket = current_bucket - window_buckets if exclude_current_spike else current_bucket - window_buckets + 1
            end_bucket = current_bucket - 1 if exclude_current_spike else current_bucket
            
            total_msgs = 0
            counted_buckets = 0
            
            for b_idx in range(start_bucket, end_bucket + 1):
                cnt = self.buckets.get(b_idx, 0)
                total_msgs += cnt
                counted_buckets += 1
                
            if counted_buckets == 0:
                return 1.0 # Minimum default baseline to avoid division by zero
                
            # Average messages per 5s bucket
            avg = total_msgs / counted_buckets
            return max(avg, 0.5) # ensure positive baseline

    def get_recent_messages(self, count: int = 20, around_timestamp: Optional[float] = None) -> List[ChatMessage]:
        """Retrieve recent chat messages, optionally filtered around a specific timestamp."""
        with self._lock:
            msgs = list(self.recent_messages)
            
        if around_timestamp is not None:
            # Filter within 30 seconds of timestamp
            filtered = [m for m in msgs if abs(m.timestamp - around_timestamp) <= 30.0]
            if filtered:
                return filtered[-count:]
        return msgs[-count:]

    def _cleanup_old_buckets(self, current_bucket: int):
        """Prune buckets older than 10 minutes to prevent memory leak."""
        retention_buckets = int(600 // self.bucket_sec)
        cutoff = current_bucket - retention_buckets
        to_remove = [b for b in self.buckets.keys() if b < cutoff]
        for b in to_remove:
            del self.buckets[b]

    def _record_message(self, username: str, text: str, timestamp: float):
        """Record a parsed chat message."""
        bucket_id = int(timestamp // self.bucket_sec)
        with self._lock:
            self.buckets[bucket_id] = self.buckets.get(bucket_id, 0) + 1
            self.recent_messages.append(ChatMessage(timestamp=timestamp, username=username, message=text))
            self.last_message_time = timestamp
            self._cleanup_old_buckets(bucket_id)

    def _parse_line(self, line: str) -> Optional[Tuple[str, str]]:
        """
        Parse IRC raw line into (username, message).
        Twitch format: :username!username@username.tmi.twitch.tv PRIVMSG #channel :message
        """
        if not line or "PRIVMSG" not in line:
            return None
        try:
            parts = line.split("PRIVMSG", 1)
            prefix = parts[0]
            msg_part = parts[1]
            
            # Extract username
            username = "unknown"
            if prefix.startswith(":"):
                user_host = prefix[1:].split("!", 1)[0]
                username = user_host.strip()
                
            # Extract message text
            if ":" in msg_part:
                message = msg_part.split(":", 1)[1].strip()
            else:
                message = msg_part.strip()
                
            return username, message
        except Exception:
            return None

    def _run_loop(self):
        """IRC connection and reconnection loop."""
        backoff_sec = 3
        while not self._stop_event.is_set():
            try:
                self._connect_and_listen()
            except Exception as e:
                logger.warning(f"Twitch IRC connection dropped: {e}")
            finally:
                self.is_connected = False
                
            if not self._stop_event.is_set():
                logger.info(f"Reconnecting to Twitch IRC in {backoff_sec} seconds...")
                time.sleep(backoff_sec)
                backoff_sec = min(backoff_sec * 2, 30)

    def _connect_and_listen(self):
        """Manage single IRC socket session."""
        logger.info(f"Connecting to {self.config.irc_host}:{self.config.irc_port}...")
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._socket.settimeout(60.0)
        self._socket.connect((self.config.irc_host, self.config.irc_port))

        # Send Twitch IRC handshake
        self._socket.send(f"PASS SCHMOOPIIE\r\n".encode("utf-8"))
        self._socket.send(f"NICK {self.nick}\r\n".encode("utf-8"))

        # Wait for Twitch IRC registration (001 or 376 MOTD end) before sending JOIN
        buffer = ""
        registered = False
        start_wait = time.time()
        while not registered and not self._stop_event.is_set():
            if time.time() - start_wait > 30:
                logger.warning("Twitch IRC registration timeout, proceeding to JOIN anyway")
                break
            try:
                data = self._socket.recv(4096).decode("utf-8", errors="ignore")
            except socket.timeout:
                continue
            if not data:
                raise ConnectionError("Twitch IRC closed connection during registration handshake")
            buffer += data
            lines = buffer.split("\r\n")
            buffer = lines.pop()

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("PING"):
                    pong_resp = line.replace("PING", "PONG") + "\r\n"
                    self._socket.send(pong_resp.encode("utf-8"))
                if " 001 " in line or " 376 " in line or " 422 " in line:
                    registered = True
                    break

        # Now send JOIN
        self._socket.send(f"JOIN {self.channel}\r\n".encode("utf-8"))

        self.is_connected = True
        if self.connection_start_time is None:
            self.connection_start_time = time.time()
        logger.info(f"Successfully joined {self.channel} on Twitch IRC")

        while not self._stop_event.is_set():
            try:
                data = self._socket.recv(4096).decode("utf-8", errors="ignore")
            except socket.timeout:
                # Socket timed out with no incoming messages; send keepalive PING
                try:
                    self._socket.send(b"PING :tmi.twitch.tv\r\n")
                    continue
                except Exception as e:
                    logger.warning(f"Failed to send keepalive PING: {e}")
                    break

            if not data:
                logger.warning("Empty data received from Twitch IRC (socket closed)")
                break

            buffer += data
            lines = buffer.split("\r\n")
            buffer = lines.pop() # Keep incomplete line in buffer

            now = time.time()
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Handle PING/PONG
                if line.startswith("PING"):
                    pong_resp = line.replace("PING", "PONG") + "\r\n"
                    self._socket.send(pong_resp.encode("utf-8"))
                    logger.debug("IRC PING answered with PONG")
                    continue
                if line.startswith("PONG"):
                    logger.debug("IRC PONG received")
                    continue

                # Parse chat message
                parsed = self._parse_line(line)
                if parsed:
                    user, text = parsed
                    self._record_message(user, text, now)

