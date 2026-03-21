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

print('直接调用 API...')
response = requests.post(url, headers=headers, json=payload, stream=True, timeout=30)
print(f'Status: {response.status_code}')

full = ''
for line in response.iter_lines():
    line_str = line.decode('utf-8') if line else ''
    print(f'Raw line: {repr(line_str)}')
    
    if line_str.startswith('data: '):
        data_str = line_str[6:]
        print(f'  Data content: {repr(data_str)}')
        if data_str == '[DONE]':
            break
        try:
            data = json.loads(data_str)
            print(f'  Parsed: {data}')
            if data.get('type') == 'content_block_delta':
                delta = data.get('delta', {})
                if delta.get('type') == 'text_delta':
                    content = delta.get('text', '')
                    if content:
                        full += content
                        print(f'  -> Content: {repr(content)}')
        except Exception as e:
            print(f'  Parse error: {e}')

print(f'\nFull response: {repr(full)}')
