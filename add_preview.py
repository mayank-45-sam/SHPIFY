with open('storefront.py', 'a') as f:
    f.write('''


@bp.get("/customize/preview")
def customize_preview():
    from flask import session
    from catalog import format_rupees
    custom = session.get('custom_shirt')
    if not custom:
        flash("Please upload your photos first.", "error")
        return redirect(url_for("storefront.customize"))
    return render_template("customize_preview.html", custom=custom, price_display=format_rupees(custom['price_cents']))
''')
print('done')
