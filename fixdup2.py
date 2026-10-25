with open('storefront.py') as f:
    content = f.read()

# Find the second occurrence of customize route start
first = content.find('@bp.route("/customize"')
second = content.find('@bp.route("/customize"', first + 1)
if second == -1:
    print('only')
    raise SystemExit

# Find start of this block
block_start = content.rfind('\n\n', 0, second)
if block_start == -1:
    block_start = second
else:
    block_start += 2

# Find end of this duplicate block - look for next @bp.route after block_start
# Find @bp.route at start of line after block_start
idx = content.find('\n@bp.route', block_start + 100)
if idx == -1:
    # maybe no more routes, cut to end?
    idx = content.find('\n#', block_start + 100)
if idx == -1:
    idx = len(content)

# Cut from block_start to idx
new_content = content[:block_start] + '\n\n' + content[idx:].lstrip()
with open('storefront.py', 'w') as f:
    f.write(new_content)
print('done')
