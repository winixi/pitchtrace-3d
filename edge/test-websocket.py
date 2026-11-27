#!/usr/bin/env python3
"""WebSocket 测试客户端"""

import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("Error: websockets module not found")
    print("Install with: pip3 install websockets")
    sys.exit(1)

async def test_websocket():
    url = "ws://127.0.0.1:8765/api/live"
    print(f"Connecting to {url}...")
    
    try:
        async with websockets.connect(url) as ws:
            print("✅ WebSocket connected\n")
            
            # 接收消息（最多 15 秒）
            message_count = 0
            timeout_seconds = 15
            
            try:
                while message_count < 10:  # 最多接收 10 条消息
                    msg_text = await asyncio.wait_for(ws.recv(), timeout=timeout_seconds)
                    message_count += 1
                    
                    try:
                        msg = json.loads(msg_text)
                        msg_type = msg.get('type', 'unknown')
                        
                        print(f"📨 Message #{message_count} [{msg_type}]:")
                        
                        if msg_type in ['initial', 'update']:
                            data = msg.get('data', {})
                            print(f"   Match: {data.get('matchId')}")
                            print(f"   State: {data.get('matchState')}")
                            print(f"   Cameras: {len(data.get('cameras', []))}")
                            print(f"   Alerts: {len(data.get('alerts', []))}")
                            print(f"   Timestamp: {msg.get('timestamp')}")
                        elif msg_type == 'ping':
                            print(f"   Timestamp: {msg.get('timestamp')}")
                            print("   → Sending pong...")
                            await ws.send(json.dumps({"type": "pong"}))
                        elif msg_type == 'error':
                            print(f"   ❌ Error: {msg.get('message')}")
                        else:
                            print(f"   Data: {msg}")
                        
                        print()
                        
                    except json.JSONDecodeError:
                        print(f"   Raw: {msg_text}\n")
                        
            except asyncio.TimeoutError:
                print(f"⏰ No messages for {timeout_seconds}s, test complete")
            
            print(f"\n✅ Test finished ({message_count} messages received)")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(test_websocket())
    sys.exit(exit_code)
