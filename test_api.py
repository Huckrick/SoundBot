#!/usr/bin/env python3
import requests
import json

url = 'https://api.kimi.com/coding/v1/messages'
headers = {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer sk-kimi-PwWlgPKaHR1laR1SLhdqdzmDMCQrOve7K6HEKsoEfn2iU81ZclNatTqAyDj1Q2DP',
    'User-Agent': 'Kimi Claw Plugin'
}
payload = {
    'model': 'k2p5',
    'messages': [{'role': 'user', 'content': 'hi'}],
    'temperature': 0.7,
    'max_tokens': 50,
    'stream': True
}

print(f'URL: {url}')
print(f'Payload: {json.dumps(payload, indent=2)}')
print('\n发送请求...')

try:
    response = requests.post(url, headers=headers, json=payload, stream=True, timeout=30)
    print(f'Status: {response.status_code}')
    print(f'Response headers: {dict(response.headers)}')
    print('\n响应内容:')
    for line in response.iter_lines():
        if line:
            print(line.decode('utf-8'))
except Exception as e:
    print(f'Error: {e}')
