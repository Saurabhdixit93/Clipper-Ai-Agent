import socket
import time

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(10.0)
s.connect(("irc.chat.twitch.tv", 6667))

s.send(b"PASS SCHMOOPIIE\r\n")
s.send(b"NICK justinfan12345\r\n")

buffer = ""
registered = False
while not registered:
    data = s.recv(4096).decode("utf-8", errors="ignore")
    print("HANDSHAKE RECV:", repr(data))
    buffer += data
    lines = buffer.split("\r\n")
    buffer = lines.pop()
    for line in lines:
        if " 001 " in line or " 376 " in line:
            registered = True
            break

print("Sending JOIN #gaules...")
s.send(b"JOIN #gaules\r\n")

for i in range(15):
    try:
        data = s.recv(4096).decode("utf-8", errors="ignore")
        print(f"[{i}] DATA:", repr(data))
    except socket.timeout:
        print(f"[{i}] TIMEOUT")
    time.sleep(0.5)

s.close()
