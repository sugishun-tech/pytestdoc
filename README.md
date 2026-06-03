# pytestdoc

`pytestdoc` は pytest 用のユニットテストから Markdown ドキュメントを自動生成するツールです。

テストコードを実行せず、Python AST を解析して以下の情報を抽出します。

* テスト関数
* テストクラス
* docstring
* spec_id
* Given / When / Then
* assert 文

テストコードを仕様書として活用し、実装とドキュメントの乖離を減らすことを目的としています。

---

## 特徴

* pytest スタイルのテストを自動検出
* テストコードの import 不要
* テストコードの実行不要
* AST ベースで安全に解析
* Markdown を自動生成
* Given / When / Then 記法対応
* spec_id 対応
* ディレクトリ構造を維持して出力
* spec_id や Given/When/Then が無くても出力可能

---

## インストール

特別な依存ライブラリはありません。

Python 3.10 以上を推奨します。

```bash
git clone https://github.com/yourname/pytestdoc.git
cd pytestdoc
```

---

## 使い方

```bash
python3 pytestdoc.py ./backend/app/tests
```

出力先を指定する場合:

```bash
python3 pytestdoc.py ./backend/app/tests -o ./docs
```

---

## 入力例

```python
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
```


## ディレクトリ構造

入力:

```text
tests/
├── unit/
│   └── auth/
│       └── test_login.py
└── integration/
    └── test_user.py
```

出力:

```text
docs/
├── unit/
│   └── auth/
│       └── test_login.md
└── integration/
    └── test_user.md
```

入力ディレクトリ構造をそのまま維持します。

---

## テスト検出ルール

pytest の一般的な命名規則に従います。

### テストファイル

以下を対象とします。

```text
test_*.py
Test*.py
```

### テストクラス

```python
class TestLogin:
```

### テスト関数・メソッド

```python
def test_login_success():
```

```python
def TestLoginSuccess():
```

---

## spec_id

docstring 内で仕様IDを記述できます。

```python
"""
spec_id: AUTH-001
"""
```

```python
"""
spec_id = AUTH-001
"""
```

```python
"""
@spec_id AUTH-001
"""
```

複数指定も可能です。

```python
"""
spec_id: AUTH-001, AUTH-002
"""
```

---

## Given / When / Then

BDD スタイルの記述をサポートしています。

```python
"""
Given: ユーザーが存在する
When: ログインする
Then: ログイン成功となる
"""
```

複数行も可能です。

```python
"""
Given: ユーザーが存在する
Given: 有効なパスワードを持つ

When: ログインする

Then: HTTP 200
Then: Cookie発行
"""
```

---

## Assertions

Python の `assert` 文を抽出します。

```python
assert response.status_code == 200
```

```python
assert user is not None
```

```python
assert result["status"] == "ok"
```

生成された Markdown には assert 式と行番号が出力されます。

---

## 制限事項

現在は以下を対象外としています。

* pytest 実行結果
* fixture の依存関係解析
* parametrized テストの展開
* unittest の `assertEqual()` 等の解析
* pytest.ini / pyproject.toml の独自収集ルール

解析対象は Python ソースコードのみです。

---

## 目的

ユニットテストを単なる検証コードではなく、

* 実行可能仕様書
* 振る舞いドキュメント
* 要件トレーサビリティ

として活用することを目的としています。

コードを書き、テストを書き、さらに別途 Word や Excel に仕様を書くという古来の儀式を少しでも減らします。人類は同じ情報を三重管理しがちなので。
