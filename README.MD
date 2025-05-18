# firebase
### デプロイのディレクトリ：

```bash
C:\Users\sinzy\firebase-auth　#ここで以下のどれかでデプロイする
firebase deploy
firebase deploy --only functions
firebase deploy --only functions --debug
```

### ローカルでindex.htmlを使ってテストする：
```bash
C:\Users\sinzy\firebase-auth\public　へ移動
python -m http.server 8088
```
- **APIキー作成はできなくした。**
　
### ブラウザで　localhost:8088 で起動して、
- devtoolのコンソールの一番したに以下を貼り付けて実行
- firebase.auth().currentUser.getIdToken(true).then(idToken => console.log(idToken));
- 長い文字列が表示されたら、それが、テスト用Firebase IDトークン (YOUR_ID_TOKEN):です
- ※これは１時間で期限が切れるらしい

```bash
curl -X GET -H "Authorization: Bearer <YOUR_ID_TOKEN>" https://generate-or-fetch-api-key-.a.run.app
```

### 実行前にAPIKEYチェック＋カウントアップ(古いシステムで採用：Chrome-Record-Scraper)
- curl -H "X-API-KEY: sk_iWs311nVIWv8dEZLIgwO7sM2p35c29Ll" https://verify-api-key-.a.run.app

### 実行前にAPIKEYチェック　カウントはそのまま
- curl -X GET -H "X-API-KEY: sk_iWs311nVIWv8dEZLIgwO7sM2p35c29Ll" https://check-api-key-.a.run.app 

### 実行後にAPIKEYチェック　カウントアップ
- curl -X POST -H "X-API-KEY: sk_iWs311nVIWv8dEZLIgwO7sM2p35c29Ll" -H "Content-Type: application/json" -d '{"transactionId": "unique_transaction_id_001"}' https://record-api-usage-.a.run.app

### 実行後にAPIKEYチェック　２回目ではカウントアップしない
- curl -X POST -H "X-API-KEY: sk_iWs311nVIWv8dEZLIgwO7sM2p35c29Ll" -H "Content-Type: application/json" -d '{"transactionId": "unique_transaction_id_001"}' https://record-api-usage-.a.run.app
