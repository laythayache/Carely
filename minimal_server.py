#!/usr/bin/env python3
"""
Minimal test server for button API testing.
Accepts POST /button requests and logs them.
"""
import asyncio
import json
from pathlib import Path

# Load .env config
config = {}
env_path = Path('.env')
if env_path.exists():
    for line in env_path.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith('#') and '=' in s:
            k, v = s.split('=', 1)
            config[k.strip()] = v.strip()

BUTTON_ENABLED = config.get('BUTTON_API_ENABLED', '').lower() == 'true'
BUTTON_TOKEN = config.get('BUTTON_API_BEARER_TOKEN', '')
UI_PORT = int(config.get('UI_PORT', '8080'))

print(f"[INFO] Minimal Carely Button Test Server")
print(f"[INFO] Button API: {'ENABLED' if BUTTON_ENABLED else 'DISABLED'}")
print(f"[INFO] Starting on http://0.0.0.0:{UI_PORT}")
print(f"[INFO] Bearer token: {BUTTON_TOKEN[:16]}...")
print()

# Try to import aiohttp, if not available use simple socket server
try:
    from aiohttp import web
    
    button_presses = []
    
    async def button_handler(request):
        """Handle POST /button requests"""
        auth_header = request.headers.get('Authorization', '')
        expected = f'Bearer {BUTTON_TOKEN}'
        
        if auth_header != expected:
            print(f"[WARN] Unauthorized button press (wrong token)")
            return web.Response(status=401, text='Unauthorized')
        
        try:
            payload = await request.json()
            print(f"[INFO] ✓ Button pressed! Payload: {payload}")
            button_presses.append(payload)
            return web.Response(status=200, text='OK')
        except:
            return web.Response(status=400, text='Bad request')
    
    async def ui_handler(request):
        """Serve simple HTML UI"""
        html = f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Carely Button Test</title>
            <style>
                body {{ font-family: Arial; padding: 20px; }}
                .status {{ 
                    padding: 20px; border-radius: 5px; margin: 10px 0;
                    background: #e8f5e9; border-left: 4px solid #4caf50;
                }}
                .press {{ 
                    padding: 10px; background: #fff3cd; margin: 5px 0;
                    border-radius: 3px; font-family: monospace;
                }}
                h1 {{ color: #333; }}
            </style>
        </head>
        <body>
            <h1>🔘 Carely Button Status</h1>
            <div class="status">
                <p><strong>Button API:</strong> <span style="color: green;">ACTIVE ✓</span></p>
                <p><strong>Server:</strong> http://0.0.0.0:{UI_PORT}</p>
                <p><strong>Endpoint:</strong> POST /button</p>
                <p><strong>Presses Received:</strong> <span id="count">{len(button_presses)}</span></p>
            </div>
            <h2>Button Press Log:</h2>
            <div id="log">
                {chr(10).join(f'<div class="press">Press #{i+1}: {p}</div>' for i, p in enumerate(button_presses[-10:]))}
            </div>
            <p><small>Auto-refresh every 2 seconds...</small></p>
            <script>
                setInterval(function() {{
                    location.reload();
                }}, 2000);
            </script>
        </body>
        </html>
        '''
        return web.Response(text=html, content_type='text/html')
    
    async def status_api(request):
        """API endpoint for button status"""
        return web.json_response({{
            'button_api_enabled': BUTTON_ENABLED,
            'presses_count': len(button_presses),
            'latest_press': button_presses[-1] if button_presses else None
        }})
    
    async def main():
        app = web.Application()
        app.router.add_post('/button', button_handler)
        app.router.add_get('/', ui_handler)
        app.router.add_get('/api/status', status_api)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', UI_PORT)
        await site.start()
        
        print(f"[OK] Server running on http://0.0.0.0:{UI_PORT}")
        print(f"[OK] Web UI: http://localhost:{UI_PORT}")
        print(f"[OK] Button endpoint: POST http://localhost:{UI_PORT}/button")
        print()
        print("Waiting for button press...")
        
        try:
            await asyncio.sleep(3600)  # Run for 1 hour
        except KeyboardInterrupt:
            print("[INFO] Shutting down...")
        finally:
            await runner.cleanup()
    
    asyncio.run(main())
    
except ImportError:
    print("[ERROR] aiohttp not available - using fallback socket server")
    import socket
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', UI_PORT))
    sock.listen(1)
    
    print(f"[OK] Fallback server listening on 0.0.0.0:{UI_PORT}")
    print(f"[OK] Waiting for button press...")
    
    try:
        while True:
            conn, addr = sock.accept()
            data = conn.recv(1024).decode('utf-8', errors='ignore')
            
            if 'POST /button' in data:
                if BUTTON_TOKEN in data:
                    print(f"[INFO] ✓ Button pressed from {addr[0]}")
                    conn.send(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK')
                else:
                    print(f"[WARN] Button press with wrong token from {addr[0]}")
                    conn.send(b'HTTP/1.1 401 Unauthorized\r\n\r\n')
            elif 'GET / ' in data:
                html = b'HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n<h1>Carely Button Server Online</h1>'
                conn.send(html)
            
            conn.close()
    except KeyboardInterrupt:
        print("[INFO] Shutting down...")
    finally:
        sock.close()

