import json, sys, os, glob
sys.stdout.reconfigure(encoding='utf-8')

# Check a few transcript files to see why no user messages
files = sorted(glob.glob(r'C:\Users\Administrator\.claude\transcripts\*.jsonl'))

# Check last 10 files (most recent, likely to have user msgs)
for fpath in files[-5:]:
    fname = os.path.basename(fpath)
    roles = {}
    sample_content = ''
    with open(fpath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                role = obj.get('role', obj.get('type', 'unknown'))
                roles[role] = roles.get(role, 0) + 1
                if role == 'user' and not sample_content:
                    content = obj.get('content', obj.get('message', ''))
                    if isinstance(content, list):
                        for p in content:
                            if isinstance(p, dict) and p.get('type') == 'text':
                                sample_content = p.get('text', '')[:100]
                                break
                    elif isinstance(content, str):
                        sample_content = content[:100]
            except:
                continue
    print(f'{fname}: roles={roles}')
    if sample_content:
        print(f'  sample user: {sample_content}')
    print()
