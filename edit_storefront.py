with open('storefront.py') as f:
    content = f.read()

# Change customize route to redirect to preview
old = '''        session[\'custom_shirt\'] = {
            \'front\': fname_front,
            \'back\': fname_back,
            \'price_cents\': 59900
        }
        flash("Photos uploaded! Add to cart.", "success")
        return redirect(url_for("storefront.customize_cart"))'''

new = '''        session[\'custom_shirt\'] = {
            \'front\': fname_front,
            \'back\': fname_back,
            \'price_cents\': 59900
        }
        flash("Photos uploaded! Preview and adjust your design.", "success")
        return redirect(url_for("storefront.customize_preview"))'''

if old not in content:
    print('not found')
else:
    content = content.replace(old, new)
    with open('storefront.py', 'w') as f:
        f.write(content)
    print('done')
