from app import create_app
from security import validate_csrf
app = create_app()
with app.test_client() as c:
    with c.session_transaction() as sess:
        sess['custom_shirt'] = {'front':'x.jpg','back':'y.jpg','price_cents':59900}
    r = c.post('/customize/add', data={'csrf_token':'bad'}, follow_redirects=False)
    print(r.status_code)
