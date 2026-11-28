#!/usr/bin/env node
/**
 * WebSocket 测试客户端
 * 
 * 用法：node edge/scripts/webui/test-websocket.mjs
 */

import WebSocket from 'ws';

const url = 'ws://127.0.0.1:8765/api/live';

console.log('Connecting to', url);

const ws = new WebSocket(url);

ws.on('open', () => {
    console.log('✅ WebSocket connected');
});

ws.on('message', (data) => {
    try {
        const msg = JSON.parse(data.toString());
        console.log(`\n📨 Received [${msg.type}]:`);
        
        if (msg.type === 'initial' || msg.type === 'update') {
            console.log(`   Match: ${msg.data.matchId}`);
            console.log(`   State: ${msg.data.matchState}`);
            console.log(`   Cameras: ${msg.data.cameras.length}`);
            console.log(`   Alerts: ${msg.data.alerts.length}`);
            console.log(`   Timestamp: ${msg.timestamp}`);
        } else if (msg.type === 'ping') {
            console.log(`   Timestamp: ${msg.timestamp}`);
            console.log('   → Sending pong...');
            ws.send(JSON.stringify({ type: 'pong' }));
        } else if (msg.type === 'error') {
            console.log(`   ❌ Error: ${msg.message}`);
        } else {
            console.log('   Data:', msg);
        }
    } catch (err) {
        console.error('Failed to parse message:', err);
        console.log('Raw data:', data.toString());
    }
});

ws.on('close', () => {
    console.log('\n🔌 WebSocket closed');
    process.exit(0);
});

ws.on('error', (err) => {
    console.error('❌ WebSocket error:', err.message);
    process.exit(1);
});

// 自动退出（60 秒后）
setTimeout(() => {
    console.log('\n⏰ Test timeout, closing...');
    ws.close();
}, 60000);

console.log('Listening for messages (Ctrl+C to exit)...');
