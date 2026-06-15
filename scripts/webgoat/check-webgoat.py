"""Quick WebGoat connectivity check"""
import sys
try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

BASE = "http://localhost:8081"
paths = [
    "/", "/login", "/WebGoat", "/WebGoat/", "/login.mvc",
    "/register.mvc", "/index.mvc", "/actuator", "/actuator/health",
    "/swagger-ui.html", "/v3/api-docs", "/v2/api-docs",
    "/SqlInjection/attack2", "/CSRF/attack1",
]

for path in paths:
    try:
        r = requests.get(BASE + path, allow_redirects=False, timeout=3)
        loc = r.headers.get("Location", "")
        ct = r.headers.get("Content-Type", "")[:40]
        print(f"{r.status_code:3} {path:<40} {loc:<30} {ct}")
    except requests.exceptions.ConnectionError:
        print(f"CXN {path:<40} connection refused")
    except Exception as e:
        print(f"ERR {path:<40} {str(e)[:60]}")

# Also try registering a session
print("\n--- Session Test ---")
try:
    s = requests.Session()
    r = s.get(BASE + "/register.mvc", timeout=5)
    print(f"GET /register.mvc: {r.status_code} len={len(r.text)}")
    
    # Try POST register
    r2 = s.post(BASE + "/register.mvc", data={
        "username": "fuzztest", "password": "fuzztest123",
        "matchingPassword": "fuzztest123", "agree": "agree"
    }, allow_redirects=False, timeout=5)
    print(f"POST /register.mvc: {r2.status_code} Location={r2.headers.get('Location','')[:60]}")
    
    # Try login
    r3 = s.post(BASE + "/login", data={
        "username": "fuzztest", "password": "fuzztest123"
    }, allow_redirects=False, timeout=5)
    print(f"POST /login: {r3.status_code} Location={r3.headers.get('Location','')[:60]}")
    
    # Check cookies
    print(f"Cookies: {dict(s.cookies)}")
except Exception as e:
    print(f"Session test error: {e}")
