import socket
import subprocess
import sys

def raw_request(path):
    """Send HTTP/1.0 request (forces connection close)"""
    s = socket.socket()
    s.settimeout(5)
    try:
        s.connect(('127.0.0.1', 8081))
        req = 'GET ' + path + ' HTTP/1.0\r\nHost: localhost:8081\r\nConnection: close\r\n\r\n'
        s.sendall(req.encode())
        resp = b''
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            resp += chunk
        text = resp.decode('utf-8', errors='replace')
        lines = text.split('\r\n')
        status_line = lines[0] if lines else 'NO-RESPONSE'
        loc = ''
        content_type = ''
        for l in lines:
            ll = l.lower()
            if ll.startswith('location:'):
                loc = l.strip()
            if ll.startswith('content-type:'):
                content_type = l.strip().split(':',1)[1].strip()[:40]
        return status_line, loc, content_type
    except socket.timeout:
        return 'TIMEOUT', '', ''
    except Exception as e:
        return 'ERROR: ' + str(e)[:50], '', ''
    finally:
        try: s.close()
        except: pass


paths = [
    '/login', '/login.mvc', '/register.mvc', '/index.mvc',
    '/start.mvc', '/welcome.mvc', '/actuator/health',
    '/sqlinjection/introduction', '/SqlInjection/attack2',
    '/xxe/simple', '/access-control/users', '/jwt',
]

print("=== WebGoat Path Probe ===")
print("-" * 80)
for path in paths:
    status, loc, ct = raw_request(path)
    extra = loc if loc else ct
    print(path.ljust(40) + status.strip() + "  " + extra)

print("\n=== Registration + Login Flow ===")
# Try register + login via raw POST
import urllib.parse

s = socket.socket()
s.settimeout(10)
s.connect(('127.0.0.1', 8081))
body = urllib.parse.urlencode({
    'username': 'fuzzer',
    'password': 'fuzz1234',
    'matchingPassword': 'fuzz1234',
    'agree': 'agree'
})
req = ('POST /register.mvc HTTP/1.0\r\n'
       'Host: localhost:8081\r\n'
       'Content-Type: application/x-www-form-urlencoded\r\n'
       'Content-Length: ' + str(len(body)) + '\r\n'
       'Connection: close\r\n\r\n' + body)
s.sendall(req.encode())
resp = b''
while True:
    chunk = s.recv(4096)
    if not chunk: break
    resp += chunk
s.close()

text = resp.decode('utf-8', errors='replace')
lines = text.split('\r\n')
print("POST /register.mvc  " + (lines[0] if lines else 'NO-RESPONSE'))
for l in lines[1:6]:
    print("  " + l)
