import re
with open('storefront.py') as f:
    content = f.read()

first = content.find('@bp.get("/customize/cart")')
if first == -1:
    print('none')
    raise SystemExit
second = content.find('@bp.get("/customize/cart")', first + 1)
if second == -1:
    print('only one')
    raise SystemExit

block_start = content.rfind('\n\n', 0, second)
if block_start == -1:
    block_start = second
else:
    block_start += 2

end_marker = 'return redirect(url_for("storefront.cart_page"))'
end = content.find(end_marker, second)
if end == -1:
    print('no end')
    raise SystemExit
end = end + len(end_marker)

new_content = content[:block_start] + content[end:]
with open('storefront.py', 'w') as f:
    f.write(new_content)
print('done')
