import re

path = r'c:\Users\Yanet Zota\agentic-workshop\a2a-trip-planner\TripPlanner\frontend\app.js'
with open(path, encoding='utf-8') as f:
    content = f.read()

replacements = [
    (
        'headers: { "Content-Type": "application/json", "X-User-Id": STATE.userId },',
        'headers: getAuthHeaders(),'
    ),
    (
        'headers: { "X-User-Id": STATE.userId },',
        'headers: getAuthHeaders(),'
    ),
    (
        '{ headers: { "X-User-Id": STATE.userId } }',
        '{ headers: getAuthHeaders() }'
    ),
]

for old, new in replacements:
    count = content.count(old)
    content = content.replace(old, new)
    print(f"Replaced {count}x: ...{old[:40]}...")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Done.")
