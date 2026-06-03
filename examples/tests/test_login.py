def test_login_success(client):
    """
    spec_id: AUTH-001

    ユーザーは正常にログインできる

    Given: 登録済みユーザーが存在する
    When: 正しい認証情報でログインする
    Then: HTTP 200 が返る
    Then: セッションCookieが発行される
    """

    response = client.post("/login")

    assert response.status_code == 200
    assert "session" in response.cookies
