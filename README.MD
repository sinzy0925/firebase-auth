# README_firebase.md

```markdown
# AI Web Agent - バックエンドシステム

## 1. プロジェクト概要

本プロジェクトは、自律型AIリサーチアシスタント「AI Web Agent」のバックエンドシステムです。Firebaseプラットフォームを全面的に活用し、ユーザー認証、APIキーの発行・管理、利用状況のトラッキング機能を提供します。

フロントエンド（ランディングページ）は `public/index.html` としてFirebase Hostingでホスティングされており、実際のバックエンドロジックはPythonで記述されたCloud Functionsによって実行されます。

### 主要技術スタック
- **クラウドプラットフォーム:** Google Firebase
- **バックエンド:** Cloud Functions for Firebase (Python)
- **データベース:** Cloud Firestore
- **認証:** Firebase Authentication
- **フロントエンドホスティング:** Firebase Hosting

---

## 2. システムアーキテクチャ

本システムは以下のFirebaseサービスで構成されています。

- **Firebase Hosting (`public` ディレクトリ)**
  - `index.html` (サービスのランディングページ) と `404.html` をホスティングします。
  - すべてのリクエストを `index.html` にリライトする設定になっており、将来的にSPA（Single Page Application）として拡張可能です。

- **Firebase Authentication**
  - ユーザーのサインアップとログインを管理します。
  - `generate_or_fetch_api_key`関数は、Firebase Authが発行するIDトークンを用いてユーザーを安全に認証します。

- **Cloud Functions (`functions` ディレクトリ)**
  - Pythonで記述されたHTTPトリガー関数群です。
  - APIキーの発行、検証、利用回数のカウントアップなど、本システムのコアロジックを担います。

- **Cloud Firestore**
  - アプリケーションの主要なデータを格納するNoSQLデータベースです。
  - `apiKeys`と`processedTransactions`の2つの主要なコレクションを使用します。

- **Firebase Emulators**
  - ローカルでの開発とテストを容易にするためのエミュレータ設定が `firebase.json` に定義されています。Auth, Functions, Firestore, Hostingのエミュレータが含まれます。

---

## 3. データベース設計 (Firestore)

### 3.1. `apiKeys` コレクション

ユーザーに発行されるAPIキーの情報を格納します。

- **ドキュメントID:** Firestoreによる自動生成ID
- **フィールド:**
  - `key` (string): `sk_`で始まるAPIキー文字列。
  - `user_uid` (string): 持ち主であるユーザーのFirebase Authentication UID。
  - `isEnabled` (boolean): キーが有効かどうかのフラグ。
  - `usageCount` (number): 現在の利用回数。毎月リセットされます。
  - `usageLimit` (number): 月間の利用上限回数 (例: 100)。
  - `lastReset` (timestamp): 最後に利用回数がリセットされた日時。
  - `created_at` (timestamp): キーの作成日時。
  - `ownerEmail` (string): 持ち主のメールアドレス。

### 3.2. `processedTransactions` コレクション

`record_api_usage`関数の冪等性（同じリクエストを複数回実行しても結果が同じになること）を保証するために使用されます。

- **ドキュメントID:** 外部システムから提供されるユニークな`transactionId`。
- **フィールド:**
  - `processedAt` (timestamp): 処理日時。
  - `apiKeyIdentifier` (string): 使用されたAPIキーの識別子（一部）。
  - `apiKeyDocId` (string): `apiKeys`コレクションのドキュメントID。
  - `recordedUsageCount` (number): 記録後の`usageCount`。
  - `expiresAt` (timestamp): このドキュメントが自動的に削除される有効期限（TTL）。

### 3.3. セキュリティルール (`firestore.rules`)

- 認証済みユーザーは、自身の`user_uid`に紐づくAPIキー情報のみ読み取り、更新（有効/無効の切り替えのみ）、削除が可能です。
- クライアントからのAPIキーの直接作成は許可されていますが、フィールドや値に厳格な制約がかけられています。
- `processedTransactions`コレクションへのクライアントからの直接アクセスは一切禁止されており、Cloud Functions (Admin SDK) からの操作のみが想定されています。

---

## 4. APIエンドポイント (Cloud Functions)

`functions/main.py`で定義されている主要なHTTP関数です。

### 4.1. `generate_or_fetch_api_key`
ユーザーのAPIキーを取得、または存在しない場合に新規作成します。

- **HTTPメソッド:** `GET`
- **認証:** `Authorization: Bearer <FIREBASE_ID_TOKEN>` ヘッダーが必須。
- **処理:**
  1. IDトークンを検証し、ユーザーを認証します。
  2. ユーザーUIDで`apiKeys`コレクションを検索し、有効なキーを探します。
  3. キーが存在すれば、そのキー文字列を返します (ステータスコード `200`)。
  4. キーが存在しなければ、新しいキーを生成・保存し、そのキー文字列を返します (ステータスコード `201`)。

### 4.2. `check_api_key_status`
APIキーの現在のステータス（有効性、残り利用回数など）を確認します。**利用回数はカウントアップされません。**

- **HTTPメソッド:** `GET`
- **認証:** `X-API-KEY: <YOUR_API_KEY>` ヘッダーが必須。
- **成功レスポンス (JSON):**
  ```json
  {
    "isValid": true,
    "isEnabled": true,
    "usageCount": 10,
    "usageLimit": 100,
    "remainingUsages": 90,
    "isLimitReached": false,
    "lastReset": "2023-10-27T00:00:00Z"
  }
  ```

### 4.3. `record_api_usage`
APIキーの利用を記録し、利用回数を1回インクリメントします。**冪等性が保証されています。**

- **HTTPメソッド:** `POST`
- **認証:** `X-API-KEY: <YOUR_API_KEY>` ヘッダーが必須。
- **リクエストボディ (JSON):**
  ```json
  {
    "transactionId": "unique_transaction_id_for_this_operation"
  }
  ```
- **処理:**
  1. `transactionId`が`processedTransactions`に存在するか確認。存在すれば処理済みとして成功を返します。
  2. APIキーを検証し、利用上限に達していないか確認します。
  3. `apiKeys`の`usageCount`を1増やし、`processedTransactions`に`transactionId`を記録します。これらはアトミックなトランザクション内で実行されます。
- **成功レスポンス (JSON):**
  ```json
  {
      "status": "success",
      "message": "Usage recorded successfully.",
      "newEffectiveUsageCount": 11,
      "remainingUsages": 89,
      "usageLimit": 100
  }
  ```

### 4.4. `verify_api_key`
【旧システム互換用】APIキーを検証し、利用回数を1回インクリメントします。**冪等性はありません。**

- **HTTPメソッド:** `GET` or `POST`
- **認証:** `X-API-KEY: <YOUR_API_KEY>` ヘッダーが必須。
- **注意点:** 呼び出されるたびに利用回数がカウントアップされるため、リトライなどで意図せず複数回カウントされる可能性があります。新規システムでは`record_api_usage`の使用を強く推奨します。

---

## 5. 開発とデプロイ

### 5.1. ローカル開発 (Firebase Emulators)

`firebase.json`にエミュレータ設定が完備されています。以下のコマンドでローカル開発環境を起動します。

```bash
# Firebaseプロジェクトのルートディレクトリで実行
firebase emulators:start
```
- **Emulator UI:** `http://localhost:4000`
- **Hosting:** `http://localhost:5000`
- **Functions:** `http://localhost:5001`
- **Firestore:** `http://localhost:8080`

### 5.2. デプロイ

環境に応じたデプロイコマンドを使用します。

```bash
# プロジェクト全体をデプロイ
firebase deploy

# Cloud Functionsのみをデプロイ
firebase deploy --only functions

# Hostingのみをデプロイ
firebase deploy --only hosting

# Firestoreのルールとインデックスのみをデプロイ
firebase deploy --only firestore
```

---

## 6. API利用サンプル (cURL)

### IDトークンの取得方法
1. ブラウザでホスティングされたページ (`index.html`) を開きます。
2. Firebase Authenticationでログインします。
3. ブラウザの開発者ツールのコンソールで以下を実行します。
   ```javascript
   firebase.auth().currentUser.getIdToken(true).then(idToken => console.log(idToken));
   ```
4. 表示された長い文字列がIDトークンです（有効期限は1時間）。

### cURLコマンド例

```bash
# 自分のAPIキーを取得/生成する
curl -H "Authorization: Bearer <YOUR_ID_TOKEN>" https://generate-or-fetch-api-key-YOUR_CLOUD_RUN_URL.a.run.app

# APIキーのステータスを確認する
curl -H "X-API-KEY: <YOUR_API_KEY>" https://check-api-key-status-YOUR_CLOUD_RUN_URL.a.run.app

# API利用を記録する (冪等性あり)
curl -X POST \
     -H "X-API-KEY: <YOUR_API_KEY>" \
     -H "Content-Type: application/json" \
     -d '{"transactionId": "some-unique-id-12345"}' \
     https://record-api-usage-YOUR_CLOUD_RUN_URL.a.run.app
```
**※注:** `YOUR_CLOUD_RUN_URL` の部分は実際のデプロイ先のURLに置き換えてください。
```