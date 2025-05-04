# main.py
import os
import uuid
import random
import string
from datetime import datetime, timezone
import traceback

# Firebase / Google Cloud ライブラリ
from firebase_functions import https_fn, options
import firebase_admin
from firebase_admin import initialize_app, firestore, auth
# DocumentReference は型ヒントで使用
from google.cloud.firestore_v1.document import DocumentReference
# Firestore関連のエラーを捕捉するために必要
from google.api_core import exceptions as google_exceptions

# --- Admin SDK 初期化 ---
# グローバルスコープでの初期化は一度だけ試みる
try:
    # 引数なしで初期化 (クラウド環境 or ローカルADC/環境変数)
    initialize_app()
    print("Initialized admin SDK.")
    # Firestoreクライアントもここで取得試行
    try:
        db = firestore.client()
        print("Firestore client obtained successfully.")
    except Exception as db_e:
        print(f"Failed to get Firestore client during initial setup: {db_e}")
        db = None
except Exception as init_e:
    print(f"Failed to initialize admin SDK: {init_e}")
    db = None # 初期化失敗時はdbもNoneにする

# --- 全体的なオプション設定 ---
# デプロイするリージョンを設定 (例: 東京)
options.set_global_options(region=options.SupportedRegion.ASIA_NORTHEAST1)


# === ヘルパー関数 ===

def generate_api_key_string_internal(length: int = 32) -> str:
    """
    指定された長さのランダムなAPIキー文字列を生成します。
    'sk_' プレフィックス付き。
    """
    characters = string.ascii_letters + string.digits  # 英数字を使用
    prefix = "sk_"
    # 指定された長さのランダム文字列を生成
    random_string = ''.join(random.choice(characters) for _ in range(length))
    return prefix + random_string


# === Cloud Functions ===

@https_fn.on_request()
def hello_world(req: https_fn.Request) -> https_fn.Response:
    """
    HTTPリクエストに応答する簡単な関数 (動作確認・テスト用)。
    Firestoreへの書き込みテストも含む。
    """
    # Firestoreクライアントが初期化されているかチェック
    if db is None:
        print("Error in hello_world: Firestore client not initialized.")
        return https_fn.Response(
            "Server configuration error: Firestore unavailable.",
            status=500
            )

    print("hello_world: Received request.")

    # Firestoreへの書き込みテスト (関数がDBにアクセスできるか確認)
    try:
        doc_ref = db.collection("test_collection").document("test_doc")
        test_data = {
            "message": "Hello from function!",
            "timestamp": firestore.SERVER_TIMESTAMP
        }
        doc_ref.set(test_data)
        print("hello_world: Firestore write successful (test).")
    except Exception as e:
        # Firestoreへの書き込みが失敗しても、ログには残すが関数自体は止めない
        print(f"hello_world: Firestore write failed: {e}")
        # 本番環境ではここでエラーレスポンスを返すことも検討
        # return https_fn.Response("Error during test write.", status=500)

    # 成功レスポンス
    return https_fn.Response("Hello from Firebase using Python!")


@https_fn.on_request()
def verify_api_key(req: https_fn.Request) -> https_fn.Response:
    """
    APIキーを検証し、利用回数をチェック・カウントアップするHTTP関数。
    """
    # Firestoreクライアントが初期化されているかチェック
    if db is None:
        print("Error in verify_api_key: Firestore client not initialized.")
        return https_fn.Response(
            "Server configuration error: Firestore unavailable.",
            status=500
            )

    print("verify_api_key: Received request.")

    # 1. リクエストヘッダーからAPIキーを取得
    api_key = req.headers.get("X-API-KEY") # ヘッダー名は実際の仕様に合わせる

    # APIキーが存在しない場合
    if not api_key:
        print("verify_api_key: Error - API key not provided in header.")
        return https_fn.Response("Unauthorized: API key missing.", status=401)

    # ログ出力用にキーを短縮 (セキュリティ配慮)
    api_key_short = api_key[:5] + "..." if len(api_key) > 5 else api_key
    print(f"verify_api_key: Attempting to verify key starting with {api_key_short}")

    try:
        # 2. FirestoreでAPIキーを検索
        keys_ref = db.collection("apiKeys")
        # keyフィールドで完全一致、結果は最大1件
        query = keys_ref.where("key", "==", api_key).limit(1)
        # クエリを実行し、結果を取得
        docs = list(query.stream())

        # 3. キーの存在確認
        if not docs:
            # ドキュメントが見つからない場合
            print(f"verify_api_key: Error - API key not found or invalid: {api_key_short}")
            # 存在しないキーなので 403 Forbidden を返す
            return https_fn.Response("Unauthorized: Invalid API key.", status=403)

        # キーが見つかった場合、最初のドキュメントを使用
        key_doc_snapshot = docs[0]
        key_doc_ref: DocumentReference = key_doc_snapshot.reference
        key_data: dict = key_doc_snapshot.to_dict()
        doc_id = key_doc_snapshot.id

        print(f"verify_api_key: Found key document {doc_id} for {api_key_short}")

        # 4. キーの有効性 (isEnabled) チェック
        # isEnabledフィールドが存在しない、またはFalseの場合は無効とみなす
        if not key_data.get("isEnabled", False):
            print(f"verify_api_key: Error - API key is disabled: {api_key_short} (Doc ID: {doc_id})")
            return https_fn.Response("Unauthorized: API key disabled.", status=403)

        # 5. 利用回数チェックと更新 (トランザクション処理)
        # トランザクション内で実行される関数を定義
        @firestore.transactional
        def check_and_update_usage_transaction(
            transaction, # トランザクションオブジェクト (型ヒントは不要)
            doc_ref: DocumentReference
        ) -> bool | None: # 成功時はTrue, 上限超過はNone
            """トランザクション内で利用状況を確認し、更新する (アトミック処理)"""
            try:
                # トランザクション内で最新のドキュメントデータを取得
                snapshot = doc_ref.get(transaction=transaction)

                # ドキュメントが存在しない場合 (トランザクション中に削除されたなど)
                if not snapshot.exists:
                    print(f"verify_api_key: Error in transaction - Doc {doc_ref.id} deleted during transaction.")
                    # 上限超過と同様にNoneを返して処理を中断
                    return None

                current_data = snapshot.to_dict()

                # 各フィールドを取得 (存在しない場合のデフォルト値も設定)
                usage_count: int = current_data.get("usageCount", 0)
                usage_limit: int = current_data.get("usageLimit", 100) # デフォルト100回
                last_reset_timestamp: datetime | None = current_data.get("lastReset")

                needs_reset = False
                now_utc = datetime.now(timezone.utc)

                # 最終リセット日時が存在する場合、月が変わったかチェック
                if last_reset_timestamp:
                    # Firestore TimestampはUTCとして扱う
                    last_reset_dt_utc = last_reset_timestamp.replace(tzinfo=timezone.utc)
                    if (last_reset_dt_utc.year < now_utc.year or
                            last_reset_dt_utc.month < now_utc.month):
                        needs_reset = True

                # 月が変わっていた場合、カウンターをリセット
                if needs_reset:
                    print(f"verify_api_key: Resetting usage count for key {api_key_short} (Doc ID: {snapshot.id})")
                    usage_count = 0 # カウントをリセット
                    reset_update_data = {
                        "usageCount": 0,
                        "lastReset": firestore.SERVER_TIMESTAMP # サーバー時刻で更新
                    }
                    transaction.update(doc_ref, reset_update_data)
                    print("verify_api_key: Usage count reset in transaction.")

                # 上限チェック (リセット後のカウントで比較)
                if usage_count >= usage_limit:
                    print(f"verify_api_key: Error - Usage limit exceeded for key {api_key_short}. "
                          f"Count: {usage_count}, Limit: {usage_limit}")
                    # 上限超過を示す None を返す
                    return None

                # 上限未満の場合、カウントをインクリメント
                print(f"verify_api_key: Incrementing usage count for key {api_key_short}. "
                      f"Previous: {usage_count}")
                increment_update_data = {
                    # firestore.Increment でアトミックに+1する
                    "usageCount": firestore.Increment(1)
                }
                transaction.update(doc_ref, increment_update_data)
                print("verify_api_key: Usage count incremented in transaction.")

                # 利用許可を示す True を返す
                return True

            except Exception as trans_error:
                # トランザクション内での予期せぬエラー
                print(f"verify_api_key: Error inside usage check transaction for {doc_ref.id}: {trans_error}")
                # エラーを再送出してトランザクションを失敗させる
                raise trans_error

        # --- トランザクションの実行 ---
        try:
            # トランザクションオブジェクトを作成
            transaction_obj = db.transaction()
            # 定義したトランザクション関数を実行
            update_result = check_and_update_usage_transaction(transaction_obj, key_doc_ref)
        except Exception as transaction_execution_error:
             # トランザクション自体の実行時エラー (内部でraiseされたエラー含む)
             print(f"verify_api_key: Transaction execution failed for key {api_key_short}: {transaction_execution_error}")
             traceback.print_exc()
             return https_fn.Response("Server error during usage update.", status=500)

        # 6. トランザクション結果に基づきレスポンスを返す
        if update_result is True:
            # 正常に利用回数がインクリメントされた場合
            owner_uid = key_data.get("user_uid", "unknown")
            print(f"verify_api_key: Success for key {api_key_short}. Owner UID: {owner_uid}")

            # ★★★ 本来のAPI処理をここに追加 ★★★
            # 例: データベース検索、計算、外部API呼び出しなど
            # api_result = perform_actual_api_work(key_data, req) # 仮の関数呼び出し

            # 仮の成功レスポンス
            return https_fn.Response(f"API key verified successfully for user {owner_uid}!")

        elif update_result is None:
            # 上限に達していた場合
            # ステータスコード 429 Too Many Requests がより適切
            return https_fn.Response("Forbidden: Usage limit exceeded.", status=429)
        else:
            # トランザクション関数が予期せず False などを返した場合 (通常は起こらないはず)
            print(f"verify_api_key: Unexpected result ({update_result}) from transaction for key {api_key_short}.")
            return https_fn.Response("Server error: Unexpected transaction result.", status=500)

    # 7. 関数全体の例外処理
    except google_exceptions.NotFound as e:
        # Firestoreの検索自体で問題があった場合など (例: コレクションが存在しない - 通常考えにくい)
        print(f"verify_api_key: Firestore NotFound Error during query: {e}")
        # 403を返すのが適切か、あるいは500か要検討
        return https_fn.Response("Unauthorized: Invalid API key.", status=403)
    except google_exceptions.PermissionDenied as e:
        # Firestoreへのアクセス権限がない場合 (Admin SDKでは通常考えにくい)
        print(f"verify_api_key: Firestore Permission Denied Error: {e}")
        return https_fn.Response("Server configuration error (permissions).", status=500)
    except Exception as e:
        # その他の予期せぬエラー全般
        print(f"verify_api_key: An unexpected critical error occurred: {e}")
        traceback.print_exc() # 完全なスタックトレースを出力
        return https_fn.Response("Internal Server Error", status=500)


@https_fn.on_request()
def generate_or_fetch_api_key(req: https_fn.Request) -> https_fn.Response:
    """
    IDトークンでユーザーを認証し、有効なAPIキーを返す関数。
    キーが存在しない場合は新しく生成して保存してから返す。
    """
    # Firestoreクライアントが初期化されているかチェック
    if db is None:
        print("Error in generate_or_fetch_api_key: Firestore client not initialized.")
        return https_fn.Response("Server configuration error.", status=500)

    print("generate_or_fetch_api_key: Received request.")

    # 1. リクエストヘッダーからIDトークンを取得
    auth_header = req.headers.get("Authorization")
    id_token = None
    # "Bearer " スキーマを確認
    if auth_header and auth_header.startswith("Bearer "):
        id_token = auth_header.split("Bearer ", 1)[1] # 1回だけ分割

    # IDトークンがない場合
    if not id_token:
        print("generate_or_fetch_api_key: Error - Authorization header missing or invalid format.")
        # 401 Unauthorized を返す
        return https_fn.Response("Unauthorized: Missing or invalid token.", status=401)

    try:
        # 2. IDトークンを検証
        try:
            decoded_token = auth.verify_id_token(id_token)
        except (auth.InvalidIdTokenError, auth.ExpiredIdTokenError, ValueError) as auth_error:
            # トークン検証失敗
            print(f"generate_or_fetch_api_key: Error - Token verification failed: {auth_error}")
            # 401 Unauthorized を返す
            return https_fn.Response(f"Unauthorized: {auth_error}", status=401)

        # トークンからUIDとEmailを取得
        uid = decoded_token.get('uid')
        email = decoded_token.get('email', '') # Emailは存在しない場合もある

        # UIDが取得できない場合 (通常ありえないが念のため)
        if not uid:
             print("generate_or_fetch_api_key: Error - UID not found in valid token.")
             return https_fn.Response("Unauthorized: Invalid token claims.", status=401)

        print(f"generate_or_fetch_api_key: Verified user: UID={uid}, Email={email}")

        # 3. Firestoreで有効なAPIキーを検索
        keys_ref = db.collection("apiKeys")
        # ユーザーUIDと有効フラグで検索
        query = keys_ref.where("user_uid", "==", uid).where("isEnabled", "==", True).limit(1)
        docs = list(query.stream())

        # 4. キーの存在に応じて処理を分岐
        if docs:
            # 4a. 有効なキーが存在する場合
            existing_key_data = docs[0].to_dict()
            api_key = existing_key_data.get("key")
            doc_id = docs[0].id

            # キー文字列が取得できないデータ不整合の場合
            if not api_key:
                print(f"generate_or_fetch_api_key: Data error - Doc {doc_id} for user {uid} missing 'key' field.")
                return https_fn.Response("Internal Server Error: Data inconsistency.", status=500)

            api_key_short = api_key[:5] + "..." if len(api_key) > 5 else api_key
            print(f"generate_or_fetch_api_key: Found existing active API key for user {uid}: {api_key_short}")

            # TODO: 将来的にキーの有効期限をチェックし、必要なら更新するロジックを追加可能

            # 既存のキーを返す (ステータスコード 200 OK)
            return https_fn.Response(api_key, status=200, content_type="text/plain")
        else:
            # 4b. 有効なキーが存在しない場合 -> 新規生成
            print(f"generate_or_fetch_api_key: No active API key found for user {uid}. Generating.")

            # 新しいAPIキー文字列を生成
            api_key = generate_api_key_string_internal()
            api_key_short = api_key[:5] + "..."

            # Firestoreに保存するデータを作成
            timestamp = firestore.SERVER_TIMESTAMP # サーバー側のタイムスタンプを使用
            new_key_data = {
                "key": api_key,
                "user_uid": uid,
                "isEnabled": True,
                "usageCount": 0,        # 初期カウント
                "usageLimit": 100,      # デフォルト上限
                "lastReset": timestamp, # 作成時をリセット時とする
                "created_at": timestamp,# 作成日時
                "ownerEmail": email if email else "" # Emailがあれば保存
                # "expires_at": firestore.SERVER_TIMESTAMP + timedelta(days=7) # 例:有効期限
            }

            # Firestoreに新しいドキュメントを追加 (IDは自動生成)
            new_doc_ref = keys_ref.document()
            try:
                new_doc_ref.set(new_key_data)
                print(f"generate_or_fetch_api_key: Saved new API key for user {uid}: {api_key_short} (Doc ID: {new_doc_ref.id})")
                # 生成した新しいキーを返す (ステータスコード 201 Created)
                return https_fn.Response(api_key, status=201, content_type="text/plain")
            except Exception as db_write_error:
                 # Firestoreへの書き込み失敗
                 print(f"generate_or_fetch_api_key: Error saving new key to Firestore for user {uid}: {db_write_error}")
                 traceback.print_exc()
                 return https_fn.Response("Internal Server Error: Could not save API key.", status=500)

    # 5. 関数全体の例外処理
    except Exception as e:
        # その他の予期せぬエラー
        print(f"generate_or_fetch_api_key: An unexpected critical error occurred: {e}")
        traceback.print_exc()
        return https_fn.Response("Internal Server Error", status=500)